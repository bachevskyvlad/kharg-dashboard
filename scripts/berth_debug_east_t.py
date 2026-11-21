"""
berth_debug_east_t.py — Debug east_t_head on 2025-07-22 o101.

v3 saw a 280m ship. v4 recall=0 for all east_t_head.
Outputs:
  diagnostics/berth_v4/debug_east_t/
    panels.png        — scene dB | BG v3 dB | ratio v3 | water masks + blobs
    blob_stats.txt    — numerical values at ship pixel, blob list
    s2_reference.png  — S2 RGB same day (check bright spot at south end)
"""

import os, sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
import shapely

config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url  = "https://sh.dataspace.copernicus.eu"
if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID / CLIENT_SECRET not set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_dbg", service_url="https://sh.dataspace.copernicus.eu")
S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2l2a_dbg", service_url="https://sh.dataspace.copernicus.eu")

ROOT    = Path(__file__).parent.parent
BG_V3   = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4   = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "debug_east_t"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_DATE  = "2025-07-22"
DEBUG_ORBIT = 101
BERTH       = "east_t_head"
BERTH_PTS   = [(50.33833, 29.23812), (50.34054, 29.22189)]

RESOLUTION      = 10
CELL_KM         = 1.0
CORRIDOR_HALF_M = 150
CORRIDOR_END_M  = 250
RATIO_THR       = 10.0
struct8 = ndimage.generate_binary_structure(2, 2)

