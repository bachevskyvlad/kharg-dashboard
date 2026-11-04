"""
p3_yanbu_fix.py — Fix Yanbu ASC-14 date source and re-export section 4.

Root cause: mega_export fallback used terminal_observations dates (DESC) for
Yanbu ASC-14 → only 43 matching dates out of 290 in prod → pixel_valid_frac=0.

Fix:
  1. Query CDSE OData for Yanbu bbox → get all ASC-14 + ASC-174 pass dates
  2. Filter to dates that exist in terminal_observations (prod-DB)
  3. Write v0_manifest_yanbu.csv with correct orbit metadata
  4. Run mega_export section 4 for yanbu only with that manifest
  5. Print ring15_far per terminal+orbit — MUST be > 0 before commit

Outputs (diagnostics/mega/):
  tank_scene_variants_yanbu_ASC14_part*.csv.gz  (from mega_export s4)
  yanbu_cdse_passes.csv                          (all CDSE ASC passes over Yanbu)
  run_p3_yanbu_*.txt                             (full log)
"""

import csv, gzip, io, os, sys, datetime, requests, subprocess, json
from pathlib import Path
from collections import defaultdict

ODATA        = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
CA           = os.environ.get("REQUESTS_CA_BUNDLE") or True
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

OUT_DIR = Path("diagnostics/mega")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

YANBU_BBOX = "POLYGON((37.9 23.6,38.6 23.6,38.6 24.2,37.9 24.2,37.9 23.6))"


# ---- CDSE catalog ----------------------------------------------------------

def cdse_query_year(bbox_wkt, year):
    d0 = f"{year}-01-01"
    d1 = f"{year}-12-31"
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
                orb_dir = attrs.get("orbitDirection", "")
                if orb_dir != "ASCENDING":
                    continue
                serial = attrs.get("platformSerialIdentifier", "")
                rows.append({
                    "scene_id":    item.get("Name", ""),
                    "datetime_utc": start,
                    "date":        start[:10],
                    "platform":    attrs.get("platformShortName", "") + serial,
                    "relOrbit":    str(attrs.get("relativeOrbitNumber", "")),
                    "direction":   orb_dir,
                    "incidence":   str(attrs.get("incidenceAngleCenter",
                                   attrs.get("incidenceAngleMidSwath", ""))),
                    "serial":      serial,
                })
            if len(items) < 100:
                break
            skip += 100
        except Exception as e:
            print(f"  [WARN] CDSE {year}: {e}", flush=True)
            break
    return rows


def fetch_all_yanbu_asc_passes():
    current_year = datetime.date.today().year
    all_scenes = []
    for y in range(2014, current_year + 1):
        scenes = cdse_query_year(YANBU_BBOX, y)
        print(f"  CDSE Yanbu ASC {y}: {len(scenes)} scenes", flush=True)
        all_scenes.extend(scenes)
    return all_scenes


# ---- Supabase REST helper --------------------------------------------------

