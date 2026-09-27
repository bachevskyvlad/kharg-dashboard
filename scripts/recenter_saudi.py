"""
recenter_saudi.py — Stage 1: template matching to correct tank centres for JW/YB.
Also runs a control check on 10 Ras Tanura south tanks (expect <1 px shift).

Method:
  For each tank: build a ring template (annulus at radius_m ± WIDTH_WIDE/2),
  fetch 2 SAR scenes on the terminal's primary orbit, cross-correlate template
  with ±10-px search window using normalised cross-correlation (NCC).
  Best shift = argmax NCC. Confidence = peak NCC score.

Output:
  diagnostics/recenter/RECENTER_REPORT.md  — table + decisions
  diagnostics/recenter/crops/              — SAR crop PNGs with old and new ring overlays

Thresholds:
  NCC ≥ 0.35 → ok (shift applied)
  NCC 0.20–0.34 → low-confidence (flagged, human review)
  NCC < 0.20 → excluded (tank removed from backfill; no coordinate change)

  RT control criterion: median |shift| < 1 px → PASS. Else: ABORT.

Usage:
  python scripts/recenter_saudi.py --terminals juwaymah,yanbu
  python scripts/recenter_saudi.py --terminals ras_tanura  --control-only
  python scripts/recenter_saudi.py --terminals juwaymah,yanbu,ras_tanura
"""

import argparse
import datetime
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RESOLUTION    = 5       # m/px
WIDTH_WIDE    = 15      # m — crescent annulus width (same as prod)
SEARCH_PX     = 10      # ±10 px search window
NCC_OK        = 0.35
NCC_LOW       = 0.20
N_SCENES      = 2       # scenes per tank for template matching
BUFFER_M      = 300

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"

os.environ.setdefault("CLIENT_ID",     SH_CLIENT_ID)
os.environ.setdefault("CLIENT_SECRET", SH_CLIENT_SECRET)

OUT_DIR = Path("diagnostics/recenter")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CROPS_DIR = OUT_DIR / "crops"
CROPS_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))
from tank_registry import LOCATIONS as ALL_TERMINALS

# ---------------------------------------------------------------------------
# sentinelhub bootstrap
# ---------------------------------------------------------------------------
from sentinelhub import SHConfig, BBox, CRS, DataCollection, SentinelHubCatalog

sh_config = SHConfig()
sh_config.sh_client_id     = SH_CLIENT_ID
sh_config.sh_client_secret = SH_CLIENT_SECRET
sh_config.sh_base_url      = "https://sh.dataspace.copernicus.eu"
sh_config.sh_token_url     = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
    "/protocol/openid-connect/token"
)

_S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_recenter", service_url="https://sh.dataspace.copernicus.eu"
)

from sar_fetch import get_all_scene_dates

# ---------------------------------------------------------------------------
# SAR fetch (single scene, full terminal bbox)
# ---------------------------------------------------------------------------

_EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