EVALSCRIPT_S1 = """
//VERSION=3
function setup() { return { input: ["VV"], output: { bands:1, sampleType:"FLOAT32" } }; }
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""
EVALSCRIPT_S2 = """
//VERSION=3
function setup() {
  return { input: [{bands:["B04","B03","B02"]}], output:{bands:3,sampleType:"FLOAT32"} };
}
function evaluatePixel(s) { return [s.B04*3.5, s.B03*3.5, s.B02*3.5]; }
"""

def metres_per_deg_lat(): return 111320.0
def metres_per_deg_lon(lat): return 111320.0 * np.cos(np.deg2rad(lat))

def berth_center(pts):
    return np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])

def cell_bbox_for(clon, clat, km=CELL_KM):
    half_lat = (km*500)/metres_per_deg_lat()
    half_lon = (km*500)/metres_per_deg_lon(clat)
    return BBox(bbox=[clon-half_lon, clat-half_lat, clon+half_lon, clat+half_lat], crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx=(b[0]-a[0])*mpdlon; dy=(b[1]-a[1])*mpdlat; L=np.hypot(dx,dy)
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
    min_dist = np.full(px_x.shape, np.inf); sign = np.ones(px_x.shape)
    for i in range(len(axis_m_pts)-1):
        x1,y1=axis_m_pts[i]; x2,y2=axis_m_pts[i+1]
        dx=x2-x1; dy=y2-y1; L=np.hypot(dx,dy)
        if L<1e-6: continue
        ux=dx/L; uy=dy/L; rx=uy; ry=-ux
        vx=px_x-x1; vy=px_y-y1
        t=np.clip((vx*ux+vy*uy)/L, 0, 1)
        cx=x1+t*dx; cy=y1+t*dy
        dpx=px_x-cx; dpy=px_y-cy
        dist=np.hypot(dpx,dpy)
        sgn=np.where(dpx*rx+dpy*ry>=0, 1.0, -1.0)
        update=dist<min_dist
        min_dist=np.where(update,dist,min_dist); sign=np.where(update,sgn,sign)
    return min_dist * sign

def build_masks(pts, bbox_obj, img_shape, pier_excl_m, water_inner_m, water_outer_m):
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
    dist_arr = compute_signed_dist_field(px_x, px_y, axis_m)
    corridor = LineString(ext_pts).buffer(
        ((CORRIDOR_HALF_M/metres_per_deg_lat())+(CORRIDOR_HALF_M/metres_per_deg_lon(lat_mid)))/2)
    corridor_mask = rasterize_poly(corridor, bbox_obj, img_shape)
    abs_dist = np.abs(dist_arr)
    pc_s = ((ext_pts[0][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[0][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    pc_e = ((ext_pts[-1][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[-1][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    dcol=pc_e[0]-pc_s[0]; drow=pc_e[1]-pc_s[1]; L_px=np.hypot(dcol,drow)
    ux = dcol/L_px if L_px>1e-6 else 0.0
    uy = drow/L_px if L_px>1e-6 else 1.0
    return {
        "abs_dist":    abs_dist,
        "pier_mask":   (abs_dist < pier_excl_m) & corridor_mask,
        "water_mask":  (abs_dist >= water_inner_m) & (abs_dist <= water_outer_m) & corridor_mask,
        "corridor":    corridor_mask,
        "axis_uvec":   (ux, uy),
    }

def find_blobs(ratio_arr, water_mask, axis_uvec, merge_gap_m=0):
    ux, uy = axis_uvec; px_u, py_u = -uy, ux
    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)
    if n == 0:
        return [], None, labeled
    raw = []
    for bid in range(1, n+1):
        mask = labeled == bid
        rows, cols = np.where(mask)
        if len(rows) == 0: continue
        fc=cols.astype(float); fr=rows.astype(float)
        axis_p = fc*ux + fr*uy; perp_p = fc*px_u + fr*py_u
        eroded = ndimage.binary_erosion(mask, structure=struct8)
        raw.append({
            "axis_min": float(axis_p.min()), "axis_max": float(axis_p.max()),
            "perp_min": float(perp_p.min()), "perp_max": float(perp_p.max()),
            "row_min": int(rows.min()), "row_max": int(rows.max()),
            "col_min": int(cols.min()), "col_max": int(cols.max()),
            "area_px": int(mask.sum()), "border": mask & ~eroded,
        })
    raw.sort(key=lambda b: b["axis_min"])
    if merge_gap_m <= 0:
        groups = [[b] for b in raw]
    else:
        gap_px = merge_gap_m / RESOLUTION
        groups = []; cur = [raw[0]]
        for b in raw[1:]:
            cur_max = max(bb["axis_max"] for bb in cur)
            if b["axis_min"] - cur_max <= gap_px:
                cur.append(b)
            else:
                groups.append(cur); cur = [b]
        groups.append(cur)
    blobs = []
    for grp in groups:
        a_min=min(b["axis_min"] for b in grp); a_max=max(b["axis_max"] for b in grp)
        p_min=min(b["perp_min"] for b in grp); p_max=max(b["perp_max"] for b in grp)
        length_m=(a_max-a_min)*RESOLUTION; width_m=max((p_max-p_min)*RESOLUTION, 1.0)
        aspect=length_m/width_m; area_px=sum(b["area_px"] for b in grp)
        r0=min(b["row_min"] for b in grp); r1=max(b["row_max"] for b in grp)
        c0=min(b["col_min"] for b in grp); c1=max(b["col_max"] for b in grp)
        h, w = ratio_arr.shape
        border=np.zeros((h,w), dtype=bool)
        for b in grp: border |= b["border"]
        blobs.append({"length_m":length_m,"width_m":width_m,"aspect":aspect,
                      "area_px":area_px,"border":border,"bbox":(r0,r1,c0,c1),
                      "n_merged": len(grp)})
    blobs.sort(key=lambda b: b["length_m"], reverse=True)
    return blobs, (blobs[0] if blobs else None), labeled

def scene_dt_s1(date_str, orbit, bbox):
    cat = SentinelHubCatalog(config=config)
    try:
        res = list(cat.search(S1_CDSE, bbox=bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.sat:relative_orbit"]}))
    except Exception as e:
        print(f"  S1 catalog: {e}"); return ""
    for r in res:
        p = r.get("properties",{})
        if int(p.get("sat:relative_orbit",0)) == orbit:
            return p.get("datetime","")
    return ""

def scene_dt_s2(date_str, bbox):
    cat = SentinelHubCatalog(config=config)
    try:
        res = list(cat.search(S2_CDSE, bbox=bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.eo:cloud_cover"]}))
    except Exception as e:
        print(f"  S2 catalog: {e}"); return ""
    best_dt=""; best_cc=999.0
    for r in res:
        p=r.get("properties",{}); cc=float(p.get("eo:cloud_cover",100))
        if cc<best_cc: best_cc=cc; best_dt=p.get("datetime","")
    return best_dt

def fetch_s1_linear(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing":{"backCoeff":"SIGMA0_ELLIPSOID","orthorectify":True,
                                      "demInstance":"COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d=req.get_data(); a=d[0]
        if a.ndim==3: a=a[:,:,0]
        return a.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch: {e}"); return None

def fetch_s2_rgb(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d=req.get_data(); a=d[0]
        if a.ndim==2: a=np.stack([a]*3, axis=-1)
        return np.clip(a, 0, 1).astype(np.float32)
    except Exception as e:
        print(f"  S2 fetch: {e}"); return None


def main():
    bbox_obj = cell_bbox_for(*berth_center(BERTH_PTS))
    aoi      = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

    print(f"=== Debug: {DEBUG_DATE} o{DEBUG_ORBIT} {BERTH} ===")

    # Fetch scene
    s1_dt = scene_dt_s1(DEBUG_DATE, DEBUG_ORBIT, aoi)
    print(f"S1 dt: {s1_dt}")
    linear = fetch_s1_linear(s1_dt, bbox_obj) if s1_dt else None
    if linear is None:
        sys.exit("FATAL: S1 fetch failed")
    print(f"Scene shape: {linear.shape}  dtype: {linear.dtype}")
    print(f"Scene linear: min={linear.min():.6f}  max={linear.max():.6f}  "
          f"mean={linear.mean():.6f}")
    scene_db = np.where(linear > 0, 10*np.log10(linear), np.nan)
    print(f"Scene dB:     min={np.nanmin(scene_db):.1f}  max={np.nanmax(scene_db):.1f}  "
          f"mean={np.nanmean(scene_db):.1f}")

    # Fetch S2 (same day)
    s2_dt = scene_dt_s2(DEBUG_DATE, aoi)
    print(f"S2 dt: {s2_dt}")
    rgb = fetch_s2_rgb(s2_dt, bbox_obj) if s2_dt else None

    # Load BG v3
    bg_v3_path = BG_V3 / f"bg_o{DEBUG_ORBIT}_{BERTH}.npy"
    bg_v4_path = BG_V4 / f"bg_o{DEBUG_ORBIT}_{BERTH}.npy"
    print(f"\nBG v3 path: {bg_v3_path}  exists={bg_v3_path.exists()}")
    print(f"BG v4 path: {bg_v4_path}  exists={bg_v4_path.exists()}")

    bg_v3 = np.load(bg_v3_path) if bg_v3_path.exists() else None
    bg_v4 = np.load(bg_v4_path) if bg_v4_path.exists() else None

    if bg_v3 is not None:
        if bg_v3.shape != linear.shape:
            bg_v3 = nd_zoom(bg_v3, (linear.shape[0]/bg_v3.shape[0],
                                     linear.shape[1]/bg_v3.shape[1]), order=1)
        bg_v3_db = np.where(bg_v3 > 0, 10*np.log10(bg_v3), np.nan)
        print(f"\nBG v3 linear: min={bg_v3.min():.6f}  max={bg_v3.max():.6f}  "
              f"mean={bg_v3.mean():.6f}")
        print(f"BG v3 dB:     min={np.nanmin(bg_v3_db):.1f}  max={np.nanmax(bg_v3_db):.1f}  "
              f"mean={np.nanmean(bg_v3_db):.1f}")
    else:
        bg_v3_db = None; print("BG v3: MISSING")

    if bg_v4 is not None:
        if bg_v4.shape != linear.shape:
            bg_v4 = nd_zoom(bg_v4, (linear.shape[0]/bg_v4.shape[0],
                                     linear.shape[1]/bg_v4.shape[1]), order=1)
        bg_v4_db = np.where(bg_v4 > 0, 10*np.log10(bg_v4), np.nan)
        print(f"\nBG v4 linear: min={bg_v4.min():.6f}  max={bg_v4.max():.6f}  "
              f"mean={bg_v4.mean():.6f}")
        print(f"BG v4 dB:     min={np.nanmin(bg_v4_db):.1f}  max={np.nanmax(bg_v4_db):.1f}  "
              f"mean={np.nanmean(bg_v4_db):.1f}")
    else:
        bg_v4_db = None; print("BG v4: MISSING")

    # Ratio v3
    ratio_v3 = None
    if bg_v3 is not None:
        ratio_v3 = np.where((linear>0)&(bg_v3>0),
                            10*np.log10(linear/bg_v3), np.nan).astype(np.float32)
        print(f"\nRatio v3 dB:  min={np.nanmin(ratio_v3):.1f}  "
              f"max={np.nanmax(ratio_v3):.1f}  mean={np.nanmean(ratio_v3):.1f}")
        pct_above = np.sum(np.isfinite(ratio_v3) & (ratio_v3 > RATIO_THR))
        print(f"  pixels > {RATIO_THR} dB: {pct_above}")

    # Ratio v4
    ratio_v4 = None
    if bg_v4 is not None:
        ratio_v4 = np.where((linear>0)&(bg_v4>0),
                            10*np.log10(linear/bg_v4), np.nan).astype(np.float32)
        print(f"\nRatio v4 dB:  min={np.nanmin(ratio_v4):.1f}  "
              f"max={np.nanmax(ratio_v4):.1f}  mean={np.nanmean(ratio_v4):.1f}")
        pct_above = np.sum(np.isfinite(ratio_v4) & (ratio_v4 > RATIO_THR))
        print(f"  pixels > {RATIO_THR} dB: {pct_above}")

    # Water masks: v3 params (pier=20, water=20-80) and v4 params (pier=12, water=12-90)
    geom_v3 = build_masks(BERTH_PTS, bbox_obj, linear.shape,
                          pier_excl_m=20, water_inner_m=20, water_outer_m=80)
    geom_v4 = build_masks(BERTH_PTS, bbox_obj, linear.shape,
                          pier_excl_m=12, water_inner_m=12, water_outer_m=90)

    print(f"\nWater mask v3 (20-80m): {geom_v3['water_mask'].sum()} px")
    print(f"Water mask v4 (12-90m): {geom_v4['water_mask'].sum()} px")

    # Blobs in ratio_v3, water v3 — no merge (v3)
    blobs_v3_nomerg, long_v3, labeled_v3 = find_blobs(
        ratio_v3, geom_v3["water_mask"], geom_v3["axis_uvec"], merge_gap_m=0) \
        if ratio_v3 is not None else ([], None, None)
    # Blobs in ratio_v3, water v3 — with merge (v4 merge logic)
    blobs_v3_merged, long_v3m, _ = find_blobs(
        ratio_v3, geom_v3["water_mask"], geom_v3["axis_uvec"], merge_gap_m=40) \
        if ratio_v3 is not None else ([], None, None)
    # Blobs in ratio_v4, water v4 — with merge
    blobs_v4, long_v4, labeled_v4 = find_blobs(
        ratio_v4, geom_v4["water_mask"], geom_v4["axis_uvec"], merge_gap_m=40) \
        if ratio_v4 is not None else ([], None, None)

    print(f"\nBlobs v3 BG + v3 water (no merge):  {len(blobs_v3_nomerg)}")
    for b in blobs_v3_nomerg[:5]:
        print(f"  len={b['length_m']:.0f}m  asp={b['aspect']:.1f}  "
              f"area={b['area_px']}px  bbox={b['bbox']}")
    print(f"\nBlobs v3 BG + v3 water (gap<=40m):  {len(blobs_v3_merged)}")
    for b in blobs_v3_merged[:5]:
        print(f"  len={b['length_m']:.0f}m  asp={b['aspect']:.1f}  "
              f"area={b['area_px']}px  n_merged={b['n_merged']}  bbox={b['bbox']}")
    print(f"\nBlobs v4 BG + v4 water (gap<=40m):  {len(blobs_v4)}")
    for b in blobs_v4[:5]:
        print(f"  len={b['length_m']:.0f}m  asp={b['aspect']:.1f}  "
              f"area={b['area_px']}px  n_merged={b['n_merged']}  bbox={b['bbox']}")

    # At the longest v3 blob — print pixel values
    if long_v3 and ratio_v3 is not None:
        r0,r1,c0,c1 = long_v3["bbox"]
        cr = (r0+r1)//2; cc2 = (c0+c1)//2
        print(f"\n=== Pixel values at v3 blob center ({cr},{cc2}) ===")
        print(f"  Scene linear: {linear[cr,cc2]:.6f}")
        print(f"  Scene dB:     {scene_db[cr,cc2]:.2f}")
        if bg_v3 is not None:
            print(f"  BG v3 linear: {bg_v3[cr,cc2]:.6f}")
            print(f"  BG v3 dB:     {bg_v3_db[cr,cc2]:.2f}")
            print(f"  Ratio v3 dB:  {ratio_v3[cr,cc2]:.2f}")
        if bg_v4 is not None:
            print(f"  BG v4 linear: {bg_v4[cr,cc2]:.6f}")
            print(f"  BG v4 dB:     {bg_v4_db[cr,cc2]:.2f}")
            if ratio_v4 is not None:
                print(f"  Ratio v4 dB:  {ratio_v4[cr,cc2]:.2f}")
        print(f"  Water v3:     {geom_v3['water_mask'][cr,cc2]}")
        print(f"  Water v4:     {geom_v4['water_mask'][cr,cc2]}")
        print(f"  Abs dist:     {geom_v3['abs_dist'][cr,cc2]:.1f} m")

    # Also print row of max ratio within water v3
    if ratio_v3 is not None:
        ratio_in_water = np.where(geom_v3["water_mask"], ratio_v3, np.nan)
        if np.isfinite(ratio_in_water).any():
            max_idx = np.unravel_index(np.nanargmax(ratio_in_water), ratio_in_water.shape)
            mr, mc = max_idx
            print(f"\n=== Max ratio in v3 water band: row={mr}, col={mc} ===")
            print(f"  Max ratio v3 dB: {ratio_in_water[mr,mc]:.2f}")
            print(f"  Scene dB:        {scene_db[mr,mc]:.2f}")
            if bg_v3 is not None: print(f"  BG v3 dB:        {bg_v3_db[mr,mc]:.2f}")
            if bg_v4 is not None and ratio_v4 is not None:
                r4_in_water = np.where(geom_v4["water_mask"], ratio_v4, np.nan)
                if np.isfinite(r4_in_water).any():
                    mr4, mc4 = np.unravel_index(np.nanargmax(r4_in_water), r4_in_water.shape)
                    print(f"\n=== Max ratio in v4 water band: row={mr4}, col={mc4} ===")
                    print(f"  Max ratio v4 dB: {r4_in_water[mr4,mc4]:.2f}")
                    print(f"  Scene dB:        {scene_db[mr4,mc4]:.2f}")
                    print(f"  BG v4 dB:        {bg_v4_db[mr4,mc4]:.2f}")

    # ── Panels PNG ────────────────────────────────────────────────────────────
    n_cols = 7; fig, axes = plt.subplots(1, n_cols, figsize=(n_cols*2.8, 3.2))
    fig.patch.set_facecolor("#1a1a1a")
    titles = ["S2 RGB", "Scene dB", "BG v3 dB", "Ratio v3", "Ratio v4",
              "Water v3\n(20-80m)", "Water v4\n(12-90m)"]
    for ax, t in zip(axes, titles):
        ax.set_facecolor("#111"); ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(t, color="white", fontsize=7, pad=2)

    # S2
    if rgb is not None:
        axes[0].imshow(rgb, interpolation="nearest", origin="upper")
    else:
        axes[0].text(0.5,0.5,"NO S2",color="#666",ha="center",va="center",
                     transform=axes[0].transAxes)

    # Scene dB
    if np.isfinite(scene_db).any():
        fin = scene_db[np.isfinite(scene_db)]
        axes[1].imshow(scene_db, cmap="gray",
                       vmin=np.percentile(fin,2), vmax=np.percentile(fin,98),
                       interpolation="nearest", origin="upper")

    # BG v3 dB
    if bg_v3_db is not None and np.isfinite(bg_v3_db).any():
        fin = bg_v3_db[np.isfinite(bg_v3_db)]
        axes[2].imshow(bg_v3_db, cmap="gray",
                       vmin=np.percentile(fin,2), vmax=np.percentile(fin,98),
                       interpolation="nearest", origin="upper")
    else:
        axes[2].text(0.5,0.5,"NO BG",color="#666",ha="center",va="center",
                     transform=axes[2].transAxes)

    # Ratio v3
    if ratio_v3 is not None and np.isfinite(ratio_v3).any():
        disp = np.clip(ratio_v3, -15, 30)
        axes[3].imshow(disp, cmap="RdYlGn", vmin=-15, vmax=30,
                       interpolation="nearest", origin="upper")
        try:
            axes[3].contour(ratio_v3, levels=[RATIO_THR], colors=["white"],
                            linewidths=0.8, alpha=0.9)
        except Exception: pass
        try:
            axes[3].contour(geom_v3["water_mask"].astype(float),
                            levels=[0.5], colors=["orange"], linewidths=1.0)
        except Exception: pass
        for b in blobs_v3_nomerg:
            rs2, cs2 = np.where(b["border"])
            axes[3].scatter(cs2, rs2, s=0.5, c="cyan", marker=".", linewidths=0)
        if long_v3:
            r0,r1,c0,c1 = long_v3["bbox"]
            axes[3].add_patch(mpatches.Rectangle((c0-0.5,r0-0.5), c1-c0+1, r1-r0+1,
                lw=1.2, edgecolor="cyan", facecolor="none", zorder=5))
            axes[3].text(0.02,0.98,
                f"len={long_v3['length_m']:.0f}m\nasp={long_v3['aspect']:.1f}",
                color="white", ha="left", va="top", transform=axes[3].transAxes,
                fontsize=5, bbox=dict(fc="black", alpha=0.6, lw=0))

    # Ratio v4
    if ratio_v4 is not None and np.isfinite(ratio_v4).any():
        disp4 = np.clip(ratio_v4, -15, 30)
        axes[4].imshow(disp4, cmap="RdYlGn", vmin=-15, vmax=30,
                       interpolation="nearest", origin="upper")
        try:
            axes[4].contour(ratio_v4, levels=[RATIO_THR], colors=["white"],
                            linewidths=0.8, alpha=0.9)
        except Exception: pass
        try:
            axes[4].contour(geom_v4["water_mask"].astype(float),
                            levels=[0.5], colors=["orange"], linewidths=1.0)
        except Exception: pass
        for b in blobs_v4:
            rs2, cs2 = np.where(b["border"])
            axes[4].scatter(cs2, rs2, s=0.5, c="cyan", marker=".", linewidths=0)
    else:
        axes[4].text(0.5,0.5,"NO RATIO",color="#666",ha="center",va="center",
                     transform=axes[4].transAxes)

    # Water masks
    for ax, mask, label in [
        (axes[5], geom_v3["water_mask"], "orange"),
        (axes[6], geom_v4["water_mask"], "orange"),
    ]:
        composite = np.zeros((*linear.shape, 3), dtype=float)
        composite[mask] = [1.0, 0.5, 0.0]
        composite[geom_v3["pier_mask"]] = [0.3, 0.3, 1.0]
        ax.imshow(composite, interpolation="nearest", origin="upper")

    fig.suptitle(f"east_t_head  {DEBUG_DATE} o{DEBUG_ORBIT} — Debug",
                 color="white", fontsize=8)
    out = OUT_DIR / "panels.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\nSaved: {out}")

    # Save S2 reference (larger, for bright spot check)
    if rgb is not None:
        fig2, ax2 = plt.subplots(1, 1, figsize=(5, 5))
        ax2.imshow(rgb, interpolation="nearest", origin="upper")
        ax2.set_title(f"S2 RGB — east_t_head {DEBUG_DATE}", fontsize=9)
        ax2.set_xticks([]); ax2.set_yticks([])
        # Mark water band boundaries
        try:
            ax2.contour(geom_v3["water_mask"].astype(float),
                        levels=[0.5], colors=["orange"], linewidths=1.5)
            ax2.contour(geom_v3["pier_mask"].astype(float),
                        levels=[0.5], colors=["blue"], linewidths=1.5)
        except Exception: pass
        out2 = OUT_DIR / "s2_reference.png"
        fig2.savefig(out2, dpi=150, bbox_inches="tight")
        plt.close(fig2)
        print(f"Saved: {out2}")


if __name__ == "__main__":
    main()
