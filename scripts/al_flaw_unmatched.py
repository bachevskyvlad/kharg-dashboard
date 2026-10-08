"""
al_flaw_unmatched.py — Investigate the 82 al_flaw dates not matched by rel=108 DESC.

Steps:
1. Pull al_flaw observation dates from terminal_observations (all).
2. Pull rel=108 DESC catalog dates over al_flaw bbox.
3. Identify unmatched dates (Supabase − catalog).
4. For each unmatched date, search ALL catalog orbits (DESC + ASC) over bbox within ±1 day.
5. Build attribution table: date | nearest_108_scene | delta_days | matched_orbit | direction.
6. Per-year distribution of unmatched dates.
7. Verdict: artefact (±1d shift) vs foreign-track contamination vs genuine gap.
8. Write diagnostics/AL_FLAW_UNMATCHED.md + al_flaw_unmatched_result.json.

Usage:
  cd scripts
  python al_flaw_unmatched.py
"""

import os, sys, json
from datetime import datetime, timedelta, date
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config import config, S1_CDSE, sb
from locations import get_super_bbox
from tank_registry import LOCATIONS

from sentinelhub import SentinelHubCatalog, BBox, CRS

OUT_DIR = Path(__file__).parent.parent / "diagnostics"
OUT_DIR.mkdir(exist_ok=True)

SURVEY_START = "2017-01-01"
SURVEY_END   = datetime.utcnow().strftime("%Y-%m-%d")
HISTORICAL_ORBIT = 108
HISTORICAL_DIR   = "DESC"

# ── 1. al_flaw bbox ──────────────────────────────────────────────────────────
af_loc = next(l for l in LOCATIONS if l["id"] == "al_flaw")
af_tanks_raw = [{"id": t["id"], "lat": t["lat"], "lon": t["lon"],
                 "radius": t["radius_m"], "h_max": t["h_max_m"]}
                for t in af_loc["tanks"]]
bbox = get_super_bbox(af_tanks_raw, buffer_m=500)
print(f"al_flaw bbox: {bbox}")

# ── 2. Supabase — all al_flaw dates ──────────────────────────────────────────
print("\nPulling al_flaw dates from terminal_observations …")
all_rows = []
offset = 0
while True:
    rows = (sb.table("terminal_observations")
              .select("date")
              .eq("terminal_id", "al_flaw")
              .range(offset, offset + 999)
              .execute().data)
    all_rows.extend(rows)
    if len(rows) < 1000:
        break
    offset += 1000

sb_dates = sorted({pd.to_datetime(r["date"]).date() for r in all_rows})
print(f"  {len(sb_dates)} distinct dates  ({sb_dates[0]} → {sb_dates[-1]})")

# ── 3. Catalog — rel=108 DESC scenes ─────────────────────────────────────────
print(f"\nQuerying CDSE catalog for {HISTORICAL_ORBIT} DESC …")
catalog = SentinelHubCatalog(config=config)

scenes_108 = list(catalog.search(
    S1_CDSE,
    bbox=bbox,
    time=(SURVEY_START, SURVEY_END),
    filter="sat:orbit_state = 'descending'",
    fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
))
cat_108 = {pd.to_datetime(s["properties"]["datetime"]).normalize().date()
           for s in scenes_108
           if s["properties"].get("sat:relative_orbit") == HISTORICAL_ORBIT}
print(f"  rel=108 DESC dates: {len(cat_108)}")

# Unmatched
unmatched = sorted(set(sb_dates) - cat_108)
print(f"  Unmatched dates: {len(unmatched)}")

# ── 4. Full catalog scan — all DESC + ASC orbits over bbox ────────────────────
print(f"\nFull catalog scan (DESC + ASC) {SURVEY_START}…{SURVEY_END} …")

all_desc = list(catalog.search(
    S1_CDSE,
    bbox=bbox,
    time=(SURVEY_START, SURVEY_END),
    filter="sat:orbit_state = 'descending'",
    fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
))
all_asc = list(catalog.search(
    S1_CDSE,
    bbox=bbox,
    time=(SURVEY_START, SURVEY_END),
    filter="sat:orbit_state = 'ascending'",
    fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
))
print(f"  DESC scenes: {len(all_desc)}  ASC scenes: {len(all_asc)}")

