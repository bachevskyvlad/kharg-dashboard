"""
p1_obs_rebuild.py — Rebuild observations_all.csv.gz from prod-DB.

Sources:
  PRIMARY: raw_observations (kharg) + terminal_observations (RT, Yanbu) from Supabase
  RING15:  diagnostics/mega/tank_scene_variants_*.csv.gz (section 4 part files)
  SCENE:   CDSE OData catalog (platform, relOrbit, direction, incidence, datetime_utc)
  REG:     diagnostics/mega/registry_full.csv

Output columns:
  terminal_id, tank_id, date, datetime_utc, scene_id,
  platform, relative_orbit, orbit_direction,
  incidence_angle_center, los_azimuth_used,
  db_mean_prod, ring15_far, ring15_near, pixel_valid_frac,
  lat, lon, radius_m, h_max_m, enabled, in_whitelist,
  ingest_version, source_table

RULE: print ring15_far non-null fraction PER terminal PER orbit before committing.
File with zero fraction for any terminal+orbit must NOT be committed.
"""

import csv, gzip, io, json, os, sys, datetime, requests, subprocess
from pathlib import Path
from collections import defaultdict

BRANCH   = "origin/research/2026-10-auto"
MEGA     = "diagnostics/mega"
OUT_DIR  = Path("diagnostics/mega")
OUT_PATH = OUT_DIR / "observations_all.csv.gz"

SUPABASE_URL = os.environ["SUPABASE_URL"]   # FATAL if missing
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
CA    = os.environ.get("REQUESTS_CA_BUNDLE") or True

OUTCOLS = [
    "terminal_id","tank_id","date","datetime_utc","scene_id",
    "platform","relative_orbit","orbit_direction",
    "incidence_angle_center","los_azimuth_used",
    "db_mean_prod","ring15_far","ring15_near","pixel_valid_frac",
    "lat","lon","radius_m","h_max_m","enabled","in_whitelist",
    "ingest_version","source_table",
]

# ---- Supabase REST helper (no supabase-py needed) --------------------------

