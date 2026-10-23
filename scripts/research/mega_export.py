"""
mega_export.py — Full diagnostic export: Kharg / Ras Tanura / Yanbu.

Sections (run order per ТЗ):
  1  Registry & geometry (no API quota)
  2  Scene metadata (no quota — Supabase + v0_manifest)
  3.3 Evalscript & params (no quota — code inspection)
  5  Calibration & volume (no quota — Supabase + code)
  6  Anchors (no quota — Supabase + repo)
  4  Tank-scene metrics (SentinelHub quota — heavy)
  3.1-3.2 Crops & mean stack (SentinelHub quota — heaviest)

Usage:
  python scripts/research/mega_export.py --sections 1,2,3.3,5,6
  python scripts/research/mega_export.py --sections 4
  python scripts/research/mega_export.py --sections 3.1,3.2

Output: diagnostics/mega/  on research/2026-10-auto branch.
"""

import argparse
import csv
import gzip
import json
import math
import os
import subprocess
import sys
import textwrap
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT   = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

OUT = REPO_ROOT / "diagnostics" / "mega"
OUT.mkdir(parents=True, exist_ok=True)

SCRIPT_SHA = os.environ.get("GITHUB_SHA", "local")[:12]
RUN_ID     = os.environ.get("GITHUB_RUN_ID", "local")
TS         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

TARGET_TERMINALS = ["kharg", "ras_tanura", "yanbu"]

# ---------------------------------------------------------------------------
# Imports (lazy where quota-heavy)
# ---------------------------------------------------------------------------
from config import sb, S1_CDSE, config as sh_config, BARREL_PER_M3, TANK_HEIGHT_MIN

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------
def _get_registry():
    """Load LOCATIONS from main branch tank_registry.py (current checkout = research,
    but tank_registry.py was synced from main — confirmed identical for targets)."""
    from locations import LOCATIONS, raw_tanks, TERMINALS
    return LOCATIONS, raw_tanks, TERMINALS

# ---------------------------------------------------------------------------
# Supabase pagination helper
# ---------------------------------------------------------------------------
def sb_fetch_all(table, cols, filters=None, order=None):
    rows = []; offset = 0
    while True:
        q = sb.table(table).select(cols)
        if filters:
            for k, v in filters.items():
                if isinstance(v, list):
                    q = q.in_(k, v)
                else:
                    q = q.eq(k, v)
        if order:
            q = q.order(order)
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows

# ---------------------------------------------------------------------------
# Manifest loader
# ---------------------------------------------------------------------------
def load_v0_manifest():
    p = REPO_ROOT / "diagnostics/research/2026-10/event_csv/v0_manifest_all.csv"
    if not p.exists():
        print("  WARNING: v0_manifest_all.csv not found — scene metadata will be partial")
        return pd.DataFrame()
    return pd.read_csv(p)

# ===========================================================================
# SECTION 1 — Registry & geometry
# ===========================================================================
def section_1():
    print("\n=== Section 1: Registry & geometry ===")
    LOCATIONS, raw_tanks, TERMINALS = _get_registry()

    # ── 1.1 registry_full.csv ───────────────────────────────────────────────
    rows = []
    for loc in LOCATIONS:
        tid = loc["id"]
        if tid not in TARGET_TERMINALS:
            continue
        los = loc.get("los_azimuth", "")
        for t in loc["tanks"]:
            row = {
                "terminal_id": tid,
                "tank_id":     t["id"],
                "lat":         t["lat"],
                "lon":         t["lon"],
                "radius_m":    t["radius_m"],
                "h_max_m":     t["h_max_m"],
                "enabled":     t.get("enabled", True),
                "wl_step":     t.get("wl_step", ""),
                "wl_date":     t.get("wl_date", ""),
                "wl_n_scenes": t.get("wl_n_scenes", ""),
                "los_azimuth": los,
                "geom_source": t.get("geom_source", ""),
                "h_source":    "api650",
                "uncertain":   t.get("uncertain", False),
                "notes":       t.get("notes", t.get("note", "")),
            }
            rows.append(row)

    p = OUT / "registry_full.csv"
    _write_csv(p, rows)
    print(f"  1.1 registry_full.csv: {len(rows)} rows → {p}")

    # ── 1.2 registry_history.csv ────────────────────────────────────────────
    print("  1.2 registry_history.csv: scanning git log…")
    history = []
    try:
        log = subprocess.check_output(
            ["git", "log", "--follow", "--diff-filter=M",
             "--format=%H%x09%ai%x09%s",
             "scripts/tank_registry.py"],
            cwd=str(REPO_ROOT), text=True
        ).strip().splitlines()

        for line in log:
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            sha, date_str = parts[0], parts[1]
            # get diff for this commit
            try:
                diff = subprocess.check_output(
                    ["git", "show", "--unified=0", sha, "--", "scripts/tank_registry.py"],
                    cwd=str(REPO_ROOT), text=True
                )
            except Exception:
                continue
            for dline in diff.splitlines():
                if not dline.startswith(("+", "-")) or dline.startswith(("+++", "---")):
                    continue
                sign = "+" if dline.startswith("+") else "-"
                content = dline[1:].strip()
                if not content:
                    continue
                history.append({
                    "sha": sha[:12],
                    "date": date_str[:10],
                    "sign": sign,
                    "content": content[:200],
                })
    except Exception as e:
        print(f"  WARNING: git log failed: {e}")
        history = [{"sha": "N/A", "date": "N/A", "sign": "", "content": str(e)}]

    ph = OUT / "registry_history.csv"
    _write_csv(ph, history)
    print(f"  1.2 registry_history.csv: {len(history)} diff lines → {ph}")

    # ── 1.3 esri_crops/ ─────────────────────────────────────────────────────
    print("  1.3 esri_crops/: generating per-tank PNGs via ESRI tile API…")
    _gen_esri_crops(rows)

    # ── 1.4 source_points ───────────────────────────────────────────────────
    print("  1.4 source_points: searching repo…")
    _dump_source_points()

    print("  Section 1 done.")