# Build date-index: date → [(relative_orbit, direction)]
date_to_orbits: dict[date, list] = defaultdict(list)
for s in all_desc:
    rel = s["properties"].get("sat:relative_orbit")
    d   = pd.to_datetime(s["properties"]["datetime"]).normalize().date()
    date_to_orbits[d].append((rel, "DESC"))
for s in all_asc:
    rel = s["properties"].get("sat:relative_orbit")
    d   = pd.to_datetime(s["properties"]["datetime"]).normalize().date()
    date_to_orbits[d].append((rel, "ASC"))

# ── 5. Attribution of unmatched dates ────────────────────────────────────────
print("\n── UNMATCHED DATE ATTRIBUTION ──")

# Sort 108 catalog dates for nearest-scene lookup
cat_108_sorted = sorted(cat_108)

def nearest_108(d):
    """Return (nearest_date, delta_days) for the closest 108-DESC scene."""
    if not cat_108_sorted:
        return None, None
    diffs = [(abs((c - d).days), c) for c in cat_108_sorted]
    diffs.sort()
    return diffs[0][1], diffs[0][0]

attribution = []
for d in unmatched:
    nearest, delta = nearest_108(d)
    # Check ±1 day: does a 108 DESC scene exist within 1 day?
    pm1_match = any((d + timedelta(days=k)) in cat_108 for k in [-1, 0, 1])
    # Other orbits on the exact date
    same_day = date_to_orbits.get(d, [])
    same_day_non108 = [(r, dr) for r, dr in same_day if not (r == HISTORICAL_ORBIT and dr == HISTORICAL_DIR)]
    # ±1 day other orbits
    pm1_orbits = set()
    for k in [-1, 1]:
        for r, dr in date_to_orbits.get(d + timedelta(days=k), []):
            pm1_orbits.add((r, dr))
    attribution.append({
        "date": str(d),
        "year": d.year,
        "nearest_108_date": str(nearest) if nearest else None,
        "delta_days_to_108": delta,
        "pm1_108_match": pm1_match,
        "same_day_orbits": [(r, dr) for r, dr in same_day if r != HISTORICAL_ORBIT],
        "pm1_foreign_orbits": sorted(pm1_orbits),
        "no_catalog_scene": len(same_day) == 0,
    })

# Summary counts
pm1_artefact = sum(1 for a in attribution if a["pm1_108_match"])
foreign_track = sum(1 for a in attribution if not a["pm1_108_match"] and a["same_day_orbits"])
true_gap      = sum(1 for a in attribution if a["no_catalog_scene"])
unexplained   = len(unmatched) - pm1_artefact - foreign_track - true_gap

print(f"  Total unmatched  : {len(unmatched)}")
print(f"  ±1d artefact     : {pm1_artefact}  (108 scene exists within 1 day → date-shift)")
print(f"  Foreign track    : {foreign_track}  (other orbit on same day)")
print(f"  True gap (no SAR): {true_gap}  (no catalog scene at all)")
print(f"  Unexplained      : {unexplained}")

# ── 6. Per-year distribution ──────────────────────────────────────────────────
print("\n── PER-YEAR DISTRIBUTION ──")
by_year = defaultdict(lambda: {"total": 0, "pm1": 0, "foreign": 0, "gap": 0})
for a in attribution:
    y = a["year"]
    by_year[y]["total"] += 1
    if a["pm1_108_match"]:
        by_year[y]["pm1"] += 1
    elif a["same_day_orbits"]:
        by_year[y]["foreign"] += 1
    elif a["no_catalog_scene"]:
        by_year[y]["gap"] += 1

print(f"  {'Year':<6} {'total':<7} {'±1d_art':<9} {'foreign':<9} {'gap':<5}")
print(f"  {'-'*36}")
for year in sorted(by_year):
    b = by_year[year]
    print(f"  {year:<6} {b['total']:<7} {b['pm1']:<9} {b['foreign']:<9} {b['gap']:<5}")