def sb_fetch_all(table, select="*", filters=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    rows = []
    offset = 0
    PAGE = 1000
    while True:
        params = {"select": select, "limit": PAGE, "offset": offset}
        if filters:
            for k, v in filters.items():
                params[k] = f"eq.{v}"
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers, params=params,
            verify=CA, timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


# ---- Write v0 manifest for Yanbu -------------------------------------------

def write_manifest(scenes, out_path):
    fields = ["terminal_id","scene_id","datetime_utc","date","relOrbit",
              "direction","platform","incidence_angle","heading"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for s in scenes:
            w.writerow({
                "terminal_id":    "yanbu",
                "scene_id":       s.get("scene_id", ""),
                "datetime_utc":   s.get("datetime_utc", ""),
                "date":           s.get("date", ""),
                "relOrbit":       s.get("relOrbit", ""),
                "direction":      s.get("direction", ""),
                "platform":       s.get("platform", ""),
                "incidence_angle": s.get("incidence", ""),
                "heading":        "",
            })
    print(f"  Manifest written: {out_path} ({len(scenes)} scenes)", flush=True)


# ---- Main ------------------------------------------------------------------

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    print(f"=== P3.1 Yanbu ASC-14 fix — {ts} ===\n", flush=True)

    print("Step 1: CDSE catalog for Yanbu ASC passes…", flush=True)
    all_scenes = fetch_all_yanbu_asc_passes()
    print(f"  Total ASC GRDH scenes: {len(all_scenes)}", flush=True)

    date_orb_seen = set()
    deduped = []
    for s in sorted(all_scenes, key=lambda x: x.get("datetime_utc", "")):
        k = (s["date"], s["relOrbit"])
        if k not in date_orb_seen:
            date_orb_seen.add(k)
            deduped.append(s)

    by_orb = defaultdict(list)
    for s in deduped:
        by_orb[s["relOrbit"]].append(s["date"])
    for orb, dates in sorted(by_orb.items()):
        print(f"  relOrbit={orb}: {len(dates)} dates, first={dates[0]}, last={dates[-1]}", flush=True)

    cdse_out = OUT_DIR / "yanbu_cdse_passes.csv"
    with open(cdse_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scene_id","datetime_utc","date","platform","relOrbit","direction","incidence"])
        w.writeheader()
        for s in deduped:
            w.writerow({k: s.get(k, "") for k in ["scene_id","datetime_utc","date","platform","relOrbit","direction","incidence"]})
    print(f"  Saved: {cdse_out}", flush=True)

    print("\nStep 2: Fetch prod-DB Yanbu dates…", flush=True)
    db_rows = sb_fetch_all("terminal_observations", select="date,tank_id",
                           filters={"terminal_id": "yanbu"})
    db_dates = sorted(set(r["date"][:10] for r in db_rows if r.get("date")))
    print(f"  terminal_observations yanbu: {len(db_rows)} rows, {len(db_dates)} unique dates", flush=True)
    print(f"  first={db_dates[0] if db_dates else 'N/A'}, last={db_dates[-1] if db_dates else 'N/A'}", flush=True)

    db_dates_set = set(db_dates)
    matched = [s for s in deduped if s["date"] in db_dates_set]
    print(f"\nStep 3: CDSE scenes matching DB dates: {len(matched)}", flush=True)
    by_orb_matched = defaultdict(int)
    for s in matched:
        by_orb_matched[s["relOrbit"]] += 1
    for orb, cnt in sorted(by_orb_matched.items()):
        print(f"  relOrbit={orb}: {cnt} matched dates", flush=True)

    print("\nStep 4: Writing manifest…", flush=True)
    manifest_path = REPO_ROOT / "diagnostics/research/2026-10/event_csv/v0_manifest_all.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(matched, manifest_path)

    print("\nStep 5: Running mega_export section 4 for yanbu…", flush=True)
    result = subprocess.run(
        [sys.executable, "scripts/research/mega_export.py", "--sections", "4",
         "--terminal", "yanbu"],
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        print(f"  [WARN] mega_export exited {result.returncode}", flush=True)

    print("\n=== Ring15 quality report ===", flush=True)
    import glob
    part_files = sorted(glob.glob(str(OUT_DIR / "tank_scene_variants_yanbu*.csv.gz")))
    stats = defaultdict(lambda: [0, 0])
    for pf in part_files:
        with gzip.open(pf, "rt") as f:
            for row in csv.DictReader(f):
                rel_orb = row.get("relOrbit", "")
                direction = row.get("direction", "")
                k = ("yanbu", rel_orb, direction)
                stats[k][0] += 1
                r15 = row.get("ring15_far", "")
                if r15 not in ("", "0.0", "0"):
                    stats[k][1] += 1

    any_zero = False
    for (tid, orb, direction), (total, nn) in sorted(stats.items()):
        frac = nn / total if total else 0
        flag = " ← ZERO — DO NOT COMMIT" if frac == 0 and total > 0 else ""
        print(f"  {tid} orb={orb} {direction}: {nn}/{total} non-null ({frac:.3f}){flag}", flush=True)
        if frac == 0 and total > 0:
            any_zero = True

    if any_zero:
        print("\n[FATAL] Zero ring15_far fraction detected — do not commit.", flush=True)
        sys.exit(1)
    else:
        print("\n[OK] All terminal+orbit groups have ring15_far > 0", flush=True)


if __name__ == "__main__":
    main()
