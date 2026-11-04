"""
orbit_survey.py — Sentinel-1 orbit catalogue survey for all terminals
without a confirmed relative_orbit in tank_registry.py.

For each terminal (15 NEEDS_BASELINE + al_flaw + kharg reference):
  - Query CDSE catalog bbox × 2020-01-01…now, both orbit states
  - Client-side group by (relative_orbit, direction)
  - Per orbit: n_scenes, cadence_days (median interval), bbox_coverage (FULL/PARTIAL)
  - Select working orbit: FULL-frame + max scenes + best cadence
  - Emit recommended allowed_orbits patch (JSON)
  - Write ORBIT_SURVEY.md

Coverage check: scene geometry polygon vs terminal bbox.
  FULL   = footprint covers ≥ 95% of bbox area
  PARTIAL = 20–95%
  EDGE   = < 20%  (likely artefact, like RT desc-137)

Usage:
  cd scripts
  python orbit_survey.py [--force]   # --force writes registry patch + .md
  python orbit_survey.py             # dry-run, prints table only

Credentials from env: CLIENT_ID, CLIENT_SECRET (CDSE OAuth).
"""

import sys, os, json, math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import config, S1_CDSE
from locations import get_super_bbox
from tank_registry import LOCATIONS

try:
    from sentinelhub import SentinelHubCatalog, BBox, CRS
    from shapely.geometry import shape, box as shapely_box
    SHAPELY = True
except ImportError:
    SHAPELY = False
    print("WARNING: shapely not installed — coverage will be UNKNOWN")

FORCE = "--force" in sys.argv

OUT_DIR = Path(__file__).parent.parent / "diagnostics"
OUT_DIR.mkdir(exist_ok=True)

SURVEY_START = "2020-01-01"
SURVEY_END   = datetime.utcnow().strftime("%Y-%m-%d")

# los_azimuth by orbit direction
LOS_BY_DIR = {"descending": 282, "ascending": 98}

# Terminals to survey: all except kharg (which is reference-only)
# al_flaw is included even though it has data — missing relative_orbit in registry
SKIP = set()   # none skipped; kharg added at end as reference row

RUSSIA_IDS = {"kozmino", "de_kastri", "primorsk", "novorossiysk"}

# ─────────────────────────────────────────────────────────────────────────────

def bbox_area_deg2(bbox):
    """Area of bbox in deg²."""
    return (bbox.max_x - bbox.min_x) * (bbox.max_y - bbox.min_y)


def coverage_fraction(scene_geom_json, bbox):
    """Fraction of bbox covered by scene footprint. Requires shapely."""
    if not SHAPELY or scene_geom_json is None:
        return None
    try:
        scene_poly = shape(scene_geom_json)
        term_box   = shapely_box(bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y)
        inter_area = scene_poly.intersection(term_box).area
        return inter_area / term_box.area if term_box.area > 0 else 0.0
    except Exception:
        return None


def coverage_label(frac):
    if frac is None:
        return "UNKNOWN"
    if frac >= 0.95:
        return "FULL"
    if frac >= 0.20:
        return "PARTIAL"
    return "EDGE"


def query_terminal(terminal_id, tanks, survey_start=SURVEY_START):
    """
    Query catalog for both orbit states. Return dict:
      {(relative_orbit, direction): {"scenes": [...datetimes], "coverages": [...fracs]}}
    """
    bbox = get_super_bbox(
        [{"lat": t["lat"], "lon": t["lon"]} for t in tanks],
        buffer_m=500
    )
    catalog = SentinelHubCatalog(config=config)
    result = defaultdict(lambda: {"scenes": [], "coverages": []})

    for orbit_state in ("descending", "ascending"):
        _norm = {"descending": "descending", "ascending": "ascending"}
        stac_filter = f"sat:orbit_state = '{orbit_state}'"
        try:
            scenes = list(catalog.search(
                S1_CDSE,
                bbox=bbox,
                time=(survey_start, SURVEY_END),
                filter=stac_filter,
                fields={
                    "include": [
                        "properties.datetime",
                        "properties.sat:relative_orbit",
                        "geometry",
                    ]
                },
            ))
        except Exception as e:
            print(f"    [{terminal_id}] catalog error ({orbit_state}): {e}")
            continue

        # Deduplicate: keep first scene per (relative_orbit, calendar_date)
        seen = set()
        for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
            rel = s["properties"].get("sat:relative_orbit")
            dt  = np.datetime64(s["properties"]["datetime"][:10])
            key = (rel, str(dt))
            if key in seen:
                continue
            seen.add(key)
            k = (rel, orbit_state)
            result[k]["scenes"].append(str(dt))
            cov = coverage_fraction(s.get("geometry"), bbox)
            result[k]["coverages"].append(cov)

    return bbox, result


