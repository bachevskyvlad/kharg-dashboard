"""
berth_diag_roi.py — ROI pixel diagnostics for 4 missed sea_island dates.

For each date × orbit, prints:
  - n_px_above_thr:   pixels in sea_island corridor with ratio_dB > RATIO_THR
  - max_ratio_in_roi: max ratio_dB inside corridor
  - max_ratio_ring:   max ratio_dB in 300m ring outside corridor (possible ship outside mask)
  - bias_water:       median ratio_dB in water ring (wind normalisation offset)
  - raw_max_dB:       max raw dB in corridor (before bg subtraction)

Shares all constants and helpers with berth_detect_v410.py.
No output files written — stdout only.
"""

import os, sys
from pathlib import Path
from datetime import datetime, timedelta
import json
import numpy as np
from scipy import ndimage

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
from shapely.ops import unary_union
import shapely

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_roi_diag", service_url="https://sh.dataspace.copernicus.eu"
)

AOI_BBOX   = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RESOLUTION = 10
SIZE       = bbox_to_dimensions(AOI_BBOX, resolution=RESOLUTION)

BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}

CORRIDOR_HALF_M  = 150
CORRIDOR_END_M   = 100
RATIO_THR        = 10.0
WATER_RING_INNER_M = 300
WATER_RING_OUTER_M = 800

BG_PCT            = 20
BG_WINDOW_DAYS_1  = 365 * 2
BG_WINDOW_DAYS_2  = 365 * 4
BG_MIN_DATES      = 10
BG_MAX_SCENES     = 25

ROOT     = Path(__file__).parent.parent
BG_CACHE = ROOT / "diagnostics" / "berth_v3" / "bg_cache"