def _write_csv(path, rows, fieldnames=None):
    if not rows:
        path.write_text("")
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _gen_esri_crops(registry_rows):
    """Download ESRI World Imagery tiles and compose per-tank crops + farm overview."""
    import io
    try:
        import requests
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("    Pillow/requests not available — skipping ESRI crops")
        return

    crops_dir = OUT / "esri_crops"
    crops_dir.mkdir(exist_ok=True)
    ZOOM = 18
    TILE_SIZE = 256

    def _latlng_to_tile(lat, lon, z):
        n = 2 ** z
        xtile = int((lon + 180) / 360 * n)
        ytile = int((1 - math.log(math.tan(math.radians(lat)) +
                                   1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
        return xtile, ytile

    def _tile_to_latlng(x, y, z):
        n = 2 ** z
        lon = x / n * 360 - 180
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        return math.degrees(lat_rad), lon

    def _fetch_tile(x, y, z):
        url = (f"https://services.arcgisonline.com/ArcGIS/rest/services/"
               f"World_Imagery/MapServer/tile/{z}/{y}/{x}")
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            pass
        return Image.new("RGB", (TILE_SIZE, TILE_SIZE), (50, 50, 50))

    def _meters_per_pixel(lat, z):
        return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)

    by_terminal = {}
    for row in registry_rows:
        by_terminal.setdefault(row["terminal_id"], []).append(row)

    for tid, tanks in by_terminal.items():
        print(f"    {tid}: {len(tanks)} tanks…")
        # Farm overview
        lats = [t["lat"] for t in tanks]
        lons = [t["lon"] for t in tanks]
        clat = sum(lats) / len(lats)
        clon = sum(lons) / len(lons)
        max_r = max(t["radius_m"] for t in tanks)
        buf_deg = (max(
            max(lats) - min(lats),
            max(lons) - min(lons)
        ) * 0.6 + 0.003)

        tx0, ty0 = _latlng_to_tile(clat + buf_deg, clon - buf_deg, ZOOM)
        tx1, ty1 = _latlng_to_tile(clat - buf_deg, clon + buf_deg, ZOOM)
        tx0, tx1 = min(tx0, tx1), max(tx0, tx1)
        ty0, ty1 = min(ty0, ty1), max(ty0, ty1)
        nx = tx1 - tx0 + 1
        ny = ty1 - ty0 + 1
        if nx * ny > 25:
            step = math.ceil(math.sqrt(nx * ny / 25))
            nx = math.ceil(nx / step)
            ny = math.ceil(ny / step)
            tx1 = tx0 + nx - 1
            ty1 = ty0 + ny - 1

        farm_img = Image.new("RGB", (nx * TILE_SIZE, ny * TILE_SIZE))
        for xi in range(nx):
            for yi in range(ny):
                tile = _fetch_tile(tx0 + xi, ty0 + yi, ZOOM)
                farm_img.paste(tile, (xi * TILE_SIZE, yi * TILE_SIZE))

        draw = ImageDraw.Draw(farm_img)
        mpp = _meters_per_pixel(clat, ZOOM)
        origin_lat, origin_lon = _tile_to_latlng(tx0, ty0, ZOOM)
        lat_deg_per_px = (origin_lat - _tile_to_latlng(tx0, ty0 + ny, ZOOM)[0]) / (ny * TILE_SIZE)
        lon_deg_per_px = (_tile_to_latlng(tx0 + nx, ty0, ZOOM)[1] - origin_lon) / (nx * TILE_SIZE)

        for t in tanks:
            px = int((t["lon"] - origin_lon) / lon_deg_per_px)
            py = int((origin_lat - t["lat"]) / lat_deg_per_px)
            r_px = max(2, int(t["radius_m"] / mpp))
            draw.ellipse([px - r_px, py - r_px, px + r_px, py + r_px],
                         outline=(255, 50, 50), width=2)
            draw.text((px + r_px + 2, py - 6), t["tank_id"], fill=(255, 255, 0))

        farm_img.save(crops_dir / f"farm_{tid}.png")

        # Per-tank crops (3 diameters window)
        for t in tanks:
            try:
                buf_m = t["radius_m"] * 3
                buf_lat = buf_m / 111320
                buf_lon = buf_m / (111320 * math.cos(math.radians(t["lat"])))
                tx0t, ty0t = _latlng_to_tile(t["lat"] + buf_lat, t["lon"] - buf_lon, ZOOM)
                tx1t, ty1t = _latlng_to_tile(t["lat"] - buf_lat, t["lon"] + buf_lon, ZOOM)
                tx0t, tx1t = min(tx0t, tx1t), max(tx0t, tx1t)
                ty0t, ty1t = min(ty0t, ty1t), max(ty0t, ty1t)
                nxt = tx1t - tx0t + 1
                nyt = ty1t - ty0t + 1
                if nxt * nyt > 16:
                    nxt = nyt = 4
                    tx1t = tx0t + 3
                    ty1t = ty0t + 3
                tank_img = Image.new("RGB", (nxt * TILE_SIZE, nyt * TILE_SIZE))
                for xi in range(nxt):
                    for yi in range(nyt):
                        tile = _fetch_tile(tx0t + xi, ty0t + yi, ZOOM)
                        tank_img.paste(tile, (xi * TILE_SIZE, yi * TILE_SIZE))
                dt = ImageDraw.Draw(tank_img)
                mpp_t = _meters_per_pixel(t["lat"], ZOOM)
                olat, olon = _tile_to_latlng(tx0t, ty0t, ZOOM)
                lpp = (olat - _tile_to_latlng(tx0t, ty0t + nyt, ZOOM)[0]) / (nyt * TILE_SIZE)
                lopp = (_tile_to_latlng(tx0t + nxt, ty0t, ZOOM)[1] - olon) / (nxt * TILE_SIZE)
                px = int((t["lon"] - olon) / lopp)
                py = int((olat - t["lat"]) / lpp)
                r_px = max(2, int(t["radius_m"] / mpp_t))
                dt.ellipse([px - r_px, py - r_px, px + r_px, py + r_px],
                           outline=(255, 50, 50), width=2)
                dt.text((4, 4), t["tank_id"], fill=(255, 255, 0))
                tank_img.save(crops_dir / f"{t['tank_id']}.png")
            except Exception as e:
                print(f"    WARNING crop {t['tank_id']}: {e}")


def _dump_source_points():
    """Document the origin of tank coordinates in the registry."""
    out_p = OUT / "source_points.txt"
    lines = []

    # A: geom_source field values from registry
    try:
        from locations import LOCATIONS
        lines.append("=== geom_source per tank (from locations.py / tank_registry.py) ===")
        seen = {}
        for loc in LOCATIONS:
            tid = loc.get("id", "")
            if tid not in TARGET_TERMINALS:
                continue
            for t in loc.get("tanks", []):
                gs = t.get("geom_source", "") or t.get("source", "")
                key = (tid, gs or "(none)")
                seen[key] = seen.get(key, 0) + 1
        if seen:
            for (tid, gs), cnt in sorted(seen.items()):
                lines.append(f"  {tid}: {cnt} tanks with geom_source='{gs}'")
        else:
            lines.append("  (geom_source field not present in registry)")
    except Exception as e:
        lines.append(f"  WARNING: {e}")

    # B: esri_measure* workflow files → these encode initial ESRI-measured centroids
    lines.append("\n=== esri_measure workflows (initial centroid measurement runs) ===")
    try:
        wf_dir = REPO_ROOT / ".github" / "workflows"
        esri_wfs = sorted(wf_dir.glob("esri_measure*.yml"))
        if esri_wfs:
            for wf in esri_wfs:
                lines.append(f"  {wf.name}")
        else:
            lines.append("  (none found)")
    except Exception as e:
        lines.append(f"  WARNING: {e}")

    # C: git log — commits that introduced tank coordinates
    lines.append("\n=== git log: commits touching tank_registry.py / locations.py ===")
    try:
        for fname in ("scripts/tank_registry.py", "scripts/locations.py"):
            r = subprocess.check_output(
                ["git", "log", "--oneline", "--follow", fname],
                cwd=str(REPO_ROOT), text=True, stderr=subprocess.DEVNULL
            ).strip()
            lines.append(f"  {fname}:")
            lines.extend(f"    {l}" for l in (r.splitlines() or ["(none)"]))
    except Exception:
        lines.append("  (git log failed)")

    # D: diagnostics/research esri_measure result files
    lines.append("\n=== esri_measure result files in diagnostics/ ===")
    try:
        diag = REPO_ROOT / "diagnostics"
        measure_files = sorted(diag.rglob("esri_measure*.csv")) + \
                        sorted(diag.rglob("*centroids*.csv")) + \
                        sorted(diag.rglob("*centers*.csv"))
        if measure_files:
            for mf in measure_files[:30]:
                lines.append(f"  {mf.relative_to(REPO_ROOT)}")
        else:
            lines.append("  (none found)")
    except Exception as e:
        lines.append(f"  WARNING: {e}")

    out_p.write_text("\n".join(lines))
    print(f"  1.4 source_points.txt → {out_p}")


# ===========================================================================
# SECTION 2 — Scene metadata
# ===========================================================================
def section_2():
    print("\n=== Section 2: Scene metadata ===")
    manifest = load_v0_manifest()

    # DB dates per terminal
    db_dates = {}
    for tid in TARGET_TERMINALS:
        tbl = "raw_observations" if tid == "kharg" else "terminal_observations"
        try:
            rows = sb_fetch_all(tbl, "date", filters={"terminal_id": tid}, order="date")
            db_dates[tid] = set(r["date"][:10] for r in rows)
        except Exception as e:
            print(f"  WARNING {tid} DB fetch: {e}")
            db_dates[tid] = set()
        print(f"  {tid}: {len(db_dates[tid])} dates in DB")

    if manifest.empty:
        print("  WARNING: v0_manifest_all.csv missing — scene metadata will lack catalog info")
        rows_out = []
        for tid in TARGET_TERMINALS:
            for d in sorted(db_dates[tid]):
                rows_out.append({
                    "terminal": tid, "scene_id": "", "datetime_utc": d + "T00:00:00Z",
                    "date_in_db": d, "relOrbit": "", "direction": "", "platform": "",
                    "incidence_angle_center": "", "heading": "", "in_db": "yes",
                    "ingest_path": "raw_observations" if tid == "kharg" else "terminal_observations",
                })
    else:
        rows_out = []
        cols_available = set(manifest.columns)
        for _, row in manifest.iterrows():
            tid = str(row.get("terminal_id", ""))
            if tid not in TARGET_TERMINALS:
                continue
            date_str = str(row.get("date", ""))[:10]
            in_db = "yes" if date_str in db_dates.get(tid, set()) else "no"
            rows_out.append({
                "terminal":               tid,
                "scene_id":               row.get("scene_id", row.get("id", "")),
                "datetime_utc":           row.get("datetime", row.get("date", "")),
                "date_in_db":             date_str,
                "relOrbit":               row.get("relOrbit", row.get("relative_orbit", "")),
                "direction":              row.get("direction", row.get("orbit_direction", "")),
                "platform":               row.get("platform", ""),
                "incidence_angle_center": row.get("incidence_angle", ""),
                "heading":                row.get("heading", ""),
                "in_db":                  in_db,
                "ingest_path":            "raw_observations" if tid == "kharg" else "terminal_observations",
            })

        # Add DB dates not in manifest
        manifest_dates = {(str(r.get("terminal_id", "")), str(r.get("date", ""))[:10])
                          for _, r in manifest.iterrows()}
        for tid in TARGET_TERMINALS:
            for d in sorted(db_dates[tid]):
                if (tid, d) not in manifest_dates:
                    rows_out.append({
                        "terminal": tid, "scene_id": "DB_ONLY", "datetime_utc": d,
                        "date_in_db": d, "relOrbit": "", "direction": "",
                        "platform": "", "incidence_angle_center": "", "heading": "",
                        "in_db": "yes",
                        "ingest_path": "raw_observations" if tid == "kharg" else "terminal_observations",
                    })

    p = OUT / "scenes.csv"
    _write_csv(p, rows_out)
    print(f"  2.1 scenes.csv: {len(rows_out)} rows → {p}")
    print("  Section 2 done.")


# ===========================================================================
# SECTION 3.3 — Evalscript & params
# ===========================================================================
def section_3_3():
    print("\n=== Section 3.3: Evalscript & params ===")
    sar_fetch_path = SCRIPTS_DIR / "sar_fetch.py"
    text = sar_fetch_path.read_text()

    # Extract evalscript
    es_start = text.find('_EVALSCRIPT = """')
    es_end   = text.find('"""', es_start + len('_EVALSCRIPT = """')) + 3
    evalscript_raw = text[es_start:es_end] if es_start != -1 else "(not found)"

    # Extract processing params
    import re
    proc_block = re.search(r'"processing":\s*\{[^}]+\}', text)
    proc_str = proc_block.group(0) if proc_block else "(not found)"

    # Extract other_args dataFilter block
    filter_block = re.search(r'"dataFilter":\s*\{[^}]+\}', text)
    filter_str = filter_block.group(0) if filter_block else "(not found)"

    from config import RESOLUTION, BUFFER_METERS
    try:
        from locations import get_super_bbox, TERMINALS
        bbox_info = {}
        for tid in TARGET_TERMINALS:
            if tid in TERMINALS:
                bb = get_super_bbox(TERMINALS[tid])
                bbox_info[tid] = f"({bb.min_x:.4f},{bb.min_y:.4f}) → ({bb.max_x:.4f},{bb.max_y:.4f})"
            else:
                bbox_info[tid] = "N/A"
    except Exception as e:
        bbox_info = {t: f"ERROR: {e}" for t in TARGET_TERMINALS}

    out_txt = textwrap.dedent(f"""
    # evalscript_and_params.txt
    # Generated: {TS}
    # SHA: {SCRIPT_SHA}  Run: {RUN_ID}
    # Source: scripts/sar_fetch.py

    ## Evalscript (lines {_find_line(text, '_EVALSCRIPT')}):
    {evalscript_raw}

    ## Request parameters:
    - data_collection: S1_CDSE (DataCollection.SENTINEL1_IW, service_url=https://sh.dataspace.copernicus.eu)
    - CRS: WGS84 (bbox in lon/lat)
    - resx / resy: {RESOLUTION} m/px (RESOLUTION from config.py)
    - upsampling: default (BILINEAR in SentinelHub SDK)
    - mosaicking: ORBIT (when orbit_cfg set), else default
    - backCoeff: GAMMA0_TERRAIN
    - orthorectify: True
    - demInstance: COPERNICUS_30
    - timeRange: [date, date+1day] per pass
    - polarization: SV (single VV — ship_backfill.py fix applied 2026-10)

    ## Per-terminal BBox (WGS84, BUFFER_METERS={BUFFER_METERS}m):
    """)
    for tid, bb in bbox_info.items():
        out_txt += f"  {tid}: {bb}\n"

    out_txt += f"""
    ## Processing block (sar_fetch.py):
    {proc_str}

    ## DataFilter block (orbit-specific fetches):
    {filter_str}

    ## Key file:line references:
    - evalscript:         scripts/sar_fetch.py:{_find_line(text, '_EVALSCRIPT')}
    - orthorectify:       scripts/sar_fetch.py:{_find_line(text, 'orthorectify')}
    - backCoeff:          scripts/sar_fetch.py:{_find_line(text, 'backCoeff')}
    - demInstance:        scripts/sar_fetch.py:{_find_line(text, 'demInstance')}
    - RESOLUTION:         scripts/config.py (RESOLUTION={RESOLUTION})
    - BUFFER_METERS:      scripts/config.py (BUFFER_METERS={BUFFER_METERS})
    - LOS_AZIMUTH:        scripts/config.py (LOS_AZIMUTH=282 for DESC)
    - WIDTH_WIDE:         scripts/config.py (WIDTH_WIDE=15m)
    - fill_ratio formula: scripts/volume.py:compute_volumes
    - dB_min/dB_max clip: scripts/volume.py (quantile 0.05/0.95, global)
    - TANK_HEIGHT_MIN:    scripts/config.py (TANK_HEIGHT_MIN=2m)
    - BARREL_PER_M3:      scripts/config.py (BARREL_PER_M3=6.2898)
    """

    p = OUT / "evalscript_and_params.txt"
    p.write_text(out_txt)
    print(f"  3.3 evalscript_and_params.txt → {p}")
    print("  Section 3.3 done.")


def _find_line(text, pattern):
    for i, line in enumerate(text.splitlines(), 1):
        if pattern in line:
            return i
    return "?"


# ===========================================================================
# SECTION 5 — Calibration & volume
# ===========================================================================
def section_5():
    print("\n=== Section 5: Calibration & volume ===")
    from volume import compute_volumes

    # ── 5.1 prod_tank_series.csv ────────────────────────────────────────────
    print("  5.1 prod_tank_series.csv…")
    all_rows = []
    for tid in TARGET_TERMINALS:
        tbl = "raw_observations" if tid == "kharg" else "terminal_observations"
        try:
            rows = sb_fetch_all(tbl, "date,tank_id,db_mean,radius_m,h_max",
                                filters={"terminal_id": tid}, order="date")
        except Exception as e:
            print(f"  WARNING {tid}: {e}")
            continue

        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"])
        df["radius_m"] = pd.to_numeric(df["radius_m"], errors="coerce")
        df["h_max"]    = pd.to_numeric(df["h_max"], errors="coerce")
        df["db_mean"]  = pd.to_numeric(df["db_mean"], errors="coerce")
        df = df.dropna(subset=["db_mean", "radius_m", "h_max"])

        # per_orbit_calibration for RT
        if tid == "ras_tanura" and "orbit_direction" in df.columns:
            result = compute_volumes(df.copy(), per_orbit_calibration=True)
            df2, dBmin, dBmax, cal_map = result
        else:
            df2, dBmin, dBmax = compute_volumes(df.copy())
            cal_map = {"global": (dBmin, dBmax)}

        df2["terminal_id"]   = tid
        df2["dB_min"]        = dBmin
        df2["dB_max"]        = dBmax
        df2["fill_ratio"]    = df2["db_mean"].apply(
            lambda x: (dBmax - np.clip(x, dBmin, dBmax)) / (dBmax - dBmin))
        df2["h_m"]           = TANK_HEIGHT_MIN + df2["fill_ratio"] * (df2["h_max"] - TANK_HEIGHT_MIN)
        df2["vol_bbl"]       = df2["barrels"]
        df2["calib_group"]   = tid if tid != "ras_tanura" else df2.get(
            "orbit_direction", pd.Series(["global"] * len(df2))).fillna("global")

        cols = ["terminal_id", "tank_id", "date", "db_mean", "calib_group",
                "dB_min", "dB_max", "fill_ratio", "h_m", "vol_bbl"]
        for c in cols:
            if c not in df2.columns:
                df2[c] = ""
        all_rows.append(df2[cols])

    if all_rows:
        combined = pd.concat(all_rows, ignore_index=True)
        combined["date"] = combined["date"].astype(str).str[:10]
        p = OUT / "prod_tank_series.csv"
        combined.to_csv(p, index=False)
        print(f"  5.1 prod_tank_series.csv: {len(combined)} rows → {p}")
    else:
        print("  5.1 EMPTY")

    # ── 5.2 calibration_code/ ───────────────────────────────────────────────
    print("  5.2 calibration_code/…")
    calib_dir = OUT / "calibration_code"
    calib_dir.mkdir(exist_ok=True)
    for fname in ["sar_fetch.py", "volume.py", "compute.py", "config.py",
                  "locations.py", "export.py", "main_script.py"]:
        src = SCRIPTS_DIR / fname
        if src.exists():
            dst = calib_dir / fname
            dst.write_text(src.read_text())
    print(f"  5.2 calibration_code/: {len(list(calib_dir.iterdir()))} files → {calib_dir}")

    # ── 5.3 tvt_all.csv ─────────────────────────────────────────────────────
    print("  5.3 tvt_all.csv…")
    tvt_rows = []
    # Kharg: volume_timeseries
    try:
        kr = sb_fetch_all("volume_timeseries", "*", order="date")
        for r in kr:
            r["terminal_id"] = "kharg"
        tvt_rows.extend(kr)
    except Exception as e:
        print(f"  WARNING kharg tvt: {e}")
    # RT/Yanbu: terminal_volume_timeseries
    for tid in ["ras_tanura", "yanbu"]:
        try:
            tr = sb_fetch_all("terminal_volume_timeseries", "*",
                              filters={"terminal_id": tid}, order="date")
            tvt_rows.extend(tr)
        except Exception as e:
            print(f"  WARNING {tid} tvt: {e}")

    # n_tanks per date per terminal from raw/terminal_observations
    tank_counts = {}
    for tid in TARGET_TERMINALS:
        tbl = "raw_observations" if tid == "kharg" else "terminal_observations"
        try:
            rows = sb_fetch_all(tbl, "date,tank_id", filters={"terminal_id": tid})
            for r in rows:
                key = (tid, r["date"][:10])
                tank_counts[key] = tank_counts.get(key, set())
                tank_counts[key].add(r["tank_id"])
        except Exception:
            pass

    for r in tvt_rows:
        tid = r.get("terminal_id", "kharg")
        d   = str(r.get("date", ""))[:10]
        r["n_tanks"] = len(tank_counts.get((tid, d), set()))

    if tvt_rows:
        p5 = OUT / "tvt_all.csv"
        _write_csv(p5, tvt_rows)
        print(f"  5.3 tvt_all.csv: {len(tvt_rows)} rows → {p5}")
    print("  Section 5 done.")


# ===========================================================================
# SECTION 6 — Anchors
# ===========================================================================
def section_6():
    print("\n=== Section 6: Anchors ===")
    anc_dir = OUT / "anchors_all"
    anc_dir.mkdir(exist_ok=True)

    # ── 6.1 dump everything from repo anchors/ ───────────────────────────────
    anchor_dirs = ["data/anchors", "anchors", "scripts/anchors", "data"]
    found_files = []
    for adir in anchor_dirs:
        p = REPO_ROOT / adir
        if p.is_dir():
            for f in p.rglob("*.csv"):
                if any(k in f.name.lower() for k in
                       ["anchor", "portwatch", "jodi", "uani", "ship", "hormuz", "export", "mbpd"]):
                    found_files.append(f)
            for f in p.rglob("*.json"):
                if any(k in f.name.lower() for k in ["anchor", "portwatch"]):
                    found_files.append(f)

    for f in found_files:
        dst = anc_dir / f.name
        dst.write_bytes(f.read_bytes())
        print(f"  copied {f.relative_to(REPO_ROOT)} → anchors_all/{f.name}")

    # event_csv anchors
    ec_dir = REPO_ROOT / "diagnostics/research/2026-10/event_csv"
    if (ec_dir / "anchors.csv").exists():
        import shutil
        shutil.copy(ec_dir / "anchors.csv", anc_dir / "anchors_event_csv.csv")
        if (ec_dir / "missing_anchors.txt").exists():
            shutil.copy(ec_dir / "missing_anchors.txt", anc_dir / "missing_anchors.txt")
        print("  copied anchors.csv + missing_anchors.txt from event_csv/")

    # Supabase: any anchor/reference tables
    for tbl_name in ["anchors", "export_anchors", "portwatch_anchors", "jodi_data"]:
        try:
            rows = sb_fetch_all(tbl_name, "*")
            if rows:
                p = anc_dir / f"sb_{tbl_name}.csv"
                _write_csv(p, rows)
                print(f"  Supabase {tbl_name}: {len(rows)} rows → {p}")
        except Exception:
            pass

    # ── 6.2 anchor_mapping.txt ───────────────────────────────────────────────
    mapping_lines = ["# anchor_mapping.txt — anchor-to-terminal wiring in codebase", ""]
    anchor_keywords = ["portwatch", "anchor", "jodi", "uani", "ship", "mbpd", "lag", "hormuz"]
    for fname in sorted((SCRIPTS_DIR).rglob("*.py")):
        try:
            text = fname.read_text()
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(k in line.lower() for k in anchor_keywords):
                rel = fname.relative_to(REPO_ROOT)
                mapping_lines.append(f"{rel}:{i}: {line.rstrip()}")
    p62 = OUT / "anchor_mapping.txt"
    p62.write_text("\n".join(mapping_lines))
    print(f"  6.2 anchor_mapping.txt: {len(mapping_lines)} lines → {p62}")

    # ── 6.3 past_results/ ───────────────────────────────────────────────────
    past_dir = OUT / "past_results"
    past_dir.mkdir(exist_ok=True)
    # Search for correlation result CSVs in diagnostics/
    diag = REPO_ROOT / "diagnostics"
    corr_files = []
    if diag.exists():
        for f in diag.rglob("*.csv"):
            if any(k in f.name.lower() for k in ["corr", "result", "r_kharg", "yanbu", "series"]):
                corr_files.append(f)
        for f in diag.rglob("*.txt"):
            if "run_block" in f.name.lower() or "result" in f.name.lower():
                corr_files.append(f)
    for f in corr_files[:50]:
        import shutil
        try:
            shutil.copy(f, past_dir / f.name)
        except Exception:
            pass
    # Also copy run_blockA.txt if present
    block_a = REPO_ROOT / "diagnostics/research/2026-10/run_blockA.txt"
    if block_a.exists():
        import shutil
        shutil.copy(block_a, past_dir / "run_blockA.txt")

    print(f"  6.3 past_results/: {len(list(past_dir.iterdir()))} files → {past_dir}")
    print("  Section 6 done.")


# ===========================================================================
# SECTION 4 — Tank-scene metrics (SentinelHub quota)
# ===========================================================================
def section_4(only_terminal=None):
    """only_terminal: one of 'kharg', 'ras_tanura', 'yanbu', or None for all."""
    label = only_terminal or "all"
    print(f"\n=== Section 4: Tank-scene metrics (quota-heavy) — terminal={label} ===")
    from sentinelhub import BBox, CRS, SentinelHubRequest, MimeType
    from datetime import timedelta as td
    from locations import LOCATIONS, TERMINALS

    # Load V0 manifest for scene selection
    manifest = load_v0_manifest()

    run_terminals = [only_terminal] if only_terminal else TARGET_TERMINALS

    TARGET_ORBITS = {
        "kharg":      [{"direction": "DESC", "relOrbit": 35}],
        "ras_tanura": [{"direction": "DESC", "relOrbit": 35},
                       {"direction": "ASC",  "relOrbit": 101}],
        "yanbu":      [{"direction": "ASC",  "relOrbit": 14}],
    }

    # evalscript returning raw VV linear
    EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""

    def _crescent_mask(cx, cy, r, los_az_deg, width_m, res, half_angle_deg=90):
        """Create crescent mask at pixel coords cx,cy radius r pixels."""
        H, W = cy * 2 + 10, cx * 2 + 10
        y, x = np.mgrid[0:H, 0:W]
        dist = np.sqrt((x - cx)**2 + (y - cy)**2)
        ring = (dist >= r - width_m/res) & (dist <= r)
        az_rad = math.radians(los_az_deg)
        angle_from_center = np.arctan2(-(y - cy), x - cx)
        los_rad = math.radians(90 - los_az_deg)
        diff = np.abs(((angle_from_center - los_rad + math.pi) % (2 * math.pi)) - math.pi)
        sector = diff <= math.radians(half_angle_deg)
        return ring & sector

    def _ring_mask(cx, cy, r_inner, r_outer, H, W):
        y, x = np.mgrid[0:H, 0:W]
        dist = np.sqrt((x - cx)**2 + (y - cy)**2)
        return (dist >= r_inner) & (dist <= r_outer)

    def _sector_mask(cx, cy, r, az_center_deg, half_deg, res, H, W):
        width_px = 15 / res
        y, x = np.mgrid[0:H, 0:W]
        dist = np.sqrt((x - cx)**2 + (y - cy)**2)
        ring = (dist >= r - width_px) & (dist <= r)
        angle = np.arctan2(-(y - cy), x - cx)
        az_rad = math.radians(90 - az_center_deg)
        diff = np.abs(((angle - az_rad + math.pi) % (2 * math.pi)) - math.pi)
        return ring & (diff <= math.radians(half_deg))

    def _safe_db(arr):
        vals = arr[np.isfinite(arr) & (arr > 0)]
        if len(vals) == 0:
            return np.nan
        return float(10 * np.log10(np.mean(vals)))

    def _safe_db_pixels(arr):
        """mean of per-pixel dB (not mean_lin→dB)"""
        vals = arr[np.isfinite(arr) & (arr > 0)]
        if len(vals) == 0:
            return np.nan
        return float(np.mean(10 * np.log10(vals)))

    RES = 10  # 10 m/px for section 4
    BUFFER = 300

    record_rows = []
    profile_data = {}  # {terminal: {tank_id: {scene_id: array}}}

    for tid in run_terminals:
        loc = next((l for l in LOCATIONS if l["id"] == tid), None)
        if not loc:
            continue
        los_az = loc.get("los_azimuth", 282)
        tanks  = loc["tanks"]

        # Compute farm bbox
        lats = [t["lat"] for t in tanks]
        lons = [t["lon"] for t in tanks]
        bbox = BBox([min(lons) - BUFFER/111320, min(lats) - BUFFER/111320,
                     max(lons) + BUFFER/111320, max(lats) + BUFFER/111320], crs=CRS.WGS84)

        W_px = int((bbox.max_x - bbox.min_x) / (RES / 111320))
        H_px = int((bbox.max_y - bbox.min_y) / (RES / 111320))

        profile_data[tid] = {}

        orbits = TARGET_ORBITS[tid]
        for orb in orbits:
            dir_str = orb["direction"]
            rel_orb = orb["relOrbit"]

            # Get scene list from manifest
            if not manifest.empty:
                mf = manifest[manifest["terminal_id"] == tid]
                mf = mf[mf["relOrbit"].astype(str) == str(rel_orb)]
                dates = sorted(mf["date"].str[:10].unique())
            else:
                # Fall back to DB dates
                tbl = "raw_observations" if tid == "kharg" else "terminal_observations"
                rows = sb_fetch_all(tbl, "date", filters={"terminal_id": tid})
                dates = sorted(set(r["date"][:10] for r in rows))

            if not dates:
                print(f"  {tid}/{dir_str}-{rel_orb}: 0 dates, skipping")
                continue
            print(f"  {tid}/{dir_str}-{rel_orb}: {len(dates)} dates to process…")

            api_dir = "DESCENDING" if dir_str.upper() in ("DESC", "DESCENDING") else "ASCENDING"

            for d_str in dates:
                d = datetime.strptime(d_str, "%Y-%m-%d")
                try:
                    req = SentinelHubRequest(
                        evalscript=EVALSCRIPT,
                        input_data=[SentinelHubRequest.input_data(
                            data_collection=S1_CDSE,
                            time_interval=(d_str, (d + td(days=1)).strftime("%Y-%m-%d")),
                            other_args={
                                "processing": {
                                    "orthorectify": True,
                                    "backCoeff":    "GAMMA0_TERRAIN",
                                    "demInstance":  "COPERNICUS_30",
                                },
                                "dataFilter": {
                                    "orbitDirection":    api_dir,
                                    "relativeOrbitNumber": rel_orb,
                                },
                                "mosaicking": "ORBIT",
                            },
                        )],
                        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                        bbox=bbox,
                        size=(W_px, H_px),
                        config=sh_config,
                    )
                    data = req.get_data()
                    if not data:
                        continue
                    img = data[0].squeeze().astype(np.float32)
                except Exception as e:
                    print(f"    {d_str}: SKIP ({e})")
                    continue

                scene_id = f"{tid}_{dir_str}{rel_orb}_{d_str}"

                for tank in tanks:
                    # pixel coords
                    cx = int((tank["lon"] - bbox.min_x) / (bbox.max_x - bbox.min_x) * W_px)
                    cy = int((bbox.max_y - tank["lat"]) / (bbox.max_y - bbox.min_y) * H_px)
                    r  = tank["radius_m"] / RES
                    H, W = img.shape

                    if cx < 0 or cy < 0 or cx >= W or cy >= H:
                        continue

                    def _safe_crop(mask):
                        try:
                            mh, mw = mask.shape
                            mh2, mw2 = min(mh, H), min(mw, W)
                            return img[:mh2, :mw2][mask[:mh2, :mw2]]
                        except Exception:
                            return np.array([])

                    w15 = 15 / RES
                    w10 = 10 / RES
                    w25 = 25 / RES

                    # masks
                    disc_full_mask   = _ring_mask(cx, cy, 0, r, H, W)
                    disc_inner_mask  = _ring_mask(cx, cy, 0, r - w15, H, W)
                    ring15_all_mask  = _ring_mask(cx, cy, r - w15, r, H, W)
                    ring15_far_mask  = _crescent_mask(cx, cy, r, los_az, 15, RES, 90)
                    ring15_near_mask = _crescent_mask(cx, cy, r, (los_az + 180) % 360, 15, RES, 90)
                    ring10_far_mask  = _crescent_mask(cx, cy, r, los_az, 10, RES, 90)
                    ring25_far_mask  = _crescent_mask(cx, cy, r, los_az, 25, RES, 90)

                    try:
                        mh, mw = ring15_far_mask.shape
                        mh2, mw2 = min(mh, H), min(mw, W)
                        valid_frac = (float(np.isfinite(img[:mh2, :mw2][ring15_far_mask[:mh2, :mw2]]).sum()) /
                                      max(1, int(ring15_far_mask[:mh2, :mw2].sum())))
                    except Exception:
                        valid_frac = np.nan

                    # 8 sector masks (45° each from N clockwise)
                    sector_vals = {}
                    for si in range(8):
                        az_c = si * 45
                        sm = _sector_mask(cx, cy, r, az_c, 22.5, RES, H, W)
                        sector_vals[f"ring15_s{si}"] = _safe_db(_safe_crop(sm))

                    # offset grid for ring15_far (dx,dy ∈ {-40,-20,0,+20,+40} m)
                    offset_vals = {}
                    for dx_m in (-40, -20, 0, 20, 40):
                        for dy_m in (-40, -20, 0, 20, 40):
                            cx2 = cx + int(dx_m / RES)
                            cy2 = cy + int(dy_m / RES)
                            om = _crescent_mask(cx2, cy2, r, los_az, 15, RES, 90)
                            offset_vals[f"off_{dx_m:+d}_{dy_m:+d}"] = _safe_db(_safe_crop(om))

                    # r×0.8 and r×1.2
                    ring15_far_r08_mask = _crescent_mask(cx, cy, r * 0.8, los_az, 15, RES, 90)
                    ring15_far_r12_mask = _crescent_mask(cx, cy, r * 1.2, los_az, 15, RES, 90)

                    # bg ring
                    bg_mask = _ring_mask(cx, cy, r + 10/RES, r + 30/RES, H, W)

                    # range profile along LOS through center
                    profile_len = int(1.5 * r)
                    az_rad = math.radians(90 - los_az)
                    profile_pts = np.linspace(-profile_len, profile_len, int(2 * profile_len * RES / 2.5))
                    prof_vals = []
                    for pp in profile_pts:
                        px_f = cx + pp * math.cos(az_rad)
                        py_f = cy - pp * math.sin(az_rad)
                        px_i, py_i = int(round(px_f)), int(round(py_f))
                        if 0 <= px_i < W and 0 <= py_i < H:
                            v = img[py_i, px_i]
                            prof_vals.append(v if np.isfinite(v) and v > 0 else np.nan)
                        else:
                            prof_vals.append(np.nan)
                    prof_arr = np.array(prof_vals, dtype=np.float32)
                    prof_db  = np.where(np.isfinite(prof_arr) & (prof_arr > 0),
                                        10 * np.log10(prof_arr), np.nan)
                    if np.any(np.isfinite(prof_db)):
                        peak_idx    = int(np.nanargmax(prof_db))
                        R_pos_m     = float(profile_pts[peak_idx] * RES)
                        R_max_dB    = float(prof_db[peak_idx])
                        above       = prof_db > (R_max_dB - 3)
                        idxs        = np.where(above)[0]
                        peak_width  = float((idxs[-1] - idxs[0]) * 2.5) if len(idxs) > 1 else np.nan
                    else:
                        R_pos_m = R_max_dB = peak_width = np.nan

                    # store profile
                    if tid in ("kharg", "ras_tanura"):
                        profile_data[tid].setdefault(tank["id"], {})[scene_id] = prof_db

                    # ring15_far aggregates
                    far_raw = _safe_crop(ring15_far_mask)
                    far_lin = far_raw[np.isfinite(far_raw) & (far_raw > 0)]
                    ring15_far_mean_db_of_px  = float(np.mean(10 * np.log10(far_lin))) if len(far_lin) else np.nan
                    ring15_far_mean_lin_to_db = float(10 * np.log10(np.mean(far_lin)))  if len(far_lin) else np.nan
                    ring15_far_median         = float(np.median(10 * np.log10(far_lin))) if len(far_lin) else np.nan
                    ring15_far_p90            = float(np.percentile(10 * np.log10(far_lin), 90)) if len(far_lin) else np.nan

                    row = {
                        "terminal":          tid,
                        "tank_id":           tank["id"],
                        "scene_id":          scene_id,
                        "date":              d_str,
                        "relOrbit":          rel_orb,
                        "direction":         dir_str,
                        "disc_full":                _safe_db(_safe_crop(disc_full_mask)),
                        "disc_inner":               _safe_db(_safe_crop(disc_inner_mask)),
                        "ring15_all":               _safe_db(_safe_crop(ring15_all_mask)),
                        "ring15_far":               ring15_far_mean_lin_to_db,
                        "ring15_near":              _safe_db(_safe_crop(ring15_near_mask)),
                        **sector_vals,
                        "ring10_far":               _safe_db(_safe_crop(ring10_far_mask)),
                        "ring25_far":               _safe_db(_safe_crop(ring25_far_mask)),
                        "ring15_far_mean_dB_of_px": ring15_far_mean_db_of_px,
                        "ring15_far_mean_lin_dB":   ring15_far_mean_lin_to_db,
                        "ring15_far_median":        ring15_far_median,
                        "ring15_far_p90":           ring15_far_p90,
                        **offset_vals,
                        "ring15_far_r08":           _safe_db(_safe_crop(ring15_far_r08_mask)),
                        "ring15_far_r12":           _safe_db(_safe_crop(ring15_far_r12_mask)),
                        "bg_ring":                  _safe_db(_safe_crop(bg_mask)),
                        "R_pos_m":                  R_pos_m,
                        "R_max_dB":                 R_max_dB,
                        "peak_width_m":             peak_width,
                        "pixel_valid_frac":         valid_frac,
                    }
                    record_rows.append(row)

            print(f"    {tid}/{dir_str}-{rel_orb}: {len(record_rows)} rows so far")

    # Write 4.1 — per-terminal file if only one terminal, else combined
    if record_rows:
        df4 = pd.DataFrame(record_rows)
        suffix = f"_{only_terminal}" if only_terminal else ""
        p41 = OUT / f"tank_scene_variants{suffix}.csv.gz"
        df4.to_csv(p41, index=False, compression="gzip")
        n_dup = df4.duplicated(subset=["terminal", "tank_id", "scene_id"]).sum()
        print(f"  4.1 tank_scene_variants{suffix}.csv.gz: {len(df4)} rows, {n_dup} duplicates → {p41}")

        # ── prod vs DB verification ──────────────────────────────────────────
        # Compare ring15_far in section-4 output vs db_mean from raw_observations/terminal_observations
        print("  4.1 prod-vs-DB match check…")
        try:
            match_rows = []
            for tid in df4["terminal"].unique():
                tbl = "raw_observations" if tid == "kharg" else "terminal_observations"
                db_rows = sb_fetch_all(tbl, "date,tank_id,db_mean",
                                       filters={"terminal_id": tid} if tid != "kharg" else None)
                db_map = {(r["tank_id"], str(r["date"])[:10]): r["db_mean"] for r in db_rows}
                sub = df4[df4["terminal"] == tid][["tank_id", "date", "ring15_far"]].copy()
                sub["db_mean_prod"] = sub.apply(
                    lambda row: db_map.get((row["tank_id"], str(row["date"])[:10])), axis=1)
                sub["delta"] = (sub["ring15_far"] - sub["db_mean_prod"]).abs()
                sub["match_01"] = sub["delta"] <= 0.01
                match_rows.append(sub)
            if match_rows:
                mdf = pd.concat(match_rows)
                n_total = len(mdf.dropna(subset=["db_mean_prod"]))
                n_match = int(mdf["match_01"].sum())
                frac = n_match / n_total if n_total else 0
                pv = OUT / f"prod_db_match{suffix}.csv"
                mdf.to_csv(pv, index=False)
                print(f"  prod-vs-DB: {n_match}/{n_total} rows match ±0.01 dB ({frac:.1%}) → {pv}")
        except Exception as e:
            print(f"  WARNING prod-vs-DB: {e}")
    else:
        print("  4.1 EMPTY")

    # Write 4.2 range_profiles.npz
    for tid in ("kharg", "ras_tanura"):
        if tid in profile_data and profile_data[tid]:
            tanks_list = sorted(profile_data[tid].keys())
            scenes_set = sorted({sc for t in profile_data[tid].values() for sc in t})
            # build array: tanks × scenes × profile_pts (pad to common length)
            max_len = max(
                len(v) for t in profile_data[tid].values() for v in t.values()
                if len(v) > 0
            ) if any(profile_data[tid].values()) else 1
            arr = np.full((len(tanks_list), len(scenes_set), max_len), np.nan, dtype=np.float32)
            for ti, tk in enumerate(tanks_list):
                for si, sc in enumerate(scenes_set):
                    if sc in profile_data[tid].get(tk, {}):
                        v = profile_data[tid][tk][sc]
                        arr[ti, si, :len(v)] = v
            p42 = OUT / f"range_profiles_{tid}.npz"
            np.savez_compressed(p42, profiles=arr, tanks=tanks_list, scenes=scenes_set)
            print(f"  4.2 range_profiles_{tid}.npz: {arr.shape} → {p42}")

    print("  Section 4 done.")


# ===========================================================================
# SECTION 3.1-3.2 — Crops & mean stack (SentinelHub quota)
# ===========================================================================
def section_3_crops():
    print("\n=== Section 3.1-3.2: SAR crops & mean stack ===")
    from sentinelhub import BBox, CRS, SentinelHubRequest, MimeType
    from datetime import timedelta as td
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from locations import LOCATIONS

    MANDATORY_DATES = {
        "kharg": ["2019-04-21", "2019-06-08", "2024-09-21", "2024-10-03",
                  "2024-10-15", "2026-04-14", "2026-05-14", "2026-06-26"],
    }
    N_PER_ORBIT = {"kharg": 24, "ras_tanura": 24, "yanbu": 24}
    N_SECONDARY = {"ras_tanura": 12}

    EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""
    RES = 10
    BUFFER = 300

    TARGET_ORBITS = {
        "kharg":      [{"direction": "DESC", "relOrbit": 35, "primary": True}],
        "ras_tanura": [{"direction": "DESC", "relOrbit": 35, "primary": True},
                       {"direction": "ASC",  "relOrbit": 101, "primary": False}],
        "yanbu":      [{"direction": "ASC",  "relOrbit": 14, "primary": True}],
    }

    manifest = load_v0_manifest()

    crops_dir = OUT / "crops"
    stack_dir = OUT / "mean_stack"
    crops_dir.mkdir(exist_ok=True)
    stack_dir.mkdir(exist_ok=True)

    for tid in TARGET_TERMINALS:
        loc = next((l for l in LOCATIONS if l["id"] == tid), None)
        if not loc:
            continue
        los_az = loc.get("los_azimuth", 282)
        tanks  = loc["tanks"]
        lats = [t["lat"] for t in tanks]
        lons = [t["lon"] for t in tanks]
        bbox = BBox([min(lons) - BUFFER/111320, min(lats) - BUFFER/111320,
                     max(lons) + BUFFER/111320, max(lats) + BUFFER/111320], crs=CRS.WGS84)
        W_px = int((bbox.max_x - bbox.min_x) / (RES / 111320))
        H_px = int((bbox.max_y - bbox.min_y) / (RES / 111320))
        if W_px < 1 or H_px < 1:
            continue

        for orb in TARGET_ORBITS[tid]:
            rel_orb = orb["relOrbit"]
            dir_str = orb["direction"]
            is_primary = orb["primary"]
            n_crops = N_PER_ORBIT[tid] if is_primary else N_SECONDARY.get(tid, 12)
            api_dir = "DESCENDING" if dir_str.upper() in ("DESC", "DESCENDING") else "ASCENDING"

            # Get dates
            if not manifest.empty:
                mf = manifest[(manifest["terminal_id"] == tid) &
                              (manifest["relOrbit"].astype(str) == str(rel_orb))]
                all_dates = sorted(mf["date"].str[:10].unique())
            else:
                tbl = "raw_observations" if tid == "kharg" else "terminal_observations"
                rows = sb_fetch_all(tbl, "date", filters={"terminal_id": tid})
                all_dates = sorted(set(r["date"][:10] for r in rows))

            if not all_dates:
                continue

            # Select dates: mandatory + evenly spaced
            mandatory = [d for d in MANDATORY_DATES.get(tid, []) if d in all_dates]
            remaining = [d for d in all_dates if d not in mandatory]
            n_add = max(0, n_crops - len(mandatory))
            step = max(1, len(remaining) // n_add) if n_add > 0 else 1
            selected = sorted(set(mandatory + remaining[::step])[:n_crops])

            print(f"  {tid}/{dir_str}-{rel_orb}: {len(selected)} crops to download…")
            orb_dir = crops_dir / f"{tid}_{dir_str}{rel_orb}"
            orb_dir.mkdir(exist_ok=True)

            stack_imgs = []
            for d_str in selected:
                d = datetime.strptime(d_str, "%Y-%m-%d")
                try:
                    req = SentinelHubRequest(
                        evalscript=EVALSCRIPT,
                        input_data=[SentinelHubRequest.input_data(
                            data_collection=S1_CDSE,
                            time_interval=(d_str, (d + td(days=1)).strftime("%Y-%m-%d")),
                            other_args={
                                "processing": {"orthorectify": True,
                                               "backCoeff": "GAMMA0_TERRAIN",
                                               "demInstance": "COPERNICUS_30"},
                                "dataFilter": {"orbitDirection": api_dir,
                                               "relativeOrbitNumber": rel_orb},
                                "mosaicking": "ORBIT",
                            },
                        )],
                        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                        bbox=bbox, size=(W_px, H_px), config=sh_config,
                    )
                    data = req.get_data()
                    if not data:
                        continue
                    img = data[0].squeeze().astype(np.float32)
                except Exception as e:
                    print(f"    {d_str}: SKIP ({e})")
                    continue

                stack_imgs.append(img)

                # Save float16 .npz
                gt = [bbox.min_x, (bbox.max_x - bbox.min_x) / W_px, 0,
                      bbox.max_y, 0, -(bbox.max_y - bbox.min_y) / H_px]
                scene_id = f"{tid}_{dir_str}{rel_orb}_{d_str}"
                npz_path = orb_dir / f"{scene_id}.npz"
                np.savez_compressed(npz_path,
                                    img=img.astype(np.float16),
                                    geotransform=np.array(gt),
                                    scene_id=scene_id)

            print(f"    {tid}/{dir_str}-{rel_orb}: {len(stack_imgs)} scenes downloaded")

            # 3.2 mean stack
            if stack_imgs:
                # average in linear
                mean_img = np.nanmean(np.stack(stack_imgs, axis=0), axis=0)
                gt = [bbox.min_x, (bbox.max_x - bbox.min_x) / W_px, 0,
                      bbox.max_y, 0, -(bbox.max_y - bbox.min_y) / H_px]
                npz_path = stack_dir / f"{tid}_{dir_str}{rel_orb}_mean_stack.npz"
                np.savez_compressed(npz_path,
                                    img=mean_img.astype(np.float16),
                                    geotransform=np.array(gt),
                                    n_scenes=len(stack_imgs))

                # PNG with registry circles
                mean_db = np.where(mean_img > 0, 10 * np.log10(mean_img), np.nan)
                fig, ax = plt.subplots(figsize=(10, 10 * H_px / W_px))
                vmin = np.nanpercentile(mean_db, 2)
                vmax = np.nanpercentile(mean_db, 98)
                ax.imshow(mean_db, cmap="gray", vmin=vmin, vmax=vmax,
                          extent=[bbox.min_x, bbox.max_x, bbox.min_y, bbox.max_y],
                          aspect="auto")
                for t in tanks:
                    circ = plt.Circle((t["lon"], t["lat"]),
                                      t["radius_m"] / 111320,
                                      color="red", fill=False, lw=0.8)
                    ax.add_patch(circ)
                    ax.text(t["lon"], t["lat"], t["id"], fontsize=4,
                            color="yellow", ha="center", va="center")
                ax.set_title(f"{tid} {dir_str}-{rel_orb} mean stack (N={len(stack_imgs)})")
                fig.savefig(stack_dir / f"{tid}_{dir_str}{rel_orb}_mean_stack.png",
                            dpi=150, bbox_inches="tight")
                plt.close()

    print("  Section 3.1-3.2 done.")


# ===========================================================================
# README
# ===========================================================================
def write_readme(sections_run):
    p = OUT / "README.md"
    lines = [
        f"# diagnostics/mega — Full diagnostic export",
        f"",
        f"Generated: {TS}  SHA: {SCRIPT_SHA}  Run: {RUN_ID}",
        f"Sections run: {sections_run}",
        f"",
        f"## Files",
        f"",
        f"| File | Description | Status |",
        f"|------|-------------|--------|",
    ]
    entries = [
        ("registry_full.csv",         "All tanks, all fields from main registry"),
        ("registry_history.csv",      "Git diff history of tank_registry.py"),
        ("esri_crops/farm_*.png",     "ESRI z18 farm overview per terminal with circles"),
        ("esri_crops/<tank_id>.png",  "Per-tank ESRI z18 crop (3× diameter window)"),
        ("source_points.txt",         "Source-points search results in repo"),
        ("scenes.csv",                "All scenes: V0 manifest + DB dates, in_db flag"),
        ("evalscript_and_params.txt", "Exact evalscript and SentinelHub request params"),
        ("prod_tank_series.csv",      "Per-tank fill/vol series from prod code + DB"),
        ("calibration_code/",         "Copies of sar_fetch, volume, compute, config, etc."),
        ("tvt_all.csv",               "volume_timeseries + terminal_volume_timeseries + n_tanks"),
        ("anchors_all/",              "All anchor files from repo + Supabase + event_csv"),
        ("anchor_mapping.txt",        "Anchor-to-terminal wiring (file:line)"),
        ("past_results/",             "Correlation result CSVs and run logs"),
        ("tank_scene_variants.csv.gz","All (terminal,tank,scene) metric variants — section 4"),
        ("range_profiles_*.npz",      "LOS range profiles tank×scene×pts — section 4"),
        ("crops/<terminal_orbit>/",   "Per-scene float16 .npz crops — section 3.1"),
        ("mean_stack/",               "Mean stack .npz + PNG per orbit — section 3.2"),
    ]
    for fname, desc in entries:
        fp = OUT / fname.split("/")[0]
        if fp.exists():
            status = "✓"
            try:
                if fp.is_file():
                    sz = f"{fp.stat().st_size / 1024:.0f} KB"
                else:
                    n = len(list(fp.rglob("*")))
                    sz = f"{n} files"
            except Exception:
                sz = ""
            lines.append(f"| `{fname}` | {desc} | {status} {sz} |")
        else:
            lines.append(f"| `{fname}` | {desc} | pending |")

    lines += ["", "## Data quality checks (section 4)", ""]
    gz = OUT / "tank_scene_variants.csv.gz"
    if gz.exists():
        try:
            df_check = pd.read_csv(gz)
            n_dup = df_check.duplicated(subset=["terminal", "tank_id", "scene_id"]).sum()
            prod_db_match = ""
            if "ring15_far" in df_check.columns:
                lines.append(f"- Rows: {len(df_check)}")
                lines.append(f"- Duplicates (terminal,tank_id,scene_id): {n_dup}")
        except Exception as e:
            lines.append(f"- Could not read tank_scene_variants.csv.gz: {e}")

    p.write_text("\n".join(lines))
    print(f"\n  README.md → {p}")


# ===========================================================================
# Pack zip
# ===========================================================================
def pack_zips():
    """Pack diagnostics/mega into two archives beside it."""
    import zipfile
    root = OUT

    # mega_part1.zip — everything except esri_crops/
    p1 = root.parent / "mega_part1.zip"
    with zipfile.ZipFile(p1, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fp in sorted(root.rglob("*")):
            if fp.is_file() and "esri_crops" not in fp.parts:
                zf.write(fp, fp.relative_to(root.parent))
    sz1 = p1.stat().st_size / 1024 / 1024
    print(f"  mega_part1.zip: {sz1:.1f} MB → {p1}")

    # esri_crops.zip
    crops_dir = root / "esri_crops"
    p2 = root.parent / "esri_crops.zip"
    if crops_dir.exists():
        with zipfile.ZipFile(p2, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for fp in sorted(crops_dir.rglob("*")):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(root.parent))
        sz2 = p2.stat().st_size / 1024 / 1024
        print(f"  esri_crops.zip: {sz2:.1f} MB → {p2}")
    else:
        print("  esri_crops/ not found — skipping esri_crops.zip")


# ===========================================================================
# Main
# ===========================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sections", default="1,2,3.3,5,6",
                        help="Comma-separated sections to run: 1,2,3.3,5,6,4,3.1,3.2")
    parser.add_argument("--terminal", default=None,
                        help="For section 4: run only this terminal (kharg|ras_tanura|yanbu)")
    parser.add_argument("--pack-zip", action="store_true",
                        help="After sections, pack output into mega_part1.zip + esri_crops.zip")
    args = parser.parse_args()
    sections = [s.strip() for s in args.sections.split(",")]

    print(f"mega_export.py  SHA={SCRIPT_SHA}  Run={RUN_ID}  TS={TS}")
    print(f"Sections: {sections}  Terminal: {args.terminal or 'all'}  Output: {OUT}")

    if "1" in sections:
        section_1()
    if "2" in sections:
        section_2()
    if "3.3" in sections:
        section_3_3()
    if "5" in sections:
        section_5()
    if "6" in sections:
        section_6()
    if "4" in sections:
        section_4(only_terminal=args.terminal)
    if "3.1" in sections or "3.2" in sections:
        section_3_crops()

    write_readme(sections)

    if args.pack_zip:
        print("\n=== Packing zips ===")
        pack_zips()

    print(f"\n=== mega_export complete. Output: {OUT} ===")


if __name__ == "__main__":
    main()