def orbit_stats(data):
    """
    data: {"scenes": [date_str, ...], "coverages": [frac, ...]}
    Returns: n_scenes, cadence_days (median), coverage_label, coverage_frac_median
    """
    scenes = sorted(data["scenes"])
    n = len(scenes)
    if n == 0:
        return 0, None, "NO_DATA", None
    if n == 1:
        cadence = None
    else:
        dates = [datetime.strptime(d, "%Y-%m-%d") for d in scenes]
        gaps  = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        cadence = float(np.median(gaps))

    covs = [c for c in data["coverages"] if c is not None]
    med_cov = float(np.median(covs)) if covs else None
    label   = coverage_label(med_cov)

    return n, cadence, label, med_cov


def select_working_orbit(orbit_map):
    """
    From {(rel_orbit, direction): stats_dict}, choose best orbit(s).
    Priority: FULL coverage > n_scenes > cadence (min).
    Returns list of candidate dicts sorted best first.
    """
    candidates = []
    for (rel, direction), stats in orbit_map.items():
        n, cad, cov_label, cov_frac = stats
        if n == 0:
            continue
        candidates.append({
            "relative_orbit": rel,
            "direction":      direction,
            "n_scenes":       n,
            "cadence_days":   cad,
            "coverage":       cov_label,
            "coverage_frac":  cov_frac,
        })
    if not candidates:
        return []
    # Sort: FULL first, then PARTIAL, then by n_scenes desc, cadence asc
    cov_rank = {"FULL": 0, "PARTIAL": 1, "EDGE": 2, "UNKNOWN": 3, "NO_DATA": 4}
    candidates.sort(key=lambda c: (
        cov_rank.get(c["coverage"], 9),
        -c["n_scenes"],
        c["cadence_days"] if c["cadence_days"] else 999,
    ))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"=== orbit_survey.py  {SURVEY_START}…{SURVEY_END} ===")
    print(f"  shapely available: {SHAPELY}")
    print()

    report_rows = []       # for ORBIT_SURVEY.md
    registry_patches = {}  # terminal_id → allowed_orbits list

    # Build terminal list: all except kharg (processed last as reference)
    survey_terminals = [t for t in LOCATIONS if t["id"] != "kharg"]

    for term in survey_terminals:
        tid     = term["id"]
        country = term["country"]
        tanks   = term["tanks"]
        n_tanks = len(tanks)
        is_russia = tid in RUSSIA_IDS

        # Jask: start from 2024-06-01 (before = construction noise)
        survey_start = "2024-06-01" if tid == "jask" else SURVEY_START

        print(f"  {tid:<18} ({country}, {n_tanks}t) ...", end="", flush=True)

        try:
            bbox, raw = query_terminal(tid, tanks, survey_start=survey_start)
        except Exception as e:
            print(f" ERROR: {e}")
            report_rows.append({
                "terminal_id": tid, "country": country, "n_tanks": n_tanks,
                "error": str(e), "candidates": [], "russia": is_russia,
            })
            continue

        # Compute stats per orbit
        orbit_map = {}
        for (rel, direction), data in raw.items():
            n, cad, cov_label, cov_frac = orbit_stats(data)
            orbit_map[(rel, direction)] = {
                "n": n, "cadence": cad, "coverage": cov_label,
                "cov_frac": cov_frac,
            }

        candidates = select_working_orbit(
            {k: (v["n"], v["cadence"], v["coverage"], v["cov_frac"])
             for k, v in orbit_map.items()}
        )

        # Print summary line
        if candidates:
            best = candidates[0]
            print(f" best: rel={best['relative_orbit']} {best['direction'][:4].upper()} "
                  f"n={best['n_scenes']} cad={best['cadence_days']:.1f}d "
                  f"cov={best['coverage']}")
        else:
            print(" NO DATA")

        # Build registry patch for this terminal
        # Use top candidate(s): if 2nd candidate is FULL and cadence within 2d of best → add both
        patch_orbits = []
        for c in candidates[:3]:
            if c["coverage"] in ("FULL", "PARTIAL") and c["n_scenes"] >= 5:
                los = LOS_BY_DIR.get(c["direction"], 282)
                patch_orbits.append({
                    "relative_orbit": c["relative_orbit"],
                    "direction":      c["direction"][:4].upper(),
                    "los_azimuth":    los,
                })
                # Stop after first FULL unless next is also FULL
                if c["coverage"] == "FULL" and len(patch_orbits) >= 1:
                    if len(candidates) > candidates.index(c) + 1:
                        nxt = candidates[candidates.index(c) + 1]
                        if nxt["coverage"] != "FULL":
                            break
                    else:
                        break

        if patch_orbits:
            registry_patches[tid] = patch_orbits

        report_rows.append({
            "terminal_id": tid,
            "country":     country,
            "n_tanks":     n_tanks,
            "candidates":  candidates,
            "patch":       patch_orbits,
            "russia":      is_russia,
            "jask_window": tid == "jask",
        })

    # ── Kharg reference row ───────────────────────────────────────────────────
    print(f"  {'kharg':<18} (IR, 47t) reference ...", end="", flush=True)
    try:
        kharg = next(t for t in LOCATIONS if t["id"] == "kharg")
        bbox, raw = query_terminal("kharg", kharg["tanks"])
        orbit_map = {}
        for (rel, direction), data in raw.items():
            n, cad, cov_label, cov_frac = orbit_stats(data)
            orbit_map[(rel, direction)] = {"n": n, "cadence": cad,
                                           "coverage": cov_label, "cov_frac": cov_frac}
        candidates = select_working_orbit(
            {k: (v["n"], v["cadence"], v["coverage"], v["cov_frac"])
             for k, v in orbit_map.items()}
        )
        if candidates:
            best = candidates[0]
            print(f" best: rel={best['relative_orbit']} {best['direction'][:4].upper()} "
                  f"n={best['n_scenes']} cov={best['coverage']}")
        else:
            print(" NO DATA")
        report_rows.append({
            "terminal_id": "kharg", "country": "IR", "n_tanks": 47,
            "candidates": candidates, "patch": [], "russia": False,
            "reference": True,
        })
    except Exception as e:
        print(f" ERROR: {e}")

    # ── Print full table ──────────────────────────────────────────────────────
    print()
    print("=" * 110)
    print(f"  {'terminal':<18} {'cty':>3}  {'tnk':>3}  "
          f"{'rel_orb':>7}  {'dir':>5}  {'scenes':>6}  {'cad_d':>6}  {'coverage':>9}  "
          f"{'note'}")
    print("=" * 110)
    for row in report_rows:
        if row.get("error"):
            print(f"  {row['terminal_id']:<18} {row['country']:>3}  {row['n_tanks']:>3}  ERROR: {row['error']}")
            continue
        cands = row.get("candidates", [])
        is_ref = row.get("reference", False)
        is_ru  = row.get("russia", False)
        if not cands:
            note = "NO_DATA"
            if row.get("jask_window"):
                note += " (window ≥2024-06)"
            print(f"  {row['terminal_id']:<18} {row['country']:>3}  {row['n_tanks']:>3}  "
                  f"{'—':>7}  {'—':>5}  {'0':>6}  {'—':>6}  {'NO_DATA':>9}  {note}")
            continue
        for i, c in enumerate(cands[:4]):  # show up to 4 candidates
            prefix  = f"  {row['terminal_id']:<18}" if i == 0 else f"  {'':18}"
            country = row["country"] if i == 0 else "   "
            ntanks  = f"{row['n_tanks']:>3}" if i == 0 else "   "
            mark    = ""
            if i == 0 and not is_ref:
                mark = " ← WORKING" if c["coverage"] == "FULL" else " ← CANDIDATE"
            if is_ref and i == 0:
                mark = " [REF only]"
            if is_ru:
                mark += " [RU: defer]"
            cad_str = f"{c['cadence_days']:.1f}" if c['cadence_days'] else "—"
            print(f"  {row['terminal_id'] if i==0 else '':18} "
                  f"{country:>3}  {ntanks:>3}  "
                  f"{str(c['relative_orbit']):>7}  "
                  f"{c['direction'][:4].upper():>5}  "
                  f"{c['n_scenes']:>6}  "
                  f"{cad_str:>6}  "
                  f"{c['coverage']:>9}  {mark}")

    print()

    # ── Write registry patch JSON ─────────────────────────────────────────────
    patch_path = OUT_DIR / "orbit_survey_registry_patch.json"
    if FORCE:
        with open(patch_path, "w") as f:
            json.dump(registry_patches, f, indent=2)
        print(f"  → registry patch: {patch_path}")
    else:
        print(f"  DRY-RUN — pass --force to write patch + .md")
        print(f"  Registry patch preview ({len(registry_patches)} terminals):")
        for tid, orbits in registry_patches.items():
            print(f"    {tid}: {orbits}")

    # ── Write ORBIT_SURVEY.md ─────────────────────────────────────────────────
    if FORCE:
        _write_md(report_rows, patch_path)