EVALSCRIPT_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
"""

TARGET_DATES = [
    ("2023-07-09", 101),
    ("2024-06-29", 35),
    ("2025-01-07", 35),
    ("2025-01-11", 101),
]

# ── Geometry helpers (identical to berth_detect_v410) ─────────────────────────
def metres_per_deg_lat():
    return 111320.0

def metres_per_deg_lon(lat):
    return 111320.0 * np.cos(np.deg2rad(lat))

def _extend_line(pts_lonlat, end_m):
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    mpdlat = metres_per_deg_lat()
    mpdlon = metres_per_deg_lon(lat_mid)
    def extend_end(a, b, dist_m):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat
        length = np.hypot(dx,dy)
        if length < 1e-6: return b
        ux,uy = dx/length, dy/length
        return (b[0]+ux*dist_m/mpdlon, b[1]+uy*dist_m/mpdlat)
    pts = list(pts_lonlat)
    return [extend_end(pts[1],pts[0],end_m)] + pts[1:-1] + [extend_end(pts[-2],pts[-1],end_m)]

def build_corridor_polygon(pts_lonlat, half_m, end_m):
    ext_pts = _extend_line(pts_lonlat, end_m)
    line = LineString(ext_pts)
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    buf_deg = ((half_m/metres_per_deg_lat()) + (half_m/metres_per_deg_lon(lat_mid))) / 2
    return line.buffer(buf_deg), ext_pts

def rasterise_poly(poly):
    h, w = SIZE[1], SIZE[0]
    lons = np.linspace(AOI_BBOX.min_x+0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w,
                       AOI_BBOX.max_x-0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w, w)
    lats = np.linspace(AOI_BBOX.max_y-0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h,
                       AOI_BBOX.min_y+0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    try:
        return shapely.contains_xy(poly, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        from shapely.vectorized import contains
        return contains(poly, lon_g, lat_g)

# ── Catalog search ─────────────────────────────────────────────────────────────
def catalog_search(date_str):
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
        fields={"include": ["id","properties.datetime",
                            "properties.sat:relative_orbit",
                            "properties.platform"]},
    ))
    out = []
    for r in results:
        p = r.get("properties", {})
        out.append({
            "scene_id":  r.get("id",""),
            "datetime":  p.get("datetime",""),
            "rel_orbit": int(p.get("sat:relative_orbit", 0)),
        })
    return out

# ── Fetch linear ──────────────────────────────────────────────────────────────
def fetch_linear(dt_str):
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_LINEAR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "),
                           dt_str[:19].replace("T"," ")),
            other_args={"processing": {"backCoeff": "SIGMA0_ELLIPSOID",
                                       "orthorectify": True,
                                       "demInstance": "COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=AOI_BBOX, size=SIZE, config=config,
    )
    data = req.get_data()
    arr = data[0]
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return arr.astype(np.float32)

# ── BG cache ──────────────────────────────────────────────────────────────────
def load_bg(date_str, orbit):
    # try v410 cache first (_w2), then v3
    for suffix in [f"_w2.npy", f".npy"]:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        # try ±24mo then ±48mo
        for window in [BG_WINDOW_DAYS_1, BG_WINDOW_DAYS_2]:
            ym = f"{dt.year:04d}-{dt.month:02d}"
            fname = f"bg_o{orbit}_{ym}{'' if suffix=='.npy' else '_w2'}.npy"
            p = BG_CACHE / fname
            if p.exists():
                bg = np.load(p)
                print(f"  bg cache hit: {p.name}")
                return bg
    # build
    catalog = SentinelHubCatalog(config=config)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    t0 = dt - timedelta(days=BG_WINDOW_DAYS_1)
    t1 = dt + timedelta(days=BG_WINDOW_DAYS_1)
    scenes = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(t0.strftime("%Y-%m-%dT00:00:00Z"), t1.strftime("%Y-%m-%dT23:59:59Z")),
        fields={"include": ["id","properties.datetime","properties.sat:relative_orbit"]},
    ))
    orbit_sc = [s for s in scenes if int(s["properties"].get("sat:relative_orbit",0))==orbit
                and s["properties"]["datetime"][:10] != date_str]
    if len(orbit_sc) > BG_MAX_SCENES:
        step = len(orbit_sc)/BG_MAX_SCENES
        orbit_sc = [orbit_sc[int(i*step)] for i in range(BG_MAX_SCENES)]
    print(f"  building bg orbit={orbit}: {len(orbit_sc)} scenes")
    stack = []
    for s in orbit_sc:
        d = fetch_linear(s["properties"]["datetime"])
        if d is not None:
            stack.append(d)
    if not stack:
        return None
    bg = np.percentile(np.stack(stack, axis=0), BG_PCT, axis=0).astype(np.float32)
    return bg

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Build corridor masks
    corridor_polys = {}
    corridor_masks = {}
    for berth, pts in BERTH_SPEC.items():
        poly, _ = build_corridor_polygon(pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
        corridor_polys[berth] = poly
        corridor_masks[berth] = rasterise_poly(poly)

    # Build water ring masks
    lat_mid = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    buf_avg = lambda m: m / ((mpdlat+mpdlon)/2)
    all_poly = unary_union(list(corridor_polys.values()))
    ring_masks = {}
    for berth, poly in corridor_polys.items():
        outer = poly.buffer(buf_avg(WATER_RING_OUTER_M))
        inner = poly.buffer(buf_avg(WATER_RING_INNER_M))
        ring_masks[berth] = rasterise_poly(outer.difference(inner).difference(all_poly))

    # Build 300m immediate ring (just outside corridor, within 300m)
    near_ring_masks = {}
    for berth, poly in corridor_polys.items():
        outer = poly.buffer(buf_avg(300))
        near_ring_masks[berth] = rasterise_poly(outer.difference(poly))

    print(f"\n{'='*70}")
    print(f"{'date':12s} {'orb':>5} {'n_px_thr':>9} {'max_roi':>8} {'max_ring300':>12} {'bias_water':>11} {'raw_max_dB':>11}")
    print(f"{'-'*70}")

    for date_str, orbit in TARGET_DATES:
        # Fetch scene
        scenes = catalog_search(date_str)
        sc = [s for s in scenes if s["rel_orbit"]==orbit]
        if not sc:
            print(f"{date_str} orbit={orbit}: NO SCENE")
            continue
        meta = sc[0]
        print(f"\n[{date_str}  orbit={orbit}  scene={meta['scene_id']}]")

        linear = fetch_linear(meta["datetime"])
        if linear is None:
            print("  FETCH FAILED")
            continue

        bg = load_bg(date_str, orbit)
        if bg is None:
            print("  BG NOT AVAILABLE")
            continue

        # ratio_dB = 10*log10(scene/bg)  (same as main script)
        ratio = np.where((linear > 0) & (bg > 0),
                         10 * np.log10(linear / bg),
                         np.nan)

        # Wind normalisation bias
        berth = "sea_island"
        ring = ring_masks.get(berth)
        if ring is not None and ring.sum() > 0:
            water_vals = ratio[ring]
            water_vals = water_vals[np.isfinite(water_vals)]
            bias = float(np.nanmedian(water_vals)) if water_vals.size else 0.0
        else:
            bias = 0.0

        ratio_norm = ratio.copy()
        ratio_norm[corridor_masks[berth]] = ratio[corridor_masks[berth]] - bias

        cmask = corridor_masks[berth]
        roi_vals = ratio_norm[cmask]
        roi_vals_finite = roi_vals[np.isfinite(roi_vals)]

        n_above = int((roi_vals_finite > RATIO_THR).sum())
        max_roi = float(np.nanmax(roi_vals_finite)) if roi_vals_finite.size else float('nan')

        near = near_ring_masks.get(berth)
        if near is not None and near.sum() > 0:
            ring_vals = ratio_norm[near]
            ring_vals = ring_vals[np.isfinite(ring_vals)]
            max_ring = float(np.nanmax(ring_vals)) if ring_vals.size else float('nan')
        else:
            max_ring = float('nan')

        # raw dB in ROI
        raw_dB = np.where(linear > 0, 10*np.log10(linear), np.nan)
        raw_max = float(np.nanmax(raw_dB[cmask])) if cmask.sum() > 0 else float('nan')

        print(f"  n_px_above_thr (ratio>{RATIO_THR:.0f}dB): {n_above}")
        print(f"  max ratio_dB in ROI (normalised):       {max_roi:.2f} dB")
        print(f"  max ratio_dB in 300m ring (outside ROI):{max_ring:.2f} dB")
        print(f"  wind bias (water ring median):           {bias:.2f} dB")
        print(f"  raw max dB in ROI:                       {raw_max:.2f} dB")
        print(f"  ROI px total: {int(cmask.sum())}")
        # histogram of ratio in ROI
        hist, edges = np.histogram(roi_vals_finite, bins=10, range=(-10, 40))
        print(f"  ratio histogram (ROI, normalised):")
        for i,(h,e0,e1) in enumerate(zip(hist, edges[:-1], edges[1:])):
            bar = "#"*int(h/max(hist.max(),1)*20) if hist.max()>0 else ""
            print(f"    [{e0:5.1f},{e1:5.1f}): {h:4d}  {bar}")

    print(f"\n{'='*70}")

if __name__ == "__main__":
    main()
