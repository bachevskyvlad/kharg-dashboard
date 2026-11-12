"""
berth_v4_history.py — v4 detection on all S1 scenes 2024-01-01 → today.

v4 config (fixed per berth):
  sea_island:     BG_V3  pier=20 water=20-80 len≥150 merge=0
  east_t_head:    BG_V4  pier=20 water=20-80 len≥150 merge=0  ← EMP background
  southeast_pier: BG_V3  pier=20 water=20-80 len≥150 merge=0

Output:
  diagnostics/berth_v4/history_v4/berth_v4_history.csv
  data/berth_occupancy.json  (site widget)
"""

import os, sys, csv, json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
import shapely

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url  = "https://sh.dataspace.copernicus.eu"
if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID / CLIENT_SECRET not set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_v4hist", service_url="https://sh.dataspace.copernicus.eu")

ROOT    = Path(__file__).parent.parent
BG_V3   = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4   = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "history_v4"
JSON_OUT = ROOT / "data" / "berth_occupancy.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RESOLUTION      = 10
CELL_KM         = 1.0
CORRIDOR_END_M  = 250
RATIO_THR       = 10.0
HISTORY_START   = "2024-01-01"
ORBITS          = [35, 101]
struct8 = ndimage.generate_binary_structure(2, 2)