def _write_md(report_rows, patch_path):
    md_path = Path(__file__).parent.parent / "diagnostics" / "ORBIT_SURVEY.md"
    lines = [
        "# ORBIT_SURVEY — Sentinel-1 orbit catalogue per terminal",
        f"",
        f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC  ",
        f"Survey window: {SURVEY_START} … {SURVEY_END}  ",
        f"Shapely coverage: {'yes' if SHAPELY else 'NO — coverage = UNKNOWN'}  ",
        "",
        "## Working orbit selection",
        "",
        "| terminal | country | tanks | rel_orbit | dir | scenes | cad_d | coverage | status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for row in report_rows:
        if row.get("error"):
            lines.append(f"| {row['terminal_id']} | {row['country']} | {row['n_tanks']} "
                         f"| — | — | — | — | ERROR | {row['error']} |")
            continue
        cands = row.get("candidates", [])
        is_ref = row.get("reference", False)
        is_ru  = row.get("russia", False)
        if not cands:
            status = "NO_DATA"
            if row.get("jask_window"):
                status += " (window≥2024-06)"
            lines.append(f"| {row['terminal_id']} | {row['country']} | {row['n_tanks']} "
                         f"| — | — | 0 | — | — | {status} |")
            continue
        c = cands[0]
        cad_str = f"{c['cadence_days']:.1f}" if c['cadence_days'] else "—"
        if is_ref:
            status = "REFERENCE (read-only)"
        elif is_ru:
            status = "RU — defer; orbit recorded"
        elif c["coverage"] == "FULL":
            status = "WORKING"
        else:
            status = f"CANDIDATE ({c['coverage']})"
        lines.append(f"| {row['terminal_id']} | {row['country']} | {row['n_tanks']} "
                     f"| {c['relative_orbit']} | {c['direction'][:4].upper()} "
                     f"| {c['n_scenes']} | {cad_str} | {c['coverage']} | {status} |")

    lines += [
        "",
        "## All candidates (top 4 per terminal)",
        "",
        "```",
    ]
    for row in report_rows:
        if row.get("error") or not row.get("candidates"):
            continue
        lines.append(f"  {row['terminal_id']}")
        for c in row["candidates"][:4]:
            cad_str = f"{c['cadence_days']:.1f}d" if c['cadence_days'] else "—"
            lines.append(f"    rel={c['relative_orbit']} {c['direction'][:4].upper()} "
                         f"n={c['n_scenes']} cad={cad_str} cov={c['coverage']} "
                         f"({c['coverage_frac']*100:.0f}%)" if c['coverage_frac'] is not None
                         else f"    rel={c['relative_orbit']} {c['direction'][:4].upper()} "
                         f"n={c['n_scenes']} cad={cad_str} cov={c['coverage']}")
    lines.append("```")

    lines += [
        "",
        "## Notes",
        "",
        "**Jask**: survey window ≥ 2024-06-01 (construction before, not oil operations).  ",
        "**Russia cluster** (kozmino, de_kastri, primorsk, novorossiysk): orbits recorded above;  ",
        "activation deferred — separate cycle. Known issues:  ",
        "  - Arctic terminals (primorsk, novorossiysk): potential EW-mode scenes mixed with IW  ",
        "  - Ice season interference: kozmino/de_kastri winter months  ",
        "  - Sanctions-specific anchor constraints  ",
        "",
        "**Coverage definition**:  ",
        "  FULL    ≥ 95% of terminal bbox covered by scene footprint  ",
        "  PARTIAL 20–95%  ",
        "  EDGE    < 20% (likely artefact — cf. ras_tanura DESC-137)  ",
        "",
        f"**Registry patch**: `{patch_path.name}` — apply with `patch_registry_orbits.py`  ",
    ]

    md_path.write_text("\n".join(lines))
    print(f"  → ORBIT_SURVEY.md: {md_path}")


if __name__ == "__main__":
    main()
