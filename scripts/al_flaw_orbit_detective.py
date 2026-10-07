"""
al_flaw_orbit_detective.py — Data-derived orbit identification for al_flaw.

Steps:
1. Pull al_flaw observation DATES from Supabase raw_observations.
2. Query CDSE catalog for al_flaw bbox, all DESC scenes, 2020→now.
3. Group catalog scenes by (relative_orbit, direction) → date-sets.
4. Date-match: Supabase dates × each orbit's date-set → overlap count.
5. Per-year breakdown to detect S1C dual-track contamination in 2024+.
6. Write diagnostics/AL_FLAW_ORBIT_DETECTIVE.md + al_flaw_orbit_result.json.

The orbit with the highest date-overlap IS the historical ingest track.
No Process API calls — pure catalog + Supabase comparison.

Usage:
  cd scripts
  python al_flaw_orbit_detective.py
"""

import os, sys, json
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import config, S1_CDSE
from locations import get_super_bbox
from tank_registry import LOCATIONS

from sentinelhub import SentinelHubCatalog

OUT_DIR = Path(__file__).parent.parent / "diagnostics"
OUT_DIR.mkdir(exist_ok=True)

SURVEY_START = "2020-01-01"
SURVEY_END   = datetime.utcnow().strftime("%Y-%m-%d")

# ── 1. al_flaw bbox ──────────────────────────────────────────────────────────
af_loc = next(l for l in LOCATIONS if l["id"] == "al_flaw")
af_tanks_raw = [{"id": t["id"], "lat": t["lat"], "lon": t["lon"],
                 "radius": t["radius_m"], "h_max": t["h_max_m"]}
                for t in af_loc["tanks"]]
bbox = get_super_bbox(af_tanks_raw, buffer_m=500)
print(f"al_flaw bbox: {bbox}")

# ── 2. CDSE catalog — all DESC scenes over al_flaw ───────────────────────────
print(f"\nQuerying CDSE catalog DESC {SURVEY_START}…{SURVEY_END} …")
catalog = SentinelHubCatalog(config=config)

scenes = list(catalog.search(
    S1_CDSE,
    bbox=bbox,
    time=(SURVEY_START, SURVEY_END),
    filter="sat:orbit_state = 'descending'",
    fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
))
print(f"  DESC scenes returned: {len(scenes)}")

# Also check ASC
scenes_asc = list(catalog.search(
    S1_CDSE,
    bbox=bbox,
    time=(SURVEY_START, SURVEY_END),
    filter="sat:orbit_state = 'ascending'",
    fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
))
print(f"  ASC scenes returned: {len(scenes_asc)}")

all_scenes = [(s, "DESC") for s in scenes] + [(s, "ASC") for s in scenes_asc]

# Group by (relative_orbit, direction) → set of calendar dates
orbit_dates: dict[tuple, set] = defaultdict(set)
for s, direction in all_scenes:
    rel = s["properties"].get("sat:relative_orbit")
    dt  = pd.to_datetime(s["properties"]["datetime"]).normalize().date()
    orbit_dates[(rel, direction)].add(dt)

print(f"\nOrbit tracks found over al_flaw:")
for (rel, dir_), dates in sorted(orbit_dates.items(), key=lambda x: -len(x[1])):
    print(f"  rel={rel:4d} {dir_:4s}  n={len(dates):4d}")