def sb_fetch_all(table, select="*", filters=None):
    """Paginate Supabase REST. Returns list of dicts."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Range-Unit": "items",
    }
    rows = []
    offset = 0
    PAGE = 1000
    while True:
        params = {"select": select, "limit": PAGE, "offset": offset}
        if filters:
            for k, v in filters.items():
                params[f"{k}"] = f"eq.{v}"
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        r = requests.get(url, headers=headers, params=params,
                         verify=CA, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


# ---- CDSE catalog per terminal (bbox, date range) --------------------------

TERMINAL_BBOX = {
    "kharg":      "POLYGON((49.8 29.0,50.3 29.0,50.3 29.5,49.8 29.5,49.8 29.0))",
    "ras_tanura": "POLYGON((49.5 26.0,51.0 26.0,51.0 27.5,49.5 27.5,49.5 26.0))",
    "yanbu":      "POLYGON((38.1 23.8,38.4 23.8,38.4 24.1,38.1 24.1,38.1 23.8))",
}

def cdse_query(bbox_wkt, d0, d1):
    """Query CDSE OData for GRDH scenes in bbox and date range. Returns list of dicts."""
    filt = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and ContentDate/Start gt {d0}T00:00:00.000Z "
        f"and ContentDate/Start lt {d1}T23:59:59.000Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_wkt}')"
    )
    rows = []
    skip = 0
    while True:
        try:
            r = requests.get(ODATA, params={
                "$filter": filt, "$top": 100, "$skip": skip,
                "$expand": "Attributes",
                "$orderby": "ContentDate/Start asc",
            }, verify=CA, timeout=30)
            r.raise_for_status()
            items = r.json().get("value", [])
            for item in items:
                attrs = {a["Name"]: a.get("Value") for a in item.get("Attributes", [])}
                pt = attrs.get("productType", "")
                if "GRDH" not in pt:
                    continue
                start = item.get("ContentDate", {}).get("Start", "")
                rows.append({
                    "scene_id":    item.get("Name", ""),
                    "datetime_utc": start,
                    "date":        start[:10] if start else "",
                    "platform":    attrs.get("platformShortName", "") +
                                   attrs.get("platformSerialIdentifier", ""),
                    "relOrbit":    str(attrs.get("relativeOrbitNumber", "")),
                    "direction":   attrs.get("orbitDirection", ""),
                    "incidence":   str(attrs.get("incidenceAngleCenter",
                                   attrs.get("incidenceAngleMidSwath", ""))),
                })
            if len(items) < 100:
                break
            skip += 100
        except Exception as e:
            print(f"  [WARN] CDSE query {d0}→{d1}: {e}")
            break
    return rows


def build_cdse_index(terminal, dates):
    """Fetch CDSE catalog for all dates of a terminal. Returns dict keyed by date."""
    if not dates:
        return {}
    dates = sorted(dates)
    d_min = dates[0]
    d_max = dates[-1]
    d0 = (datetime.date.fromisoformat(d_min) - datetime.timedelta(days=1)).isoformat()
    d1 = (datetime.date.fromisoformat(d_max) + datetime.timedelta(days=1)).isoformat()
    bbox = TERMINAL_BBOX[terminal]
    print(f"  CDSE {terminal}: querying {d0}→{d1} ({len(dates)} dates)…", flush=True)

    start_y = int(d0[:4])
    end_y   = int(d1[:4])
    all_scenes = []
    for y in range(start_y, end_y + 1):
        y0 = max(d0, f"{y}-01-01")
        y1 = min(d1, f"{y}-12-31")
        scenes = cdse_query(bbox, y0, y1)
        all_scenes.extend(scenes)
        print(f"    {y}: {len(scenes)} scenes", flush=True)

    idx = {}
    for s in all_scenes:
        d = s["date"]
        if d and d not in idx:
            idx[d] = s
    print(f"  CDSE {terminal}: {len(idx)} unique dates covered", flush=True)
    return idx


# ---- Load section-4 part files from git branch ----------------------------

def git_show_gz(path):
    r = subprocess.run(["git", "show", f"{BRANCH}:{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    return list(csv.DictReader(io.TextIOWrapper(gzip.open(io.BytesIO(r.stdout)))))


def git_show_text(path):
    r = subprocess.run(["git", "show", f"{BRANCH}:{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def load_ring15_index():
    """Load all section-4 part files. Returns dict (terminal,tank_id,date,relOrbit,direction)->row."""
    r = subprocess.run(["git", "show", f"{BRANCH}:{MEGA}/"], capture_output=True)
    lines = r.stdout.decode().splitlines()
    part_files = [l for l in lines if l.endswith(".csv.gz")]

    idx = {}
    for fname in sorted(part_files):
        rows = git_show_gz(f"{MEGA}/{fname}")
        if not rows:
            print(f"  [SKIP] {fname}", flush=True)
            continue
        n = 0
        for row in rows:
            terminal  = row.get("terminal", "")
            tank_id   = row.get("tank_id", "")
            date      = row.get("date", "")
            rel_orb   = str(row.get("relOrbit", ""))
            direction = row.get("direction", "")
            key = (terminal, tank_id, date, rel_orb, direction)
            if key not in idx:
                idx[key] = row
                n += 1
        print(f"  s4: {fname} → {n} new keys", flush=True)
    print(f"  s4 index: {len(idx)} total unique keys", flush=True)
    return idx


def load_registry():
    raw = git_show_text(f"{MEGA}/registry_full.csv")
    if not raw:
        return {}
    reg = {}
    for row in csv.DictReader(io.StringIO(raw)):
        reg[(row["terminal_id"], row["tank_id"])] = row
    return reg


# ---- Main ------------------------------------------------------------------

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    print(f"Run: {ts}", flush=True)

    print("\n=== 1. Registry ===", flush=True)
    registry = load_registry()
    print(f"  {len(registry)} tank entries", flush=True)

    print("\n=== 2. Section-4 ring15 index ===", flush=True)
    ring15_idx = load_ring15_index()

    print("\n=== 3. Fetch DB rows ===", flush=True)
    db_rows_by_terminal = {}
    for terminal, table in [
        ("kharg",      "raw_observations"),
        ("ras_tanura", "terminal_observations"),
        ("yanbu",      "terminal_observations"),
    ]:
        print(f"  {terminal} ({table})…", flush=True)
        try:
            # raw_observations is kharg-only (no terminal_id column)
            filt = None if table == "raw_observations" else {"terminal_id": terminal}
            rows = sb_fetch_all(table, select="*", filters=filt)
            db_rows_by_terminal[terminal] = rows
            print(f"    {len(rows)} rows", flush=True)
        except Exception as e:
            print(f"  [FATAL] {terminal}: {e}", flush=True)
            sys.exit(1)

    print("\n=== 4. CDSE catalog ===", flush=True)
    cdse_by_terminal = {}
    for terminal, rows in db_rows_by_terminal.items():
        dates = sorted(set(r["date"][:10] for r in rows if r.get("date")))
        cdse_by_terminal[terminal] = build_cdse_index(terminal, dates)

    print("\n=== 5. Assembling output ===", flush=True)
    out_rows = []
    seen = set()

    for terminal, db_rows in db_rows_by_terminal.items():
        cdse_idx  = cdse_by_terminal[terminal]

        for db_row in db_rows:
            date    = str(db_row.get("date", ""))[:10]
            tank_id = db_row.get("tank_id", "")
            if not date or not tank_id:
                continue

            sc = cdse_idx.get(date, {})
            rel_orb      = sc.get("relOrbit", "")
            direction    = sc.get("direction", "")
            platform     = sc.get("platform", "")
            incidence    = sc.get("incidence", "")
            scene_id     = sc.get("scene_id", "")
            datetime_utc = sc.get("datetime_utc", date + "T00:00:00Z")

            key = (terminal, tank_id, date, rel_orb, direction)
            if key in seen:
                continue
            seen.add(key)

            r15 = ring15_idx.get(key, {})
            ring15_far       = r15.get("ring15_far", "")
            ring15_near      = r15.get("ring15_near", "")
            pixel_valid_frac = r15.get("pixel_valid_frac", "")

            reg = registry.get((terminal, tank_id), {})
            db_mean = (db_row.get("db_mean") or db_row.get("ring15_far") or "")

            out_rows.append({
                "terminal_id":            terminal,
                "tank_id":                tank_id,
                "date":                   date,
                "datetime_utc":           datetime_utc,
                "scene_id":               scene_id,
                "platform":               platform,
                "relative_orbit":         rel_orb,
                "orbit_direction":        direction,
                "incidence_angle_center": incidence,
                "los_azimuth_used":       reg.get("los_azimuth", ""),
                "db_mean_prod":           str(db_mean),
                "ring15_far":             str(ring15_far),
                "ring15_near":            str(ring15_near),
                "pixel_valid_frac":       str(pixel_valid_frac),
                "lat":                    reg.get("lat", ""),
                "lon":                    reg.get("lon", ""),
                "radius_m":               reg.get("radius_m", ""),
                "h_max_m":                reg.get("h_max_m", ""),
                "enabled":                reg.get("enabled", ""),
                "in_whitelist":           "",
                "ingest_version":         "v5",
                "source_table":           "raw_observations" if terminal == "kharg"
                                          else "terminal_observations",
            })

    print("\n=== 6. ring15_far quality report ===", flush=True)
    stats = defaultdict(lambda: [0, 0])
    for row in out_rows:
        k = (row["terminal_id"], row["relative_orbit"], row["orbit_direction"])
        stats[k][0] += 1
        if row["ring15_far"] not in ("", "0.0", "0"):
            stats[k][1] += 1

    any_zero = False
    for (tid, orb, direction), (total, nn) in sorted(stats.items()):
        frac = nn / total if total else 0
        flag = " ← ZERO" if frac == 0 else ""
        print(f"  {tid} orb={orb} {direction}: {nn}/{total} ring15_far non-null "
              f"({frac:.3f}){flag}", flush=True)
        if frac == 0 and total > 0:
            any_zero = True

    if any_zero:
        print("\n[WARN] Some terminal+orbit has zero ring15_far — s4 not yet run for those orbits.", flush=True)

    print(f"\n=== 7. Writing {OUT_PATH} ({len(out_rows)} rows) ===", flush=True)
    with gzip.open(OUT_PATH, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTCOLS)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    total = len(out_rows)
    r15_nn = sum(1 for x in out_rows if x.get("ring15_far") not in ("", "0.0", "0"))
    print(f"\nDONE: {total} rows, ring15_far non-null overall = {r15_nn}/{total} "
          f"({r15_nn/max(total,1):.3f})", flush=True)

    by_term = defaultdict(int)
    for r in out_rows:
        by_term[r["terminal_id"]] += 1
    print(f"Rows per terminal: {dict(by_term)}", flush=True)
    print(f"Output: {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