def _fetch_scene(bbox, date_str, direction, rel_orbit):
    from sentinelhub import SentinelHubRequest, MimeType
    from datetime import date, timedelta
    d = date.fromisoformat(date_str)
    w = max(10, int((bbox.max_x - bbox.min_x) * 111320
                    * np.cos(np.deg2rad(bbox.min_y)) / RESOLUTION))
    h = max(10, int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION))
    try:
        req = SentinelHubRequest(
            evalscript=_EVALSCRIPT,
            input_data=[SentinelHubRequest.input_data(
                data_collection=_S1_CDSE,
                time_interval=(date_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                other_args={
                    "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                   "demInstance": "COPERNICUS_30"},
                    "dataFilter": {"orbitDirection": direction,
                                   "relativeOrbit": rel_orbit},
                    "mosaicking": "ORBIT",
                },
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=(w, h), config=sh_config,
        )
        data = req.get_data()
        if not data:
            return None, w, h
        arr = np.array(data[0], dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.std() == 0:
            return None, w, h
        return arr, w, h
    except Exception as e:
        print(f"    [WARN] fetch {date_str}: {e}")
        return None, w, h


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _lonlat_to_pix(lon, lat, bbox, w, h):
    cx = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    cy = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return cx, cy


def _ring_template(radius_m, width_m=WIDTH_WIDE, res=RESOLUTION):
    """Boolean ring mask centred at (0,0) in a small patch."""
    r_out = radius_m / res
    r_in  = max(0.0, (radius_m - width_m)) / res
    patch = int(r_out * 2 + 4)
    Y, X  = np.mgrid[-patch:patch+1, -patch:patch+1]
    dist  = np.sqrt(X**2 + Y**2)
    return ((dist <= r_out) & (dist >= r_in)).astype(np.float32)


# ---------------------------------------------------------------------------
# normalised cross-correlation (NCC) in a ±SEARCH_PX window
# ---------------------------------------------------------------------------

def _ncc_shift(img_lin, cx, cy, radius_m, search=SEARCH_PX):
    """
    Returns (dx_px, dy_px, ncc_peak) — the sub-pixel shift that maximises
    normalised cross-correlation of the ring template with the image patch.
    img_lin is in linear power (not dB).
    """
    # Work in dB for better ring contrast
    img_db = 10 * np.log10(np.clip(img_lin, 1e-6, None))
    h, w   = img_db.shape

    tmpl   = _ring_template(radius_m)
    th, tw = tmpl.shape
    ph, pw = th + 2 * search, tw + 2 * search  # padded extraction

    # Extract patch centred on nominal centre
    r0 = int(cy) - ph // 2
    c0 = int(cx) - pw // 2
    r1, c1 = r0 + ph, c0 + pw

    # Clamp to image bounds
    if r0 < 0 or c0 < 0 or r1 > h or c1 > w:
        return 0.0, 0.0, 0.0  # centre too close to edge

    patch = img_db[r0:r1, c0:c1]

    # Slide template over ±search window
    best_ncc = -1.0
    best_dy = best_dx = 0

    tmpl_flat = tmpl.flatten()
    tmpl_mu   = tmpl_flat.mean()
    tmpl_sig  = tmpl_flat.std()
    if tmpl_sig < 1e-9:
        return 0.0, 0.0, 0.0

    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            oy = search + dy
            ox = search + dx
            sub = patch[oy:oy + th, ox:ox + tw].flatten()
            if sub.shape != tmpl_flat.shape:
                continue
            sub_mu  = sub.mean()
            sub_sig = sub.std()
            if sub_sig < 1e-9:
                continue
            ncc = ((sub - sub_mu) * (tmpl_flat - tmpl_mu)).mean() / (sub_sig * tmpl_sig)
            if ncc > best_ncc:
                best_ncc = ncc
                best_dy, best_dx = dy, dx

    return float(best_dx), float(best_dy), float(best_ncc)


# ---------------------------------------------------------------------------
# save crop with old/new ring overlay
# ---------------------------------------------------------------------------

def _save_crop(img_lin, cx_old, cy_old, cx_new, cy_new, radius_m, tank_id, scene_date, outpath):
    img_db = 10 * np.log10(np.clip(img_lin, 1e-6, None))
    r_px   = radius_m / RESOLUTION

    # Crop ±3×radius around tank
    crop_r = int(r_px * 3)
    r0 = max(0, int(cy_old) - crop_r)
    c0 = max(0, int(cx_old) - crop_r)
    r1 = min(img_db.shape[0], int(cy_old) + crop_r)
    c1 = min(img_db.shape[1], int(cx_old) + crop_r)
    crop = img_db[r0:r1, c0:c1]

    fig, ax = plt.subplots(figsize=(4, 4))
    vmin, vmax = np.percentile(crop, [2, 98])
    ax.imshow(crop, cmap="gray", vmin=vmin, vmax=vmax)

    # old ring — red dashed
    theta = np.linspace(0, 2 * np.pi, 300)
    ax.plot(cx_old - c0 + r_px * np.cos(theta),
            cy_old - r0 + r_px * np.sin(theta),
            "r--", lw=0.8, label="old")
    ax.plot(cx_old - c0 + (r_px - WIDTH_WIDE / RESOLUTION) * np.cos(theta),
            cy_old - r0 + (r_px - WIDTH_WIDE / RESOLUTION) * np.sin(theta),
            "r--", lw=0.5)
    # new ring — green solid
    ax.plot(cx_new - c0 + r_px * np.cos(theta),
            cy_new - r0 + r_px * np.sin(theta),
            "g-", lw=0.8, label="new")
    ax.plot(cx_new - c0 + (r_px - WIDTH_WIDE / RESOLUTION) * np.cos(theta),
            cy_new - r0 + (r_px - WIDTH_WIDE / RESOLUTION) * np.sin(theta),
            "g-", lw=0.5)

    ax.legend(loc="upper right", fontsize=6)
    ax.set_title(f"{tank_id} — {scene_date}", fontsize=7)
    ax.axis("off")
    plt.tight_layout(pad=0.3)
    plt.savefig(outpath, dpi=120)
    plt.close()


# ---------------------------------------------------------------------------
# process one terminal
# ---------------------------------------------------------------------------

def process_terminal(terminal, control_only=False):
    tid        = terminal["id"]
    tanks      = terminal["tanks"]
    clusters   = terminal.get("clusters", [])
    allowed_orbits = terminal.get("allowed_orbits")
    if not allowed_orbits:
        print(f"  SKIP {tid}: no allowed_orbits")
        return []

    orb        = allowed_orbits[0]
    rel_orbit  = orb["relative_orbit"]
    direction  = orb["direction"]

    # build fetch clusters
    tank_by_id = {t["id"]: t for t in tanks}
    fetch_clusters = []
    if clusters:
        seen = set()
        for c in clusters:
            c_tanks = [tank_by_id[i] for i in c["tank_ids"] if i in tank_by_id and i not in seen]
            seen.update(t["id"] for t in c_tanks)
            if c_tanks:
                fetch_clusters.append((c["name"], c_tanks))
    else:
        fetch_clusters = [("default", tanks)]

    # For RT control: sample first 10 tanks from south cluster only
    if control_only:
        south = next((c for c in clusters if c["name"] == "south"), None)
        if south:
            sample_ids = south["tank_ids"][:10]
            tanks = [tank_by_id[i] for i in sample_ids if i in tank_by_id]
            fetch_clusters = [("south_sample", tanks)]
        else:
            tanks = tanks[:10]
            fetch_clusters = [("default_sample", tanks)]

    # Get 2 recent scene dates
    from datetime import datetime
    c_tanks_all = [t for _, c_tanks in fetch_clusters for t in c_tanks]
    dlat = BUFFER_M / 111320
    dlon = BUFFER_M / (111320 * np.cos(np.deg2rad(min(t["lat"] for t in c_tanks_all))))
    full_bbox = BBox([
        min(t["lon"] for t in c_tanks_all) - dlon,
        min(t["lat"] for t in c_tanks_all) - dlat,
        max(t["lon"] for t in c_tanks_all) + dlon,
        max(t["lat"] for t in c_tanks_all) + dlat,
    ], crs=CRS.WGS84)

    scene_dates = get_all_scene_dates(
        full_bbox,
        start_date=datetime(2026, 1, 1),
        orbit_state=direction,
        relative_orbit=rel_orbit,
    )
    scene_dates = sorted(scene_dates)[-N_SCENES:]  # 2 most recent
    if not scene_dates:
        print(f"  SKIP {tid}: no scene dates found")
        return []

    print(f"  {tid}: {len(tanks)} tanks, scenes {[d.strftime('%Y-%m-%d') for d in scene_dates]}")

    results = []

    for c_name, c_tanks in fetch_clusters:
        dlat = BUFFER_M / 111320
        dlon = BUFFER_M / (111320 * np.cos(np.deg2rad(min(t["lat"] for t in c_tanks))))
        bbox = BBox([
            min(t["lon"] for t in c_tanks) - dlon,
            min(t["lat"] for t in c_tanks) - dlat,
            max(t["lon"] for t in c_tanks) + dlon,
            max(t["lat"] for t in c_tanks) + dlat,
        ], crs=CRS.WGS84)

        # Fetch 2 scenes for this cluster
        scene_imgs = []
        for d in scene_dates:
            d_str = d.strftime("%Y-%m-%d")
            img, w, h = _fetch_scene(bbox, d_str, direction, rel_orbit)
            if img is not None:
                scene_imgs.append((d_str, img, w, h))

        if not scene_imgs:
            print(f"    {c_name}: no scenes fetched")
            for tank in c_tanks:
                results.append({
                    "terminal_id": tid, "tank_id": tank["id"],
                    "lat_old": tank["lat"], "lon_old": tank["lon"],
                    "dx_px": np.nan, "dy_px": np.nan,
                    "dx_m": np.nan, "dy_m": np.nan,
                    "ncc_score": 0.0, "status": "excluded (no scene)",
                    "lat_new": tank["lat"], "lon_new": tank["lon"],
                })
            continue

        # Per tank: average shift over available scenes
        for tank in c_tanks:
            dx_list, dy_list, ncc_list = [], [], []
            best_img = None
            best_d   = None
            best_ncc = -1.0

            for d_str, img, w, h in scene_imgs:
                cx, cy = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
                dx, dy, ncc = _ncc_shift(img, cx, cy, tank["radius_m"])
                dx_list.append(dx)
                dy_list.append(dy)
                ncc_list.append(ncc)
                if ncc > best_ncc:
                    best_ncc = ncc
                    best_img, best_d = img, d_str

            mean_dx  = float(np.mean(dx_list))
            mean_dy  = float(np.mean(dy_list))
            mean_ncc = float(np.mean(ncc_list))

            # Determine status
            if mean_ncc >= NCC_OK:
                status = "ok"
            elif mean_ncc >= NCC_LOW:
                status = "low-confidence"
            else:
                status = "excluded"

            # Convert px shift to metres and new lat/lon
            # dx_m positive = east, dy_m positive = south
            dx_m = mean_dx * RESOLUTION
            dy_m = -mean_dy * RESOLUTION  # image y increases downward

            lat_new = tank["lat"] + dy_m / 111320
            lon_new = tank["lon"] + dx_m / (111320 * np.cos(np.deg2rad(tank["lat"])))

            # Save crop with old/new ring
            if best_img is not None:
                _, iw, ih = scene_imgs[0][2], *scene_imgs[0][2:4]
                cx_old, cy_old = _lonlat_to_pix(tank["lon"],   tank["lat"],   bbox, iw, ih)
                cx_new, cy_new = _lonlat_to_pix(lon_new,       lat_new,       bbox, iw, ih)
                crop_path = CROPS_DIR / f"{tank['id']}_{best_d}.png"
                _save_crop(best_img, cx_old, cy_old, cx_new, cy_new,
                           tank["radius_m"], tank["id"], best_d, crop_path)

            results.append({
                "terminal_id": tid,
                "tank_id":     tank["id"],
                "lat_old":     tank["lat"],
                "lon_old":     tank["lon"],
                "dx_px":       round(mean_dx, 2),
                "dy_px":       round(mean_dy, 2),
                "dx_m":        round(dx_m, 1),
                "dy_m":        round(dy_m, 1),
                "ncc_score":   round(mean_ncc, 3),
                "status":      status,
                "lat_new":     round(lat_new, 9) if status != "excluded" else tank["lat"],
                "lon_new":     round(lon_new, 9) if status != "excluded" else tank["lon"],
            })

            shift_m = np.sqrt(dx_m**2 + dy_m**2)
            print(f"    {tank['id']}: shift={shift_m:.1f}m "
                  f"(dx={dx_m:.1f}m dy={dy_m:.1f}m) ncc={mean_ncc:.3f} → {status}")

    return results


# ---------------------------------------------------------------------------
# write report
# ---------------------------------------------------------------------------

def write_report(all_results, control_results):
    lines = ["# Stage 1 — Re-centring Report\n\n"]
    lines.append(f"*Generated {datetime.datetime.utcnow().isoformat()} UTC*\n\n")
    lines.append(f"NCC threshold: ok ≥ {NCC_OK}, low-confidence ≥ {NCC_LOW}, "
                 f"excluded < {NCC_LOW}. Search window ±{SEARCH_PX} px. "
                 f"Resolution {RESOLUTION} m/px.\n\n")

    # RT control check
    if control_results:
        shifts = [np.sqrt(r["dx_m"]**2 + r["dy_m"]**2)
                  for r in control_results if np.isfinite(r["dx_m"])]
        med_shift = np.median(shifts) / RESOLUTION if shifts else np.nan
        control_pass = med_shift < 1.0
        lines.append("## RT control check\n\n")
        lines.append(f"Median |shift| = **{med_shift:.2f} px** (criterion <1 px) "
                     f"→ {'**PASS**' if control_pass else '**FAIL — ABORT**'}\n\n")
        if not control_pass:
            lines.append("**ACTION REQUIRED:** RT centres shifted >1 px. "
                         "Do NOT proceed to Stage 2 until RT registry is corrected.\n\n")

        lines.append("| tank_id | dx_m | dy_m | |shift|_m | NCC | status |\n")
        lines.append("|---------|------|------|----------|-----|--------|\n")
        for r in control_results:
            sm = np.sqrt(r["dx_m"]**2 + r["dy_m"]**2) if np.isfinite(r["dx_m"]) else np.nan
            lines.append(f"| {r['tank_id']} | {r['dx_m']:.1f} | {r['dy_m']:.1f} | "
                         f"{sm:.1f} | {r['ncc_score']:.3f} | {r['status']} |\n")
        lines.append("\n")

    # JW/YB results
    import pandas as pd
    if all_results:
        df = pd.DataFrame(all_results)
        for tid, grp in df.groupby("terminal_id"):
            n_ok    = (grp["status"] == "ok").sum()
            n_low   = (grp["status"] == "low-confidence").sum()
            n_excl  = (grp["status"] == "excluded").sum()
            lines.append(f"## {tid} — {len(grp)} tanks\n\n")
            lines.append(f"ok: {n_ok} / low-confidence: {n_low} / excluded: {n_excl}\n\n")
            lines.append("| tank_id | old lat | old lon | dx_m | dy_m | |shift|_m | NCC | status | new lat | new lon |\n")
            lines.append("|---------|---------|---------|------|------|----------|-----|--------|---------|----------|\n")
            for _, r in grp.iterrows():
                sm = np.sqrt(r.dx_m**2 + r.dy_m**2) if np.isfinite(r.dx_m) else np.nan
                lines.append(
                    f"| {r.tank_id} | {r.lat_old:.6f} | {r.lon_old:.6f} | "
                    f"{r.dx_m:.1f} | {r.dy_m:.1f} | {sm:.1f} | "
                    f"{r.ncc_score:.3f} | {r.status} | "
                    f"{r.lat_new:.9f} | {r.lon_new:.9f} |\n"
                )
            lines.append("\n")

    out = OUT_DIR / "RECENTER_REPORT.md"
    out.write_text("".join(lines), encoding="utf-8")
    print(f"\nReport: {out}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminals", default="juwaymah,yanbu",
                        help="Comma-separated terminal IDs to recentre")
    parser.add_argument("--control-only", action="store_true",
                        help="Only run RT control check, skip JW/YB")
    args = parser.parse_args()

    terminal_ids = [t.strip() for t in args.terminals.split(",")]
    term_map = {t["id"]: t for t in ALL_TERMINALS}

    control_results = []
    all_results = []

    # Always run RT control (10 tanks from south cluster)
    if "ras_tanura" not in terminal_ids:
        rt = term_map.get("ras_tanura")
        if rt:
            print("\n[RT control]")
            control_results = process_terminal(rt, control_only=True)
            # Check PASS/FAIL
            shifts = [np.sqrt(r["dx_m"]**2 + r["dy_m"]**2)
                      for r in control_results if np.isfinite(r["dx_m"])]
            if shifts:
                med_px = np.median(shifts) / RESOLUTION
                if med_px >= 1.0:
                    print(f"\n[RT control FAIL] Median shift = {med_px:.2f} px ≥ 1 px. ABORT.")
                    write_report([], control_results)
                    sys.exit(1)
                print(f"[RT control PASS] Median shift = {med_px:.2f} px < 1 px.")

    if not args.control_only:
        for tid in terminal_ids:
            if tid == "ras_tanura":
                rt = term_map.get(tid)
                if rt:
                    print(f"\n[{tid} control-only mode]")
                    control_results = process_terminal(rt, control_only=True)
                continue
            term = term_map.get(tid)
            if not term:
                print(f"SKIP: {tid} not in registry")
                continue
            print(f"\n[{tid}]")
            results = process_terminal(term, control_only=False)
            all_results.extend(results)

    write_report(all_results, control_results)

    # Smoke test: PNGs must have std > 0
    import PIL.Image
    pngs = list(CROPS_DIR.glob("*.png"))
    print(f"\nCrops: {len(pngs)} PNGs")
    for p in pngs[:3]:
        arr = np.array(PIL.Image.open(p))
        assert arr.std() > 0, f"smoke-test FAIL: {p.name} std=0"
        print(f"  smoke-test OK: {p.name} std={arr.std():.1f}")


if __name__ == "__main__":
    main()
