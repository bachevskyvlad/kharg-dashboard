"""
build_registry_patch.py — Build a curated orbit patch with DESC-first rule.

Rules applied:
  1. FROZEN terminals (have Supabase data, orbit already set or ambiguous):
     ras_tanura, juwaymah, yanbu, al_flaw → EXCLUDED from patch.
  2. Russia cluster (kozmino, de_kastri, primorsk, novorossiysk) → EXCLUDED.
  3. For remaining 11 NEEDS_BASELINE terminals: DESC-first selection.
     If FULL DESC exists → take highest-n DESC as primary.
     Else → take FULL ASC.
  4. All candidates (FULL coverage) included in allowed_orbits.
  5. Each orbit entry gets orbit_source: "survey_2026-10".
  6. Fujairah special note: 166 DESC as primary (per human review).

Input:  diagnostics/orbit_survey_registry_patch.json  (all candidates from survey)
Output: diagnostics/orbit_registry_patch_curated.json  (safe to --apply)

Usage:
  cd scripts
  python build_registry_patch.py
"""

import json
from pathlib import Path

PATCH_IN  = Path(__file__).parent.parent / "diagnostics" / "orbit_survey_registry_patch.json"
PATCH_OUT = Path(__file__).parent.parent / "diagnostics" / "orbit_registry_patch_curated.json"

# These terminals must NOT be patched under any circumstances
FROZEN = {"ras_tanura", "juwaymah", "yanbu", "al_flaw"}
RUSSIA = {"kozmino", "de_kastri", "primorsk", "novorossiysk"}
EXCLUDE = FROZEN | RUSSIA

# Scene counts from orbit_survey output (used for DESC-first selection)
# Format: {terminal: [(rel, direction, n_scenes), ...]}  — top candidates only
SURVEY_N = {
    "jask":       [(93, "DESC", 109), (57, "ASCE", 111)],
    "lavan":      [(64, "DESC", 221), (28, "ASCE", 244)],
    "siri":       [(166, "DESC", 241), (64, "DESC", 221), (130, "ASCE", 245)],
    "halul":      [(137, "DESC", 239), (64, "DESC", 222), (28, "ASCE", 244)],
    "sitra":      [(137, "DESC", 239), (101, "ASCE", 244)],
    "ras_laffan": [(137, "DESC", 239), (101, "ASCE", 244)],
    "fujairah":   [(166, "DESC", 240), (93, "DESC", 239), (130, "ASCE", 245), (57, "ASCE", 243)],
    "zirku":      [(64, "DESC", 222), (28, "ASCE", 244)],
    "das_island": [(64, "DESC", 222), (28, "ASCE", 244)],
    "ruwais":     [(64, "DESC", 220), (28, "ASCE", 243)],
    "mina_ahmadi":[(108, "DESC", 235), (174, "ASCE", 242)],
}

LOS_BY_DIR = {"DESC": 282, "ASCE": 98, "descending": 282, "ascending": 98}

raw = json.loads(PATCH_IN.read_text())

curated = {}
for tid, candidates in raw.items():
    if tid in EXCLUDE:
        print(f"  [{tid}] EXCLUDED (frozen/russia)")
        continue
    if tid not in SURVEY_N:
        print(f"  [{tid}] WARN: not in SURVEY_N, skipping")
        continue

    # Build orbit list: primary first (DESC-first), then remaining candidates
    survey = SURVEY_N[tid]  # ordered by priority already

    # Primary = first DESC entry; fall back to first ASC if no FULL DESC
    desc_cands = [(rel, dir_, n) for rel, dir_, n in survey if dir_ == "DESC"]
    primary_tuple = desc_cands[0] if desc_cands else survey[0]
    primary_rel, primary_dir, _ = primary_tuple

    # Build allowed_orbits list: primary first, then others in survey order
    orbits_out = []
    seen = set()
    for rel, direction, _ in survey:
        if (rel, direction) in seen:
            continue
        seen.add((rel, direction))
        entry = {
            "relative_orbit": rel,
            "direction": direction,
            "los_azimuth": LOS_BY_DIR[direction],
            "orbit_source": "survey_2026-10",
        }
        if rel == primary_rel and direction == primary_dir:
            entry["primary"] = True
        orbits_out.append(entry)

    # Put primary first
    orbits_out.sort(key=lambda o: (0 if o.get("primary") else 1))

    curated[tid] = orbits_out
    primary_entry = orbits_out[0]
    print(f"  [{tid}] PRIMARY: rel={primary_entry['relative_orbit']} "
          f"{primary_entry['direction']}  candidates: {len(orbits_out)-1}")

PATCH_OUT.write_text(json.dumps(curated, indent=2))
print(f"\nWritten: {PATCH_OUT}")
print(f"Terminals patched: {sorted(curated.keys())}")
print(f"Excluded: {sorted(EXCLUDE)}")