# ── 3. Supabase — al_flaw observation dates ──────────────────────────────────
print("\nPulling al_flaw dates from Supabase …")
try:
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    tank_ids = [t["id"] for t in af_loc["tanks"]]
    # Pull all distinct dates for any al_flaw tank
    all_rows = []
    offset = 0
    while True:
        rows = (sb.table("raw_observations")
                  .select("date,tank_id")
                  .in_("tank_id", tank_ids)
                  .range(offset, offset + 999)
                  .execute().data)
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000

    # Distinct dates
    sb_dates = {pd.to_datetime(r["date"]).date() for r in all_rows}
    print(f"  Supabase al_flaw: {len(all_rows)} rows, {len(sb_dates)} distinct dates")

    # Per-year row count to detect contamination
    year_counts = defaultdict(int)
    for r in all_rows:
        year_counts[pd.to_datetime(r["date"]).year] += 1
    # rows per date (24 tanks → 24 rows/date normally; >24 → multiple tracks)
    dates_list = [pd.to_datetime(r["date"]).date() for r in all_rows]
    date_freq = defaultdict(int)
    for d in dates_list:
        date_freq[d] += 1
    # count dates with >24 tank-rows (24 tanks = 1 track per date)
    n_tanks = len(tank_ids)
    multi_track_dates = {d: c for d, c in date_freq.items() if c > n_tanks}

except Exception as e:
    print(f"  [!] Supabase error: {e}")
    sb_dates = set()
    year_counts = {}
    multi_track_dates = {}

# ── 4. Date-match ─────────────────────────────────────────────────────────────
print("\n── DATE-MATCH (Supabase dates × catalog orbit dates) ──")
matches = {}
for (rel, direction), cat_dates in sorted(orbit_dates.items(), key=lambda x: -len(x[1])):
    overlap = sb_dates & cat_dates
    matches[(rel, direction)] = {
        "n_catalog": len(cat_dates),
        "n_supabase": len(sb_dates),
        "n_overlap": len(overlap),
        "pct": round(100 * len(overlap) / len(sb_dates), 1) if sb_dates else 0,
    }
    print(f"  rel={rel:4d} {direction:4s}  catalog={len(cat_dates):4d}  overlap={len(overlap):4d}  "
          f"({matches[(rel,direction)]['pct']:.1f}% of Supabase dates)")

# Best match
best = max(matches.items(), key=lambda x: x[1]["n_overlap"]) if matches else None
if best:
    print(f"\n  ★ HISTORICAL TRACK: rel={best[0][0]} {best[0][1]} "
          f"— {best[1]['n_overlap']}/{best[1]['n_supabase']} dates matched "
          f"({best[1]['pct']}%)")

# ── 5. Per-year breakdown ─────────────────────────────────────────────────────
print("\n── PER-YEAR BREAKDOWN ──")
# Supabase dates per year
sb_by_year = defaultdict(set)
for d in sb_dates:
    sb_by_year[d.year].add(d)

# Catalog dates per year for top 2 orbits by overlap
top_orbits = sorted(matches.items(), key=lambda x: -x[1]["n_overlap"])[:3]

print(f"  Year  | SB_dates | " + " | ".join(f"rel{o[0][0]}{o[0][1]:4s}" for o in top_orbits))
print(f"  " + "-" * (12 + 12 * len(top_orbits)))
for year in range(2020, datetime.utcnow().year + 1):
    sb_y = sb_by_year.get(year, set())
    parts = []
    for (rel, direction), _ in top_orbits:
        cat_y = {d for d in orbit_dates[(rel, direction)] if d.year == year}
        ov = len(sb_y & cat_y)
        parts.append(f"{ov:5d}/{len(cat_y):<4d}")
    print(f"  {year}  | {len(sb_y):8d} | " + " | ".join(parts))

# Multi-track contamination check
print(f"\n── CONTAMINATION CHECK (rows/date > {n_tanks} tanks) ──")
if multi_track_dates:
    by_year = defaultdict(list)
    for d, c in sorted(multi_track_dates.items()):
        by_year[d.year].append((d, c))
    for year, items in sorted(by_year.items()):
        print(f"  {year}: {len(items)} contaminated dates  "
              f"(max rows/date: {max(c for _,c in items)})")
else:
    print("  No contaminated dates found (all dates have ≤ n_tanks rows)")

