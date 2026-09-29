"""
stage4_visual_qa.py — Visual QA: verify whitelist tanks have crescent on tank wall.

For each of 12 tanks (8 YB whitelist + 4 JW sample including jw_9):
  - Fetches one fresh SAR scene (most recent for the terminal's orbit)
  - Generates 8× upscaled crop, overlays annulus ring and crescent mask
  - Saves PNG to diagnostics/stage4/crops/

Output:
  diagnostics/stage4/visual_qa_report.txt  — table of verdicts (filled manually)
  diagnostics/stage4/visual_qa_report.json — same, for machine parsing
  diagnostics/stage4/crops/<terminal>_<tank_id>_<date>_sar.png
  diagnostics/stage4/crops/<terminal>_<tank_id>_<date>_sar_masked.png

Verdicts filled at run-time from inspection: ON_WALL / OFF_TANK / UNCLEAR
OFF_TANK tanks are excluded from whitelist (wl_note: "stable_ground").

Usage:
    python scripts/stage4_visual_qa.py
"""

import os
import sys
import math
import json
import datetime
import warnings
import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("[WARN] Pillow not installed — PNG output disabled. pip install Pillow")

from sentinelhub import SHConfig, BBox, CRS, DataCollection, SentinelHubRequest, MimeType
from config import RESOLUTION, DB_FLOOR
from sar_fetch import get_all_scene_dates, build_crescent_masks, lonlat_to_pix, _EVALSCRIPT

config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse", service_url="https://sh.dataspace.copernicus.eu"
)

CROP_BUFFER_M = 400    # metres around each tank for crop bbox
UPSCALE       = 8      # display upscale factor

