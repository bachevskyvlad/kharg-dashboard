"""
berth_sea_island_fn_sheet.py — contact sheet for sea_island 2026 false-negatives.

Reads diagnostics/berth_v4/ablation/ablation_rows.csv, finds rows where:
  mode=v3  berth=sea_island  year≥2026  optical_ref=OCC  det=EMP

For each FN date: 3-panel figure
  col 0: S2 optical RGB (or SAR if no S2)
  col 1: S1 VV backscatter (dB)
  col 2: v3 ratio with water band overlay + blob mask

Output: diagnostics/berth_v4/sea_island_fn_sheet/fn_sheet.png
"""

import os, sys, csv
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import Normalize

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
    "s1iw_fn", service_url="https://sh.dataspace.copernicus.eu")
S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2l2a_fn", service_url="https://sh.dataspace.copernicus.eu")

ROOT    = Path(__file__).parent.parent
BG_V3   = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
CSV_REF = ROOT / "diagnostics" / "berth_v4" / "ablation" / "ablation_rows.csv"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "sea_island_fn_sheet"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RESOLUTION     = 10
CELL_KM        = 1.0
CORRIDOR_END_M = 250
RATIO_THR      = 10.0
struct8        = ndimage.generate_binary_structure(2, 2)
AOI_BBOX       = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

SEA_ISLAND_PTS = [(50.28316, 29.23318), (50.28816, 29.22433)]

EVALSCRIPT_S1 = """
//VERSION=3
function setup(){return{input:["VV"],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[Math.max(s.VV,1e-10)];}
"""
EVALSCRIPT_S2 = """
//VERSION=3
function setup(){return{input:["B04","B03","B02"],output:{bands:3,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.B04/3000,s.B03/3000,s.B02/3000];}
"""

# ── Geometry helpers ──────────────────────────────────────────────────────────

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
        dx=(b[0]-a[0])*mpdlon; dy=(b[1]-a[1])*mpdlat; L=np.hypot(dx,dy)
        if L<1e-6: return b
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

def compute_abs_dist(px_x, px_y, axis_m_pts):
    min_dist = np.full(px_x.shape, np.inf)
    for i in range(len(axis_m_pts)-1):
        x1,y1=axis_m_pts[i]; x2,y2=axis_m_pts[i+1]
        dx=x2-x1; dy=y2-y1; L=np.hypot(dx,dy)
        if L<1e-6: continue
        ux=dx/L; uy=dy/L
        vx=px_x-x1; vy=px_y-y1
        t=np.clip((vx*ux+vy*uy)/L, 0, 1)
        dist=np.hypot(px_x-(x1+t*dx), px_y-(y1+t*dy))
        min_dist=np.where(dist<min_dist, dist, min_dist)
    return min_dist