BERTHS = {
    "sea_island":      [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":     [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier":  [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}

V4_CONFIG = {
    "sea_island":     {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "bg_dir": BG_V3},
    "east_t_head":    {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "bg_dir": BG_V4},
    "southeast_pier": {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "bg_dir": BG_V3},
}

AOI_BBOX = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

EVALSCRIPT_S1 = """
//VERSION=3
function setup() { return { input: ["VV"], output: { bands:1, sampleType:"FLOAT32" } }; }
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

# ── Geometry helpers (identical to berth_ablation.py) ─────────────────────────

def metres_per_deg_lat(): return 111320.0
def metres_per_deg_lon(lat): return 111320.0 * np.cos(np.deg2rad(lat))
def berth_center(pts): return np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])

def cell_bbox_for(clon, clat, km=CELL_KM):
    half_lat = (km * 500) / metres_per_deg_lat()
    half_lon = (km * 500) / metres_per_deg_lon(clat)
    return BBox(bbox=[clon-half_lon, clat-half_lat, clon+half_lon, clat+half_lat], crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat; L = np.hypot(dx, dy)
        if L < 1e-6: return b
        return (b[0]+dx/L*d/mpdlon, b[1]+dy/L*d/mpdlat)
    p = list(pts)
    return [ext(p[1],p[0],end_m)] + p[1:-1] + [ext(p[-2],p[-1],end_m)]

def rasterize_poly(poly, bbox_obj, img_shape):
    h, w = img_shape
    lons = np.linspace(bbox_obj.min_x+0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x-0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y-0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y+0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    try:
        return shapely.contains_xy(poly, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        from shapely.vectorized import contains
        return contains(poly, lon_g, lat_g)

def compute_signed_dist_field(px_x, px_y, axis_m_pts):
    min_dist = np.full(px_x.shape, np.inf)
    for i in range(len(axis_m_pts)-1):
        x1,y1=axis_m_pts[i]; x2,y2=axis_m_pts[i+1]
        dx=x2-x1; dy=y2-y1; L=np.hypot(dx,dy)
        if L < 1e-6: continue
        ux=dx/L; uy=dy/L
        vx=px_x-x1; vy=px_y-y1
        t=np.clip((vx*ux+vy*uy)/L, 0, 1)
        cx=x1+t*dx; cy=y1+t*dy
        dist=np.hypot(px_x-cx, px_y-cy)
        update=dist < min_dist
        min_dist=np.where(update, dist, min_dist)
    return min_dist

def build_geom(pts, bbox_obj, img_shape, pier_excl_m, water_inner_m, water_outer_m):
    h, w = img_shape
    lat_mid = (bbox_obj.min_y+bbox_obj.max_y)/2
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    clon, clat = berth_center(pts)
    ext_pts = _extend_line(pts, CORRIDOR_END_M)
    axis_m = [((lon-clon)*mpdlon, (lat-clat)*mpdlat) for lon, lat in ext_pts]
    lons = np.linspace(bbox_obj.min_x+0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x-0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y-0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y+0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px_x = (lon_g-clon)*mpdlon; px_y = (lat_g-clat)*mpdlat
    abs_dist = compute_signed_dist_field(px_x, px_y, axis_m)
    corridor = LineString(ext_pts).buffer(
        ((150/mpdlat)+(150/mpdlon))/2)
    corridor_mask = rasterize_poly(corridor, bbox_obj, img_shape)
    pc_s = ((ext_pts[0][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[0][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    pc_e = ((ext_pts[-1][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[-1][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    dcol=pc_e[0]-pc_s[0]; drow=pc_e[1]-pc_s[1]; L_px=np.hypot(dcol,drow)
    ux = dcol/L_px if L_px>1e-6 else 0.0
    uy = drow/L_px if L_px>1e-6 else 1.0
    return {
        "water_mask": (abs_dist >= water_inner_m) & (abs_dist <= water_outer_m) & corridor_mask,
        "axis_uvec": (ux, uy),
    }

MERGE_PERP_TOL_M = 30.0

def find_blobs(ratio_arr, water_mask, axis_uvec, merge_gap_m, min_len_m, min_aspect=3.0):
    ux, uy = axis_uvec; px_u, py_u = -uy, ux
    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)
    if n == 0: return [], None, False
    raw = []
    for bid in range(1, n+1):
        mask = labeled == bid
        rows, cols = np.where(mask)
        if len(rows) == 0: continue
        fc=cols.astype(float); fr=rows.astype(float)
        axis_p = fc*ux + fr*uy; perp_p = fc*px_u + fr*py_u
        a_min=float(axis_p.min()); a_max=float(axis_p.max())
        p_min=float(perp_p.min()); p_max=float(perp_p.max())
        len_m = (a_max-a_min)*RESOLUTION
        wid_m = max((p_max-p_min)*RESOLUTION, 1.0)
        raw.append({"axis_min":a_min,"axis_max":a_max,"perp_ctr":(p_min+p_max)/2,
                    "length_m":len_m,"width_m":wid_m,"aspect":len_m/wid_m,
                    "area_px":int(mask.sum())})
    raw.sort(key=lambda b: b["axis_min"])
    if merge_gap_m <= 0:
        groups = [[b] for b in raw]
    else:
        gap_px   = merge_gap_m / RESOLUTION
        perp_tol = MERGE_PERP_TOL_M / RESOLUTION
        groups = []; cur = [raw[0]]
        for b in raw[1:]:
            cur_max = max(bb["axis_max"] for bb in cur)
            cur_perp = float(np.mean([bb["perp_ctr"] for bb in cur]))
            if b["axis_min"] - cur_max <= gap_px and abs(b["perp_ctr"] - cur_perp) <= perp_tol:
                cur.append(b)
            else:
                groups.append(cur); cur = [b]
        groups.append(cur)
    blobs = []
    for grp in groups:
        a_min=min(b["axis_min"] for b in grp); a_max=max(b["axis_max"] for b in grp)
        length_m=(a_max-a_min)*RESOLUTION
        width_m=max(float(np.median([b["width_m"] for b in grp])), 1.0)
        best_aspect=max(b["aspect"] for b in grp)
        area_px=sum(b["area_px"] for b in grp)
        blobs.append({"length_m":length_m,"width_m":width_m,"aspect":best_aspect,
                      "area_px":area_px,"n_merged":len(grp)})
    blobs.sort(key=lambda b: b["length_m"], reverse=True)
    best = blobs[0] if blobs else None
    detected = bool(best and best["length_m"] >= min_len_m and best["aspect"] >= min_aspect)
    return blobs, best, detected

# ── Catalog & fetch ────────────────────────────────────────────────────────────

def discover_dates(date_from, date_to):
    catalog = SentinelHubCatalog(config=config)
    today = date_to
    print(f"Discovering S1 catalog {date_from} → {today} ...")
    all_scenes = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(date_from+"T00:00:00Z", today+"T23:59:59Z"),
        fields={"include":["properties.datetime","properties.sat:relative_orbit"]},
    ))
    print(f"  total scenes: {len(all_scenes)}")
    grouped = {}
    for s in all_scenes:
        p = s.get("properties", {})
        orb = p.get("sat:relative_orbit")
        if orb not in ORBITS: continue
        dt_raw = p.get("datetime", "")
        try:
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.strptime(dt_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        key = (date_str, orb)
        if key not in grouped or dt_raw < grouped[key]["dt_raw"]:
            grouped[key] = {"date_str": date_str, "orbit": orb,
                            "scene_dt": dt_raw, "dt_raw": dt_raw}
    result = sorted(grouped.values(), key=lambda x: (x["date_str"], x["orbit"]))
    print(f"  unique (date, orbit) pairs: {len(result)}")
    return result

def fetch_s1_linear(scene_dt, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(scene_dt[:19].replace("T"," "), scene_dt[:19].replace("T"," ")),
            other_args={"processing":{"backCoeff":"SIGMA0_ELLIPSOID","orthorectify":True,
                                      "demInstance":"COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d = req.get_data(); a = d[0]
        if a.ndim == 3: a = a[:,:,0]
        return a.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch err: {e}"); return None

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    entries = discover_dates(HISTORY_START, today)

    # Pre-load BG per (berth, orbit)
    bg_cache = {}
    for berth, cfg in V4_CONFIG.items():
        for orbit in ORBITS:
            p = cfg["bg_dir"] / f"bg_o{orbit}_{berth}.npy"
            bg_cache[(berth, orbit)] = np.load(p) if p.exists() else None
            if not p.exists():
                print(f"  MISSING BG: {berth} o{orbit} ({cfg['bg_dir'].name})")

    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTHS.items()}

    results = []
    for entry in entries:
        date_str = entry["date_str"]
        orbit    = entry["orbit"]
        scene_dt = entry["scene_dt"]
        print(f"\n{date_str} o{orbit}  scene={scene_dt[:16]}")

        # fetch once per (date, orbit), reuse across berths
        # use sea_island bbox for fetch (all berths share same S1 swath)
        # Actually fetch per berth (different cell bboxes)
        for berth, pts in BERTHS.items():
            cfg = V4_CONFIG[berth]
            bg  = bg_cache.get((berth, orbit))
            if bg is None:
                results.append({"date": date_str, "orbit": orbit, "berth": berth,
                                 "det": "NO_BG", "length_m": None, "aspect": None})
                continue

            linear = fetch_s1_linear(scene_dt, cell_bboxes[berth])
            if linear is None:
                results.append({"date": date_str, "orbit": orbit, "berth": berth,
                                 "det": "ERR", "length_m": None, "aspect": None})
                continue

            if bg.shape != linear.shape:
                bg_r = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                    linear.shape[1]/bg.shape[1]), order=1)
            else:
                bg_r = bg

            ratio = np.where((linear>0)&(bg_r>0),
                             10*np.log10(linear/bg_r), np.nan).astype(np.float32)
            geom = build_geom(pts, cell_bboxes[berth], linear.shape,
                              cfg["pier"], cfg["water_i"], cfg["water_o"])
            _, best, detected = find_blobs(
                ratio, geom["water_mask"], geom["axis_uvec"],
                merge_gap_m=0, min_len_m=cfg["min_len"])

            det_str = "OCC" if detected else "EMP"
            length_m = round(best["length_m"], 1) if (detected and best) else None
            aspect   = round(best["aspect"], 2) if (detected and best) else None
            results.append({"date": date_str, "orbit": orbit, "berth": berth,
                             "det": det_str, "length_m": length_m, "aspect": aspect})
            print(f"  {berth}: {det_str}" +
                  (f"  len={length_m:.0f}m asp={aspect:.1f}" if detected else ""))

    # ── CSV output ────────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "berth_v4_history.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date","orbit","berth","det","length_m","aspect"])
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"\nSaved: {csv_path} ({len(results)} rows)")

    # ── JSON output for site ──────────────────────────────────────────────────
    # Pivot to {date, orbit} → {berth: det}
    pivot = {}
    for r in results:
        key = (r["date"], r["orbit"])
        if key not in pivot:
            pivot[key] = {"date": r["date"], "orbit": r["orbit"]}
        pivot[key][r["berth"]] = r["det"]

    rows_sorted = sorted(pivot.values(), key=lambda x: (x["date"], x["orbit"]))

    # Summary stats
    occ_total = sum(1 for r in results if r["det"] == "OCC")
    total_valid = sum(1 for r in results if r["det"] in ("OCC","EMP"))

    obj = {
        "generated": today,
        "version": "v4.1",
        "note": "lower bound — detector misses ~1/3 of ships; precision ~92% when OCC",
        "berths": ["sea_island", "east_t_head", "southeast_pier"],
        "stats": {
            "total_berth_dates": total_valid,
            "occupied_detections": occ_total,
        },
        "rows": rows_sorted,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(obj, separators=(",",":")))
    print(f"Saved: {JSON_OUT} ({len(rows_sorted)} date-orbit pairs)")

    # ── Dry-run summary ───────────────────────────────────────────────────────
    print("\n=== Dry-run summary (DB NOT written) ===")
    print(f"Period: {HISTORY_START} → {today}")
    print(f"Scene-berth pairs processed: {total_valid}")
    print(f"Detections (OCC): {occ_total}")
    for berth in ["sea_island","east_t_head","southeast_pier"]:
        br = [r for r in results if r["berth"]==berth and r["det"] in ("OCC","EMP")]
        occ = sum(1 for r in br if r["det"]=="OCC")
        print(f"  {berth}: {occ}/{len(br)} occupied")
    print("To write to DB: set WRITE_DB=1 and re-run (not yet implemented — awaiting 'go K').")


if __name__ == "__main__":
    run()