# ── 7. 3 date-tolerance examples ─────────────────────────────────────────────
print("\n── DATE-TOLERANCE EXAMPLES (±1 day artefacts) ──")
examples = [a for a in attribution if a["pm1_108_match"]][:3]
for ex in examples:
    print(f"  Supabase date={ex['date']}  nearest_108={ex['nearest_108_date']}  Δ={ex['delta_days_to_108']}d")

# ── 8. Foreign-track details ──────────────────────────────────────────────────
if foreign_track > 0:
    print("\n── FOREIGN TRACK DETAILS ──")
    orbit_counts = defaultdict(int)
    for a in attribution:
        for r, dr in a["same_day_orbits"]:
            orbit_counts[(r, dr)] += 1
    for (r, dr), cnt in sorted(orbit_counts.items(), key=lambda x: -x[1]):
        print(f"  rel={r} {dr}: {cnt} dates")

# ── 9. Verdict ────────────────────────────────────────────────────────────────
print("\n── VERDICT ──")
if pm1_artefact == len(unmatched):
    verdict = "ARTEFACT_ONLY"
    verdict_text = f"All {len(unmatched)} unmatched dates explained by ±1d date-shift in ingest. No foreign tracks. No action needed."
elif foreign_track > 0:
    verdict = "CONTAMINATION_CONFIRMED"
    verdict_text = f"{foreign_track} dates have foreign-orbit scenes on the same day. Contamination present."
else:
    verdict = "GAPS_ONLY"
    verdict_text = f"Unmatched dates are true SAR gaps (no scene in catalog). Normal for orbit geometry. No foreign tracks."
print(f"  {verdict}: {verdict_text}")

# ── 10. Write outputs ─────────────────────────────────────────────────────────
result = {
    "terminal": "al_flaw",
    "historical_orbit": HISTORICAL_ORBIT,
    "supabase_total_dates": len(sb_dates),
    "cat_108_dates": len(cat_108),
    "unmatched_total": len(unmatched),
    "pm1_artefact": pm1_artefact,
    "foreign_track": foreign_track,
    "true_gap": true_gap,
    "verdict": verdict,
    "per_year": {str(y): dict(v) for y, v in sorted(by_year.items())},
    "attribution": attribution,
}
(OUT_DIR / "al_flaw_unmatched_result.json").write_text(json.dumps(result, indent=2, default=str))
print(f"\nWritten: diagnostics/al_flaw_unmatched_result.json")

lines = [
    "# al_flaw Unmatched Dates — investigation",
    f"\nRun: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC  ",
    f"Historical track: rel={HISTORICAL_ORBIT} {HISTORICAL_DIR}  ",
    f"Supabase dates: {len(sb_dates)}  catalog-108 dates: {len(cat_108)}  unmatched: {len(unmatched)}  ",
    "",
    f"## Verdict: {verdict}",
    "",
    f"{verdict_text}",
    "",
    "## Per-year distribution",
    "",
    "| Year | total | ±1d_artefact | foreign_track | true_gap |",
    "|---|---|---|---|---|",
]
for year in sorted(by_year):
    b = by_year[year]
    lines.append(f"| {year} | {b['total']} | {b['pm1']} | {b['foreign']} | {b['gap']} |")

if examples:
    lines += [
        "",
        "## Date-tolerance examples (±1 day)",
        "",
        "| Supabase date | nearest 108 scene | Δ days |",
        "|---|---|---|",
    ]
    for ex in examples:
        lines.append(f"| {ex['date']} | {ex['nearest_108_date']} | {ex['delta_days_to_108']} |")

if foreign_track > 0:
    lines += [
        "",
        "## Foreign orbit counts",
        "",
        "| rel_orbit | direction | dates |",
        "|---|---|---|",
    ]
    for (r, dr), cnt in sorted(orbit_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {r} | {dr} | {cnt} |")

(OUT_DIR / "AL_FLAW_UNMATCHED.md").write_text("\n".join(lines))
print(f"Written: diagnostics/AL_FLAW_UNMATCHED.md")
print("\nDone.")