def build_geom(pts, bbox_obj, img_shape):
    h, w = img_shape
    lat_mid=(bbox_obj.min_y+bbox_obj.max_y)/2
    mpdlat=metres_per_deg_lat(); mpdlon=metres_per_deg_lon(lat_mid)
    clon, clat = berth_center(pts)
    ext_pts = _extend_line(pts, CORRIDOR_END_M)
    axis_m = [((lon-clon)*mpdlon, (lat-clat)*mpdlat) for lon, lat in ext_pts]
    lons = np.linspace(bbox_obj.min_x+0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x-0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y-0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y+0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px_x=(lon_g-clon)*mpdlon; px_y=(lat_g-clat)*mpdlat
    abs_dist = compute_abs_dist(px_x, px_y, axis_m)
    corridor = LineString(ext_pts).buffer(((150/mpdlat)+(150/mpdlon))/2)
    corridor_mask = rasterize_poly(corridor, bbox_obj, img_shape)
    pc_s = ((ext_pts[0][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[0][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    pc_e = ((ext_pts[-1][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[-1][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    dcol=pc_e[0]-pc_s[0]; drow=pc_e[1]-pc_s[1]; L_px=np.hypot(dcol,drow)
    ux = dcol/L_px if L_px>1e-6 else 0.0
    uy = drow/L_px if L_px>1e-6 else 1.0
    return {
        "water_mask":    (abs_dist >= 20) & (abs_dist <= 80) & corridor_mask,
        "corridor_mask": corridor_mask,
        "abs_dist":      abs_dist,
        "axis_uvec":     (ux, uy),
    }

def find_blobs(ratio_arr, water_mask, axis_uvec, min_len_m=150, min_aspect=3.0):
    ux, uy = axis_uvec; px_u, py_u = -uy, ux
    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)
    blobs = []
    for bid in range(1, n+1):
        mask = labeled == bid
        rows, cols = np.where(mask)
        if not len(rows): continue
        fc=cols.astype(float); fr=rows.astype(float)
        axis_p=fc*ux+fr*uy; perp_p=fc*px_u+fr*py_u
        len_m=(axis_p.max()-axis_p.min())*RESOLUTION
        wid_m=max((perp_p.max()-perp_p.min())*RESOLUTION, 1.0)
        blobs.append({"length_m":len_m,"width_m":wid_m,"aspect":len_m/wid_m,
                      "area_px":int(mask.sum()),"label_mask":labeled==bid})
    blobs.sort(key=lambda b: b["length_m"], reverse=True)
    best = blobs[0] if blobs else None
    detected = bool(best and best["length_m"]>=min_len_m and best["aspect"]>=min_aspect)
    return blobs, best, detected

# ── SentinelHub fetch helpers ─────────────────────────────────────────────────

def scene_dt_s1(date_str, orbit):
    cat = SentinelHubCatalog(config=config)
    try:
        res = list(cat.search(S1_CDSE, bbox=AOI_BBOX,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.sat:relative_orbit"]}))
    except Exception as e:
        print(f"  catalog err: {e}"); return ""
    for r in res:
        p=r.get("properties",{})
        if int(p.get("sat:relative_orbit",0)) == orbit:
            return p.get("datetime","")
    return ""

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
        print(f"  S1 err: {e}"); return None

def fetch_s2_rgb(date_str, bbox_obj):
    # Try ±5 days window
    from datetime import datetime, timedelta
    d = datetime.strptime(date_str, "%Y-%m-%d")
    d0 = (d - timedelta(days=5)).strftime("%Y-%m-%d")
    d1 = (d + timedelta(days=5)).strftime("%Y-%m-%d")
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(d0+"T00:00:00Z", d1+"T23:59:59Z"),
            other_args={"processing":{"harmonizeValues":True}},
            mosaicking_order=MosaickingOrder.LEAST_CC,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d_=req.get_data()[0]
        if d_.ndim==2: return None
        rgb = np.clip(d_[:,:,:3], 0, 1).astype(np.float32)
        return rgb
    except Exception as e:
        print(f"  S2 err: {e}"); return None

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    if not CSV_REF.exists():
        sys.exit(f"FATAL: ablation CSV not found at {CSV_REF}\nRun ablation step first.")

    # Find FN rows: v3, sea_island, 2026+, optical_ref=OCC, det=EMP
    fn_cases = []
    with open(CSV_REF, newline="") as f:
        for r in csv.DictReader(f):
            if (r["mode"] == "v3" and r["berth"] == "sea_island"
                    and int(r["date"][:4]) >= 2026
                    and r["optical_ref"] == "OCC" and r["det"] == "EMP"):
                fn_cases.append({"date": r["date"], "orbit": int(r["orbit"])})

    if not fn_cases:
        print("No FN cases found — check CSV path / content.")
        # Print a sample to debug
        with open(CSV_REF, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows[:5]: print(r)
        return

    print(f"sea_island 2026 FN cases ({len(fn_cases)}):")
    for c in fn_cases:
        print(f"  {c['date']} o{c['orbit']}")

    bbox_obj = cell_bbox_for(*berth_center(SEA_ISLAND_PTS))
    bg = None
    for orbit_try in [fn_cases[0]["orbit"], 35, 101]:
        p = BG_V3 / f"bg_o{orbit_try}_sea_island.npy"
        if p.exists(): break
    # Load all BG (orbit-specific)
    bg_by_orbit = {}
    for orb in [35, 101]:
        p = BG_V3 / f"bg_o{orb}_sea_island.npy"
        if p.exists():
            bg_by_orbit[orb] = np.load(p)

    n = len(fn_cases)
    fig, axes = plt.subplots(n, 3, figsize=(15, n * 5 + 0.5))
    if n == 1:
        axes = [axes]
    fig.patch.set_facecolor("#0a0e17")

    COL_TITLES = ["Optical (S2 ±5d)", "SAR VV (dB)", "v3 ratio + water bands"]

    for row_idx, case in enumerate(fn_cases):
        date_str = case["date"]
        orbit    = case["orbit"]
        axs      = axes[row_idx]

        print(f"\n{date_str} o{orbit}")
        s1_dt = scene_dt_s1(date_str, orbit)
        if not s1_dt:
            print("  no S1 scene"); continue
        print(f"  S1 scene: {s1_dt[:16]}")

        linear = fetch_s1_linear(s1_dt, bbox_obj)
        if linear is None: continue

        bg = bg_by_orbit.get(orbit)
        if bg is None: continue
        if bg.shape != linear.shape:
            bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0], linear.shape[1]/bg.shape[1]), order=1)

        ratio = np.where((linear>0)&(bg>0), 10*np.log10(linear/bg), np.nan).astype(np.float32)
        vv_db = np.where(linear>0, 10*np.log10(linear), np.nan).astype(np.float32)

        geom = build_geom(SEA_ISLAND_PTS, bbox_obj, linear.shape)
        blobs, best, detected = find_blobs(ratio, geom["water_mask"], geom["axis_uvec"])

        # col 0: S2 optical
        ax0 = axs[0]
        rgb = fetch_s2_rgb(date_str, bbox_obj)
        if rgb is not None:
            ax0.imshow(rgb, origin="upper")
            ax0.set_title(f"{date_str} o{orbit}\nS2 RGB", color="white", fontsize=8)
        else:
            ax0.imshow(vv_db, cmap="gray",
                       vmin=float(np.nanpercentile(vv_db,2)),
                       vmax=float(np.nanpercentile(vv_db,98)), origin="upper")
            ax0.set_title(f"{date_str} o{orbit}\nNo S2 → SAR VV", color="#aaa", fontsize=8)
        ax0.axis("off"); ax0.set_facecolor("#0a0e17")

        # col 1: SAR VV dB
        ax1 = axs[1]
        v2, v98 = float(np.nanpercentile(vv_db,2)), float(np.nanpercentile(vv_db,98))
        ax1.imshow(vv_db, cmap="gray", vmin=v2, vmax=v98, origin="upper")
        # overlay water band boundary
        water_bound = np.zeros((*vv_db.shape, 4))
        water_bound[geom["water_mask"], :] = [1.0, 0.6, 0.0, 0.25]  # orange tint
        ax1.imshow(water_bound, origin="upper")
        ax1.set_title("SAR VV (dB)\norange=water band 20–80m", color="white", fontsize=8)
        ax1.axis("off"); ax1.set_facecolor("#0a0e17")

        # col 2: ratio + blob overlay
        ax2 = axs[2]
        ratio_clipped = np.clip(ratio, -5, 20)
        im = ax2.imshow(ratio_clipped, cmap="RdYlGn_r",
                        vmin=-5, vmax=20, origin="upper")
        # water band outline
        water_bound2 = np.zeros((*ratio.shape, 4))
        water_bound2[geom["water_mask"], :] = [1.0, 0.6, 0.0, 0.3]
        ax2.imshow(water_bound2, origin="upper")
        # blob highlight
        above_thr = np.isfinite(ratio) & (ratio > RATIO_THR) & geom["water_mask"]
        if above_thr.any():
            highlight = np.zeros((*ratio.shape, 4))
            highlight[above_thr, :] = [0.0, 1.0, 0.0, 0.6]
            ax2.imshow(highlight, origin="upper")

        blob_info = "no blobs in water band"
        if blobs:
            blob_info = "  |  ".join(
                f"L={b['length_m']:.0f}m asp={b['aspect']:.1f}" for b in blobs[:3])
        ax2.set_title(f"v3 ratio (dB rel BG)\n{blob_info}", color="white", fontsize=7.5)
        ax2.axis("off"); ax2.set_facecolor("#0a0e17")

        for i, ax in enumerate(axs):
            if row_idx == 0:
                ax.set_xlabel(COL_TITLES[i], color="#888", fontsize=7)

    fig.suptitle("sea_island 2026 — false negatives (optical=OCC, v3=EMP)",
                 color="white", fontsize=11, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.995])

    out_path = OUT_DIR / "fn_sheet.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    run()