# ── 6. Write outputs ──────────────────────────────────────────────────────────
# Determine historical orbit
historical = None
if best and best[1]["pct"] >= 70:
    historical = {"relative_orbit": best[0][0], "direction": best[0][1],
                  "los_azimuth": 282 if best[0][1] == "DESC" else 98,
                  "orbit_source": "data-derived"}
    # Candidate: second-best overlap
    candidates = [o for o in top_orbits[1:] if o[1]["pct"] >= 5]
    historical["candidates"] = [
        {"relative_orbit": o[0][0], "direction": o[0][1],
         "los_azimuth": 282 if o[0][1] == "DESC" else 98}
        for o in candidates
    ]

result = {
    "terminal": "al_flaw",
    "historical_track": historical,
    "contamination_dates": len(multi_track_dates),
    "supabase_total_dates": len(sb_dates),
    "matches": {f"rel{k[0]}_{k[1]}": v for k, v in matches.items()},
}
(OUT_DIR / "al_flaw_orbit_result.json").write_text(json.dumps(result, indent=2))
print(f"\nWritten: diagnostics/al_flaw_orbit_result.json")

# Markdown report
lines = [
    "# al_flaw Orbit Detective — data-derived track identification",
    f"\nRun: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC  ",
    f"Survey: {SURVEY_START} … {SURVEY_END}  ",
    f"Supabase dates: {len(sb_dates)}  ",
    f"n_tanks: {n_tanks}  ",
    "",
    "## Date-match summary",
    "",
    "| rel_orbit | direction | catalog_dates | overlap | pct_sb |",
    "|---|---|---|---|---|",
]
for (rel, direction), m in sorted(matches.items(), key=lambda x: -x[1]["n_overlap"]):
    lines.append(f"| {rel} | {direction} | {m['n_catalog']} | {m['n_overlap']} | {m['pct']}% |")

if historical:
    lines += [
        "",
        f"## Historical track",
        "",
        f"**rel={historical['relative_orbit']} {historical['direction']}** — "
        f"{matches[(historical['relative_orbit'], historical['direction'])]['n_overlap']}/"
        f"{len(sb_dates)} dates matched ({matches[(historical['relative_orbit'], historical['direction'])]['pct']}%)  ",
        f"los_azimuth: {historical['los_azimuth']}  ",
        f"orbit_source: {historical['orbit_source']}  ",
    ]
else:
    lines += ["", "## Historical track", "", "AMBIGUOUS — manual review required"]

lines += [
    "",
    "## Per-year breakdown",
    "",
    f"| Year | SB_dates | " + " | ".join(f"rel{o[0][0]} {o[0][1]}" for o in top_orbits) + " |",
    "|---|---|" + "|".join(["---|"] * len(top_orbits)),
]
for year in range(2020, datetime.utcnow().year + 1):
    sb_y = sb_by_year.get(year, set())
    parts = []
    for (rel, direction), _ in top_orbits:
        cat_y = {d for d in orbit_dates[(rel, direction)] if d.year == year}
        ov = len(sb_y & cat_y)
        parts.append(f"{ov}/{len(cat_y)}")
    lines.append(f"| {year} | {len(sb_y)} | " + " | ".join(parts) + " |")

lines += [
    "",
    "## Contamination check",
    "",
]
if multi_track_dates:
    lines.append(f"**{len(multi_track_dates)} dates with rows > {n_tanks} tanks** — dual-track contamination confirmed.")
    by_year = defaultdict(int)
    for d in multi_track_dates:
        by_year[d.year] += 1
    for year, cnt in sorted(by_year.items()):
        lines.append(f"- {year}: {cnt} contaminated dates")
    lines += [
        "",
        "**Action**: allowed_orbits = historical track only. "
        "Contaminated rows in raw_observations should be reviewed/removed.",
    ]
else:
    lines.append(f"No contamination found — all dates ≤ {n_tanks} rows.")

(OUT_DIR / "AL_FLAW_ORBIT_DETECTIVE.md").write_text("\n".join(lines))
print(f"Written: diagnostics/AL_FLAW_ORBIT_DETECTIVE.md")
print("\nDone.")