# YB: all 8 whitelist tanks; JW: 4 sample including jw_9 (low step but suspicious)
QA_TANKS = [
    # YB — all 8 whitelist
    {"terminal": "yanbu",    "id": "yb_0",  "lat": 23.959074, "lon": 38.244398, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_2",  "lat": 23.964053, "lon": 38.245977, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_3",  "lat": 23.965544, "lon": 38.244086, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_5",  "lat": 23.966460, "lon": 38.248119, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_7",  "lat": 23.967392, "lon": 38.252229, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_8",  "lat": 23.968866, "lon": 38.250263, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_9",  "lat": 23.970331, "lon": 38.248336, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    {"terminal": "yanbu",    "id": "yb_11", "lat": 23.971259, "lon": 38.252416, "r": 60, "h": 18,
     "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}]},
    # JW — 4 sample (jw_9 + 3 others spread across cluster)
    {"terminal": "juwaymah", "id": "jw_9",  "lat": 26.819531458247540, "lon": 49.992284316605510, "r": 50, "h": 16,
     "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}]},
    {"terminal": "juwaymah", "id": "jw_3",  "lat": 26.818881636808456, "lon": 49.982719201457790, "r": 50, "h": 16,
     "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}]},
    {"terminal": "juwaymah", "id": "jw_11", "lat": 26.820698661135080, "lon": 49.991014367679170, "r": 50, "h": 16,
     "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}]},
    {"terminal": "juwaymah", "id": "jw_16", "lat": 26.823098922243503, "lon": 49.983801260652840, "r": 50, "h": 16,
     "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}]},
]


def _crop_bbox(lat, lon, buf=CROP_BUFFER_M):
    d_lat = buf / 111320
    d_lon = buf / (111320 * math.cos(math.radians(lat)))
    return BBox((lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat), crs=CRS.WGS84)


def _fetch_sar_image(date_str, bbox, img_w, img_h, orbit_cfg):
    """Return raw linear VV array for one date."""
    orb = orbit_cfg[0]
    dir_str = orb["direction"].upper()
    if dir_str == "DESC": dir_str = "DESCENDING"
    elif dir_str == "ASC": dir_str = "ASCENDING"

    data_filter = {
        "orbitDirection": dir_str,
        "relativeOrbitNumber": orb["relative_orbit"],
    }
    evalscript = _EVALSCRIPT
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, date_str),
            other_args={"dataFilter": data_filter, "processing": {
                "orthorectify": True,
                "demInstance": "COPERNICUS_30",
                "backCoeff": "GAMMA0_TERRAIN",
                "speckleFilter": {"type": "NONE"},
            }}
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        size=[img_w, img_h],
        config=config,
    )
    data = request.get_data()
    if not data:
        return None
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return arr


def _to_db(linear):
    with np.errstate(divide='ignore', invalid='ignore'):
        db = 10.0 * np.log10(np.where(linear > 0, linear, np.nan))
    db = np.where(np.isfinite(db), db, np.nan)
    return np.clip(db, DB_FLOOR, None)


def _save_sar_png(arr_db, bbox, tank, date_str, out_path):
    """Save grayscale SAR crop with annulus ring and crescent overlay."""
    if not HAS_PIL:
        return

    h, w = arr_db.shape
    vmin = np.nanpercentile(arr_db, 2)
    vmax = np.nanpercentile(arr_db, 98)
    norm = np.clip((arr_db - vmin) / max(vmax - vmin, 1e-6), 0, 1)
    norm = np.nan_to_num(norm, nan=0.5)

    up = UPSCALE
    hw, ww = h * up, w * up
    gray = (norm * 255).astype(np.uint8)
    img_up = Image.fromarray(gray, mode='L').convert('RGBA').resize((ww, hw), Image.NEAREST)
    overlay = Image.new('RGBA', (ww, hw), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Tank centre in upscaled image
    cx_f, cy_f = lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
    cx, cy = cx_f * up, cy_f * up

    r_outer = tank["r"] / RESOLUTION * up
    r_inner = max(0, tank["r"] - 15) / RESOLUTION * up  # WIDTH_WIDE=15m

    # Outer ring (yellow)
    draw.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
                 outline=(255, 220, 0, 255), width=max(1, up // 4))
    # Inner ring (yellow, thinner)
    draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner],
                 outline=(255, 220, 0, 180), width=max(1, up // 8))

    # Crescent highlight — build pixel mask at native res, upscale
    los = tank["orbit"][0].get("los_azimuth", 282)
    az = los
    dx = math.sin(math.radians(az))
    dy = -math.cos(math.radians(az))
    Y_grid, X_grid = np.indices((h, w))
    dot_product = (X_grid - cx_f) * dx + (Y_grid - cy_f) * dy
    dist_sq = (X_grid - cx_f) ** 2 + (Y_grid - cy_f) ** 2
    r_out = tank["r"] / RESOLUTION
    r_in  = max(0, tank["r"] - 15) / RESOLUTION
    crescent = (dist_sq <= r_out**2) & (dist_sq >= r_in**2) & (dot_product > 0)

    # Paint crescent pixels red (semi-transparent) on overlay
    crescent_rgba = np.zeros((h, w, 4), dtype=np.uint8)
    crescent_rgba[crescent] = [255, 50, 50, 180]
    crescent_img = Image.fromarray(crescent_rgba, 'RGBA').resize((ww, hw), Image.NEAREST)

    combined = Image.alpha_composite(img_up, crescent_img)
    combined = Image.alpha_composite(combined, overlay)

    # Add text annotation
    from PIL import ImageFont
    draw2 = ImageDraw.Draw(combined)
    label = f"{tank['id']}  {date_str}"
    draw2.text((4, 4), label, fill=(255, 255, 100, 255))

    combined.convert('RGB').save(out_path)


def main():
    os.makedirs("diagnostics/stage4/crops", exist_ok=True)

    terminal_dates = {}  # terminal_id → most recent date string

    results = []

    for tank in QA_TANKS:
        tid   = tank["terminal"]
        tank_id = tank["id"]
        orbit_cfg = tank["orbit"]

        print(f"\n── {tank_id} ({tid}) ──")

        # Get most recent date for this terminal
        if tid not in terminal_dates:
            ref_lat, ref_lon = tank["lat"], tank["lon"]
            ref_bbox = _crop_bbox(ref_lat, ref_lon, buf=2000)
            orb = orbit_cfg[0]
            orbit_state = "descending" if orb["direction"].upper() in ("DESC","DESCENDING") else "ascending"
            all_dates = get_all_scene_dates(ref_bbox, orbit_state=orbit_state,
                                            relative_orbit=orb.get("relative_orbit"))
            if not all_dates:
                print(f"  [!] No catalog dates for {tid}")
                terminal_dates[tid] = None
            else:
                terminal_dates[tid] = str(all_dates[-1].date())
                print(f"  Using date: {terminal_dates[tid]} ({len(all_dates)} catalog dates)")

        date_str = terminal_dates.get(tid)
        if not date_str:
            results.append({"terminal": tid, "tank_id": tank_id, "date": None,
                            "verdict": "NO_DATA", "note": ""})
            continue

        # Crop bbox
        bbox = _crop_bbox(tank["lat"], tank["lon"])
        buf_m = CROP_BUFFER_M * 2
        img_w = max(20, int(buf_m / RESOLUTION))
        img_h = img_w

        # Fetch SAR image
        try:
            arr_linear = _fetch_sar_image(date_str, bbox, img_w, img_h, orbit_cfg)
        except Exception as e:
            print(f"  [!] SAR fetch failed: {e}")
            arr_linear = None

        if arr_linear is None:
            print(f"  [!] No SAR data for {date_str}")
            results.append({"terminal": tid, "tank_id": tank_id, "date": date_str,
                            "verdict": "NO_DATA", "note": "fetch failed"})
            continue

        arr_db = _to_db(arr_linear)

        # Compute crescent mean for info
        los = orbit_cfg[0].get("los_azimuth", 282)
        cx_f, cy_f = lonlat_to_pix(tank["lon"], tank["lat"], bbox, img_w, img_h)
        r_out = tank["r"] / RESOLUTION
        r_in  = max(0, tank["r"] - 15) / RESOLUTION
        Y_grid, X_grid = np.indices((img_h, img_w))
        dx = math.sin(math.radians(los))
        dy = -math.cos(math.radians(los))
        dot_product = (X_grid - cx_f) * dx + (Y_grid - cy_f) * dy
        dist_sq = (X_grid - cx_f) ** 2 + (Y_grid - cy_f) ** 2
        crescent = (dist_sq <= r_out**2) & (dist_sq >= r_in**2) & (dot_product > 0)
        crescent_pix = int(crescent.sum())
        vals = arr_db[crescent]
        vals = vals[np.isfinite(vals)]
        crescent_db = float(np.mean(vals)) if len(vals) > 0 else float('nan')
        print(f"  crescent={crescent_pix}px  mean_db={crescent_db:.2f}")

        # Save PNG
        out_sar = f"diagnostics/stage4/crops/{tid}_{tank_id}_{date_str}_sar.png"
        _save_sar_png(arr_db, bbox, tank, date_str, out_sar)
        print(f"  Saved: {out_sar}")

        results.append({
            "terminal":     tid,
            "tank_id":      tank_id,
            "date":         date_str,
            "crescent_px":  crescent_pix,
            "crescent_db":  round(crescent_db, 3) if math.isfinite(crescent_db) else None,
            "verdict":      "PENDING",  # filled by human inspection
            "note":         "",
        })

    # Write JSON (verdicts to be filled manually or via update script)
    out_json = "diagnostics/stage4/visual_qa_report.json"
    payload = {
        "run_date": datetime.datetime.utcnow().isoformat(),
        "tanks":    results,
    }
    with open(out_json, "w") as f:
        json.dump(payload, f, indent=2)

    # Write text table
    out_txt = "diagnostics/stage4/visual_qa_report.txt"
    lines = []
    lines.append(f"Stage 4 Visual QA  {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    lines.append("Inspect crops in diagnostics/stage4/crops/")
    lines.append("Update verdict in JSON (ON_WALL / OFF_TANK / UNCLEAR)")
    lines.append("")
    lines.append(f"{'Terminal':<12} {'Tank':>8} {'Date':>12} {'CrescPx':>8} {'MeanDB':>7}  Verdict")
    lines.append("-" * 65)
    for r in results:
        db_s = f"{r['crescent_db']:.2f}" if r.get("crescent_db") is not None else "  nan"
        px_s = str(r.get("crescent_px", "")) if r.get("crescent_px") is not None else ""
        lines.append(
            f"{r['terminal']:<12} {r['tank_id']:>8} {r.get('date',''):>12} "
            f"{px_s:>8} {db_s:>7}  {r['verdict']}"
        )
    with open(out_txt, "w") as f:
        f.write("\n".join(lines))

    print(f"\nResults: {out_json}")
    print(f"Report:  {out_txt}")
    print("\nNEXT: inspect crops, update verdict fields in JSON to ON_WALL/OFF_TANK/UNCLEAR")


if __name__ == "__main__":
    main()
