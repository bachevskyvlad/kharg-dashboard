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

DEBT: SUPABASE_KEY is hardcoded as plaintext in the workflow. The cron pipeline
uses the same key to write to raw_observations — i.e. a write-capable key is
in the repo. After P1 ships: move to GHA secret SUPABASE_KEY and rotate. — K to decide when.
"""

import csv, gzip, io, os, sys, datetime, requests, subprocess
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

# ---- Supabase REST helper --------------------------------------------------

def sb_fetch_all(table, select="*", filters=None):
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
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}",
                         headers=headers, params=params, verify=CA, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


# ---- CDSE catalog -----------------------------------------------------------

TERMINAL_BBOX = {
    "kharg":      "POLYGON((49.8 29.0,50.3 29.0,50.3 29.5,49.8 29.5,49.8 29.0))",
    "ras_tanura": "POLYGON((49.5 26.0,51.0 26.0,51.0 27.5,49.5 27.5,49.5 26.0))",
    "yanbu":      "POLYGON((38.1 23.8,38.4 23.8,38.4 24.1,38.1 24.1,38.1 23.8))",
}

def cdse_query(bbox_wkt, d0, d1):
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
                if "GRDH" not in attrs.get("productType", ""):
                    continue
                start = item.get("ContentDate", {}).get("Start", "")
                rows.append({
                    "scene_id":     item.get("Name", ""),
                    "datetime_utc": start,
                    "date":         start[:10] if start else "",
                    "platform":     attrs.get("platformShortName", "") +
                                    attrs.get("platformSerialIdentifier", ""),
                    "relOrbit":     str(attrs.get("relativeOrbitNumber", "")),
                    "direction":    attrs.get("orbitDirection", ""),
                    "incidence":    str(attrs.get("incidenceAngleCenter",
                                    attrs.get("incidenceAngleMidSwath", ""))),
                })
            if len(items) < 100:
                break
            skip += 100
        except Exception as e:
            print(f"  [WARN] CDSE query {d0}→{d1}: {e}", flush=True)
            break
    return rows


def build_cdse_index(terminal, dates):
    """Fetch CDSE catalog. Returns dict keyed by (date, relOrbit, direction).

    Multiple scenes per date are preserved — RT has two tracks (DESC-35 and ASC-101)
    that can both fall on the same calendar date.
    """
    if not dates:
        return {}
    dates = sorted(dates)
    d0 = (datetime.date.fromisoformat(dates[0]) - datetime.timedelta(days=1)).isoformat()
    d1 = (datetime.date.fromisoformat(dates[-1]) + datetime.timedelta(days=1)).isoformat()
    bbox = TERMINAL_BBOX[terminal]
    print(f"  CDSE {terminal}: querying {d0}→{d1} ({len(dates)} dates)…", flush=True)

    all_scenes = []
    for y in range(int(d0[:4]), int(d1[:4]) + 1):
        y0 = max(d0, f"{y}-01-01")
        y1 = min(d1, f"{y}-12-31")
        scenes = cdse_query(bbox, y0, y1)
        all_scenes.extend(scenes)
        print(f"    {y}: {len(scenes)} scenes", flush=True)

    # Key: (date, relOrbit, direction) — keeps all distinct orbit tracks per date
    idx = {}
    for s in all_scenes:
        k = (s["date"], s["relOrbit"], s["direction"])
        if k not in idx:
            idx[k] = s
    print(f"  CDSE {terminal}: {len(idx)} (date,orbit,dir) keys", flush=True)
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
    """Load tank_scene_variants_*.csv.gz only (not observations_all.csv.gz).

    Returns dict keyed by (terminal, tank_id, date, relOrbit, direction) — exact join only.
    No fallback: wrong orbit is worse than empty.
    """
    r = subprocess.run(["git", "show", f"{BRANCH}:{MEGA}/"], capture_output=True)
    lines = r.stdout.decode().splitlines()
    part_files = [l for l in lines
                  if l.endswith(".csv.gz") and l.startswith("tank_scene_variants_")]

    idx = {}
    for fname in sorted(part_files):
        rows = git_show_gz(f"{MEGA}/{fname}")
        if not rows:
            print(f"  [SKIP] {fname}", flush=True)
            continue
        n = 0
        for row in rows:
            key = (row.get("terminal",""), row.get("tank_id",""),
                   row.get("date",""), str(row.get("relOrbit","")),
                   row.get("direction",""))
            if key not in idx:
                idx[key] = row
                n += 1
        print(f"  s4: {fname} → {n} new keys", flush=True)
    print(f"  s4 index: {len(idx)} exact keys", flush=True)
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
    print(f"Run: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M UTC')}", flush=True)

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
        cdse_idx = cdse_by_terminal[terminal]

        # Group DB rows by (tank_id, date) — one row per tank per date from DB
        for db_row in db_rows:
            date    = str(db_row.get("date", ""))[:10]
            tank_id = db_row.get("tank_id", "")
            if not date or not tank_id:
                continue

            # Find all orbits that covered this terminal on this date
            orbits_on_date = [(rel_orb, direction, sc)
                              for (d, rel_orb, direction), sc in cdse_idx.items()
                              if d == date]

            if not orbits_on_date:
                # No CDSE scene for this date — still emit the row with empty scene fields
                orbits_on_date = [("", "", {})]

            for rel_orb, direction, sc in orbits_on_date:
                key = (terminal, tank_id, date, rel_orb, direction)
                if key in seen:
                    continue
                seen.add(key)

                platform     = sc.get("platform", "")
                incidence    = sc.get("incidence", "")
                scene_id     = sc.get("scene_id", "")
                datetime_utc = sc.get("datetime_utc", date + "T00:00:00Z")

                r15 = ring15_idx.get(key, {})
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
                    "ring15_far":             str(r15.get("ring15_far", "")),
                    "ring15_near":            str(r15.get("ring15_near", "")),
                    "pixel_valid_frac":       str(r15.get("pixel_valid_frac", "")),
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

    print("\n=== 6. Quality report per (terminal, relOrbit) ===", flush=True)
    stats = defaultdict(lambda: {"total":0, "r15_nn":0, "plat_nn":0, "inc_nn":0})
    for row in out_rows:
        k = (row["terminal_id"], row["relative_orbit"], row["orbit_direction"])
        s = stats[k]
        s["total"] += 1
        if row["ring15_far"] not in ("", "0.0", "0"):
            s["r15_nn"] += 1
        if row["platform"] not in ("", "None"):
            s["plat_nn"] += 1
        if row["incidence_angle_center"] not in ("", "None"):
            s["inc_nn"] += 1

    any_zero_r15 = False
    for (tid, orb, direction), s in sorted(stats.items()):
        t = s["total"]
        r15_f  = s["r15_nn"]  / t if t else 0
        plat_f = s["plat_nn"] / t if t else 0
        inc_f  = s["inc_nn"]  / t if t else 0
        zero_flag = " ← ZERO" if r15_f == 0 and t > 0 else ""
        print(
            f"  {tid} orb={orb} {direction}: "
            f"ring15_far {s['r15_nn']}/{t} ({r15_f:.3f}){zero_flag}  "
            f"platform {s['plat_nn']}/{t} ({plat_f:.3f})  "
            f"incidence {s['inc_nn']}/{t} ({inc_f:.3f})",
            flush=True,
        )
        if r15_f == 0 and t > 0:
            any_zero_r15 = True

    if any_zero_r15:
        print("\n[WARN] Some terminal+orbit has zero ring15_far — s4 not yet run for those orbits.",
              flush=True)

    print(f"\n=== 7. Writing {OUT_PATH} ({len(out_rows)} rows) ===", flush=True)
    with gzip.open(OUT_PATH, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTCOLS)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    total  = len(out_rows)
    r15_nn = sum(1 for x in out_rows if x["ring15_far"] not in ("", "0.0", "0"))
    print(f"\nDONE: {total} rows, ring15_far non-null overall = {r15_nn}/{total} "
          f"({r15_nn/max(total,1):.3f})", flush=True)
    by_term = defaultdict(int)
    for r in out_rows:
        by_term[r["terminal_id"]] += 1
    print(f"Rows per terminal: {dict(by_term)}", flush=True)
    print(f"Output: {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
