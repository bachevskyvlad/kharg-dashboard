"""
berth_validate_optics.py — Validate v3 detector against optical ground truth.

Reads diagnostics/berth_v4/optics_ref_sameday.csv
For each date+orbit: fetch SAR, load bg_v3 cache, run v3 detector.
Outputs:
  diagnostics/berth_v4/validation_optics/results.csv
  diagnostics/berth_v4/validation_optics/accuracy.txt
  diagnostics/berth_v4/validation_optics/discrepancy_sheet.png
"""

import os, sys, csv, json
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

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
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_val", service_url="https://sh.dataspace.copernicus.eu"
)
S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2l2a_cdse_val", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT    = Path(__file__).parent.parent
BG_DIR  = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "validation_optics"
REF_CSV = ROOT / "diagnostics" / "berth_v4" / "optics_ref_sameday.csv"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants (same as berth_contact_sheet_v3.py) ────────────────────────────
BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}
BERTHS          = ["sea_island", "east_t_head", "southeast_pier"]
CORRIDOR_HALF_M = 150
CORRIDOR_END_M  = 250
PIER_EXCL_M     = 20
WATER_INNER_M   = 20
WATER_OUTER_M   = 80
SHIP_MIN_LEN_M  = 150
SHIP_MIN_ASPECT = 3.0
RATIO_THR       = 10.0
RESOLUTION      = 10
CELL_KM         = 1.0

# ── Evalscripts ───────────────────────────────────────────────────────────────
EVALSCRIPT_S1 = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

EVALSCRIPT_S2_RGB = """
//VERSION=3
function setup() {
  return { input: [{bands:["B04","B03","B02"]}], output:{bands:3,sampleType:"FLOAT32"} };
}
function evaluatePixel(s) { return [s.B04*3.5, s.B03*3.5, s.B02*3.5]; }
"""

# ── Geometry ──────────────────────────────────────────────────────────────────
def metres_per_deg_lat(): return 111320.0
def metres_per_deg_lon(lat): return 111320.0 * np.cos(np.deg2rad(lat))

def berth_center(pts):
    return np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])

def cell_bbox_for(clon, clat, km=CELL_KM):
    half_lat = (km * 500) / metres_per_deg_lat()
    half_lon = (km * 500) / metres_per_deg_lon(clat)
    return BBox(bbox=[clon-half_lon, clat-half_lat,
                      clon+half_lon, clat+half_lat], crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat()
    mpdlon = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat
        L = np.hypot(dx, dy)
        if L < 1e-6: return b
        return (b[0]+dx/L*d/mpdlon, b[1]+dy/L*d/mpdlat)
    p = list(pts)
    return [ext(p[1], p[0], end_m)] + p[1:-1] + [ext(p[-2], p[-1], end_m)]

def build_corridor_polygon(pts, half_m, end_m):
    ext = _extend_line(pts, end_m)
    line = LineString(ext)
    lat_mid = np.mean([p[1] for p in pts])
    buf_deg = ((half_m/metres_per_deg_lat()) + (half_m/metres_per_deg_lon(lat_mid))) / 2
    return line.buffer(buf_deg)

def lonlat_to_px(lon, lat, bbox_obj, img_shape):
    h, w = img_shape
    return ((lon-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-lat)/(bbox_obj.max_y-bbox_obj.min_y)*h)

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
    sign = np.ones(px_x.shape)
    for i in range(len(axis_m_pts) - 1):
        x1, y1 = axis_m_pts[i]; x2, y2 = axis_m_pts[i+1]
        dx = x2-x1; dy = y2-y1; L = np.hypot(dx, dy)
        if L < 1e-6: continue
        ux = dx/L; uy = dy/L; rx = uy; ry = -ux
        vx = px_x-x1; vy = px_y-y1
        t = np.clip((vx*ux+vy*uy)/L, 0, 1)
        cx = x1+t*dx; cy = y1+t*dy
        dpx = px_x-cx; dpy = px_y-cy
        dist = np.hypot(dpx, dpy)
        sgn = np.where(dpx*rx+dpy*ry >= 0, 1.0, -1.0)
        update = dist < min_dist
        min_dist = np.where(update, dist, min_dist)
        sign = np.where(update, sgn, sign)
    return min_dist * sign

def build_water_masks(berth_pts, bbox_obj, img_shape):
    h, w = img_shape
    lat_mid = (bbox_obj.min_y+bbox_obj.max_y)/2
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    clon, clat = berth_center(berth_pts)
    ext_pts = _extend_line(berth_pts, CORRIDOR_END_M)
    axis_m = [((lon-clon)*mpdlon, (lat-clat)*mpdlat) for lon, lat in ext_pts]
    lons = np.linspace(bbox_obj.min_x+0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x-0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y-0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y+0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px_x = (lon_g-clon)*mpdlon; px_y = (lat_g-clat)*mpdlat
    dist_arr = compute_signed_dist_field(px_x, px_y, axis_m)
    corridor_poly = build_corridor_polygon(berth_pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
    corridor_mask = rasterize_poly(corridor_poly, bbox_obj, img_shape)
    abs_dist = np.abs(dist_arr)
    pc_s = lonlat_to_px(ext_pts[0][0], ext_pts[0][1], bbox_obj, img_shape)
    pc_e = lonlat_to_px(ext_pts[-1][0], ext_pts[-1][1], bbox_obj, img_shape)
    dcol = pc_e[0]-pc_s[0]; drow = pc_e[1]-pc_s[1]
    L_px = np.hypot(dcol, drow)
    ux = dcol/L_px if L_px > 1e-6 else 0.0
    uy = drow/L_px if L_px > 1e-6 else 1.0
    return {
        "corridor_mask": corridor_mask,
        "pier_mask":     (abs_dist < PIER_EXCL_M) & corridor_mask,
        "water_mask":    (abs_dist >= WATER_INNER_M) & (abs_dist <= WATER_OUTER_M) & corridor_mask,
        "axis_uvec_img": (ux, uy),
        "corridor_poly": corridor_poly,
    }

struct8 = ndimage.generate_binary_structure(2, 2)

def find_blobs_water(ratio_arr, water_mask, axis_uvec_img):
    ux, uy = axis_uvec_img; px, py = -uy, ux
    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)
    blobs = []
    for bid in range(1, n+1):
        mask = (labeled == bid)
        rows, cols = np.where(mask)
        if len(rows) == 0: continue
        fc = cols.astype(float); fr = rows.astype(float)
        proj_along = fc*ux + fr*uy; proj_perp = fc*px + fr*py
        length_m = float(proj_along.max()-proj_along.min()) * RESOLUTION
        width_m  = max(float(proj_perp.max()-proj_perp.min()) * RESOLUTION, 1.0)
        aspect = length_m / width_m
        eroded = ndimage.binary_erosion(mask, structure=struct8)
        blobs.append({"length_m": length_m, "width_m": width_m, "aspect": aspect,
                      "border": mask & ~eroded,
                      "bbox": (int(rows.min()), int(rows.max()),
                               int(cols.min()), int(cols.max()))})
    blobs.sort(key=lambda b: b["length_m"], reverse=True)
    return blobs, (blobs[0] if blobs else None)

def detect(ratio_arr, water_mask, axis_uvec_img):
    if ratio_arr is None:
        return None, None, None
    blobs, longest = find_blobs_water(ratio_arr, water_mask, axis_uvec_img)
    if longest and longest["length_m"] >= SHIP_MIN_LEN_M and longest["aspect"] >= SHIP_MIN_ASPECT:
        return True, longest, blobs
    return False, longest, blobs

# ── Fetchers ──────────────────────────────────────────────────────────────────
def scene_datetime_s1(date_str, orbit, bbox):
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"  S1 catalog error ({date_str}): {e}"); return ""
    for r in results:
        p = r.get("properties",{})
        if int(p.get("sat:relative_orbit",0)) == orbit:
            return p.get("datetime","")
    return ""

def scene_datetime_s2(date_str, bbox):
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S2_CDSE, bbox=bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.eo:cloud_cover"]},
        ))
    except Exception as e:
        print(f"  S2 catalog error ({date_str}): {e}"); return ""
    best_dt = ""; best_cc = 999.0
    for r in results:
        p = r.get("properties",{})
        cc = float(p.get("eo:cloud_cover",100))
        if cc < best_cc:
            best_cc = cc; best_dt = p.get("datetime","")
    return best_dt

def fetch_s1_linear(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing":{"backCoeff":"SIGMA0_ELLIPSOID",
                                      "orthorectify":True,"demInstance":"COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        data = req.get_data(); arr = data[0]
        if arr.ndim == 3: arr = arr[:,:,0]
        return arr.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch failed ({dt_str[:10]}): {e}"); return None

def fetch_s2_rgb(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2_RGB,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        data = req.get_data(); arr = data[0]
        if arr.ndim == 2: arr = np.stack([arr]*3, axis=-1)
        return np.clip(arr, 0, 1).astype(np.float32)
    except Exception as e:
        print(f"  S2 fetch failed ({dt_str[:10]}): {e}"); return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not REF_CSV.exists():
        sys.exit(f"FATAL: {REF_CSV} not found")

    # Load reference
    with open(REF_CSV) as f:
        ref_rows = list(csv.DictReader(f))
    print(f"Loaded {len(ref_rows)} reference rows from {REF_CSV.name}")

    # Cell bboxes
    cell_bboxes = {b: cell_bbox_for(*berth_center(pts))
                   for b, pts in BERTH_SPEC.items()}
    aoi_full = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

    # Load BG cache
    bg_cache = {}
    for berth in BERTHS:
        for orbit in (35, 101):
            p = BG_DIR / f"bg_o{orbit}_{berth}.npy"
            if p.exists():
                bg_cache[(orbit, berth)] = np.load(p)
                print(f"  BG loaded: {p.name}")
            else:
                bg_cache[(orbit, berth)] = None
                print(f"  BG MISSING: {p.name}")

    # Run detection on all dates
    results = []   # list of dicts per (date, orbit, berth)
    discrepancy_dates = set()  # (date_str, orbit) where any berth disagrees

    for row in ref_rows:
        date_str = row["date"].strip()
        if not date_str:
            continue
        try:
            orbit = int(row["orbit"])
        except Exception:
            continue

        print(f"\n{date_str} o{orbit}")
        s1_dt = scene_datetime_s1(date_str, orbit, aoi_full)
        print(f"  S1: {s1_dt[:19] if s1_dt else 'NOT FOUND'}")

        for berth in BERTHS:
            ref_col = f"{berth}_optical_ref"
            optical_ref = row.get(ref_col, "").strip()
            bbox_obj = cell_bboxes[berth]
            pts = BERTH_SPEC[berth]

            # Fetch SAR
            linear = None
            if s1_dt:
                linear = fetch_s1_linear(s1_dt, bbox_obj)

            # BG
            bg = bg_cache.get((orbit, berth))
            no_bg = bg is None
            if bg is not None and linear is not None and bg.shape != linear.shape:
                bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                  linear.shape[1]/bg.shape[1]), order=1)

            # Ratio
            ratio = None
            if linear is not None and bg is not None:
                ratio = np.where((linear > 0) & (bg > 0),
                                 10*np.log10(linear/bg), np.nan).astype(np.float32)

            # Water masks
            img_shape = linear.shape if linear is not None else (99, 101)
            geom = build_water_masks(pts, bbox_obj, img_shape)

            # Detect
            if no_bg:
                det_occ = None; longest = None; blobs = []
            else:
                det_occ, longest, blobs = detect(ratio, geom["water_mask"],
                                                  geom["axis_uvec_img"])

            det_str = "OCC" if det_occ else ("EMP" if det_occ is not None else "NO_BG")
            len_str = f"{longest['length_m']:.0f}" if longest else "0"
            asp_str = f"{longest['aspect']:.1f}" if longest else "0"

            match = None
            if optical_ref in ("OCC","EMP") and det_occ is not None:
                ref_bool = optical_ref == "OCC"
                match = (det_occ == ref_bool)
                if not match:
                    discrepancy_dates.add((date_str, orbit))

            results.append({
                "date": date_str, "orbit": orbit, "berth": berth,
                "optical_ref": optical_ref,
                "det": det_str,
                "match": ("" if match is None else str(match)),
                "length_m": len_str, "aspect": asp_str,
                "no_bg": str(no_bg),
                "s1_found": str(bool(s1_dt)),
            })
            print(f"  {berth}: ref={optical_ref} det={det_str} "
                  f"len={len_str}m asp={asp_str} "
                  f"{'OK' if match else ('MISMATCH' if match is False else 'SKIP')}")

    # Write results CSV
    res_csv = OUT_DIR / "results.csv"
    fieldnames = ["date","orbit","berth","optical_ref","det","match",
                  "length_m","aspect","no_bg","s1_found"]
    with open(res_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(results)
    print(f"\nResults CSV: {res_csv}")

    # Accuracy per berth
    acc_lines = ["Validation against optical ground truth (optics_ref_sameday.csv)",
                 "Detector: v3 (water bands 20-80m, length>=150m, aspect>=3)",
                 "="*60]
    for berth in BERTHS:
        rows_b = [r for r in results if r["berth"] == berth
                  and r["optical_ref"] in ("OCC","EMP")
                  and r["det"] in ("OCC","EMP")]
        n = len(rows_b)
        if n == 0:
            acc_lines.append(f"\n{berth}: no evaluable rows"); continue
        tp = sum(1 for r in rows_b if r["optical_ref"]=="OCC" and r["det"]=="OCC")
        tn = sum(1 for r in rows_b if r["optical_ref"]=="EMP" and r["det"]=="EMP")
        fp = sum(1 for r in rows_b if r["optical_ref"]=="EMP" and r["det"]=="OCC")
        fn = sum(1 for r in rows_b if r["optical_ref"]=="OCC" and r["det"]=="EMP")
        acc = (tp+tn)/n*100
        prec = tp/(tp+fp)*100 if (tp+fp) > 0 else float("nan")
        rec  = tp/(tp+fn)*100 if (tp+fn) > 0 else float("nan")
        acc_lines += [
            f"\n{berth}  (n={n}, ?-excluded)",
            f"  Accuracy:  {acc:.1f}%",
            f"  Precision: {prec:.1f}%  Recall: {rec:.1f}%",
            f"  Confusion matrix (ref\\det):",
            f"            det:OCC  det:EMP",
            f"  ref:OCC     {tp:3d}      {fn:3d}",
            f"  ref:EMP     {fp:3d}      {tn:3d}",
        ]

    acc_txt = OUT_DIR / "accuracy.txt"
    with open(acc_txt, "w") as f:
        f.write("\n".join(acc_lines) + "\n")
    for line in acc_lines:
        print(line)

    # Discrepancy contact sheet
    disc_rows = [(date_str, orbit) for date_str, orbit in discrepancy_dates]
    disc_rows.sort()
    print(f"\nDiscrepancies: {len(disc_rows)} date-orbits")

    if not disc_rows:
        print("No discrepancies — skipping PNG"); return

    # Collect berth-level discrepancies per row
    disc_cells = {}  # (date, orbit) -> {berth -> (optical_ref, det, blobs, longest, ratio, s1_linear, s2_rgb)}
    for date_str, orbit in disc_rows:
        disc_cells[(date_str, orbit)] = {}

    # Re-fetch data for discrepancy dates
    for date_str, orbit in disc_rows:
        print(f"\nFetching discrepancy {date_str} o{orbit} ...")
        s1_dt = scene_datetime_s1(date_str, orbit, aoi_full)
        s2_dt = scene_datetime_s2(date_str, aoi_full)

        for berth in BERTHS:
            # Check if this specific berth has a discrepancy
            matching = [r for r in results
                        if r["date"]==date_str and r["orbit"]==orbit
                        and r["berth"]==berth and r["match"]=="False"]
            if not matching:
                continue

            bbox_obj = cell_bboxes[berth]
            pts = BERTH_SPEC[berth]

            linear = fetch_s1_linear(s1_dt, bbox_obj) if s1_dt else None
            rgb    = fetch_s2_rgb(s2_dt, bbox_obj)    if s2_dt else None

            bg = bg_cache.get((orbit, berth))
            if bg is not None and linear is not None and bg.shape != linear.shape:
                bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                  linear.shape[1]/bg.shape[1]), order=1)
            ratio = None
            if linear is not None and bg is not None:
                ratio = np.where((linear>0)&(bg>0),
                                 10*np.log10(linear/bg), np.nan).astype(np.float32)

            img_shape = linear.shape if linear is not None else (99, 101)
            geom = build_water_masks(pts, bbox_obj, img_shape)
            _, longest, blobs = detect(ratio, geom["water_mask"], geom["axis_uvec_img"])

            opt_ref = matching[0]["optical_ref"]
            det_str = matching[0]["det"]

            disc_cells[(date_str, orbit)][berth] = {
                "optical_ref": opt_ref, "det": det_str,
                "blobs": blobs, "longest": longest,
                "ratio": ratio, "linear": linear, "rgb": rgb,
                "water_mask": geom["water_mask"],
                "corridor_poly": geom["corridor_poly"],
                "bbox_obj": bbox_obj,
            }

    # Build PNG
    # Rows: disc_rows; Cols: berths with discrepancy (or all 3)
    # 3 panels per cell: S2 RGB | SAR dB | ratio
    n_rows_disc = len(disc_rows)
    PANEL_H = 1.3; COL_W = 4.2  # 3 panels per berth
    fig = plt.figure(figsize=(len(BERTHS)*COL_W, n_rows_disc*(PANEL_H+0.08)+0.3))
    fig.patch.set_facecolor("#1a1a1a")
    outer_gs = GridSpec(n_rows_disc, len(BERTHS), figure=fig,
                        hspace=0.08, wspace=0.04,
                        top=0.97, bottom=0.01, left=0.06, right=0.998)

    def _hide(ax): ax.set_xticks([]); ax.set_yticks([])

    for ri, (date_str, orbit) in enumerate(disc_rows):
        for ci, berth in enumerate(BERTHS):
            cell = disc_cells.get((date_str, orbit), {}).get(berth)

            inner = GridSpecFromSubplotSpec(1, 3, subplot_spec=outer_gs[ri, ci],
                                            wspace=0.02)
            ax_s2    = fig.add_subplot(inner[0])
            ax_sar   = fig.add_subplot(inner[1])
            ax_ratio = fig.add_subplot(inner[2])
            for ax in (ax_s2, ax_sar, ax_ratio):
                ax.set_facecolor("#111"); _hide(ax)

            if ci == 0:
                ax_s2.set_ylabel(f"{date_str}\no{orbit}", color="#bbb",
                                 fontsize=4, rotation=0, labelpad=28, va="center")
            if ri == 0:
                ax_s2.set_title(berth.replace("_"," "), color="white",
                                fontsize=5.5, pad=2, fontweight="bold")

            if cell is None:
                for ax in (ax_s2, ax_sar, ax_ratio):
                    ax.text(0.5, 0.5, "OK", color="#444",
                            ha="center", va="center", transform=ax.transAxes, fontsize=5)
                continue

            # S2 panel
            rgb = cell["rgb"]
            if rgb is not None:
                ax_s2.imshow(rgb, interpolation="nearest", origin="upper")
            else:
                ax_s2.text(0.5, 0.5, "NO S2", color="#666",
                           ha="center", va="center", transform=ax_s2.transAxes, fontsize=5)

            # SAR panel
            lin = cell["linear"]
            if lin is not None and np.isfinite(lin).any():
                db = np.where(lin>0, 10*np.log10(lin), np.nan)
                vmin, vmax = np.nanpercentile(db[np.isfinite(db)], [2,98])
                ax_sar.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                              interpolation="nearest", origin="upper")
            else:
                ax_sar.text(0.5, 0.5, "NO SAR", color="#666",
                            ha="center", va="center", transform=ax_sar.transAxes, fontsize=5)

            # Ratio panel
            ratio = cell["ratio"]
            water_mask = cell["water_mask"]
            if ratio is not None and np.isfinite(ratio).any():
                disp = np.clip(np.where(np.isfinite(ratio), ratio, np.nan), -10, 30)
                ax_ratio.imshow(disp, cmap="RdYlGn", vmin=-10, vmax=30,
                                interpolation="nearest", origin="upper")
                try:
                    ax_ratio.contour(ratio, levels=[RATIO_THR], colors=["white"],
                                     linewidths=0.5, alpha=0.9)
                except Exception: pass
                try:
                    ax_ratio.contour(water_mask.astype(float), levels=[0.5],
                                     colors=["orange"], linewidths=0.7, alpha=0.9)
                except Exception: pass
                for b in cell["blobs"]:
                    rows2, cols2 = np.where(b["border"])
                    ax_ratio.scatter(cols2, rows2, s=0.3, c="red", marker=".", linewidths=0)
                if cell["longest"]:
                    r0,r1,c0,c1 = cell["longest"]["bbox"]
                    ax_ratio.add_patch(mpatches.Rectangle(
                        (c0-0.5, r0-0.5), c1-c0+1, r1-r0+1,
                        lw=1.0, edgecolor="cyan", facecolor="none", zorder=5))

            # Annotation + border
            opt = cell["optical_ref"]; det = cell["det"]
            ann = f"ref={opt}\ndet={det}"
            if cell["longest"]:
                ann += f"\nlen={cell['longest']['length_m']:.0f}m asp={cell['longest']['aspect']:.1f}"
            ax_ratio.text(0.02, 0.98, ann, color="white",
                          ha="left", va="top", transform=ax_ratio.transAxes,
                          fontsize=3.5, fontweight="bold",
                          bbox=dict(boxstyle="square,pad=0.1", fc="black", alpha=0.6, lw=0))
            for ax in (ax_s2, ax_sar, ax_ratio):
                for spine in ax.spines.values():
                    spine.set_edgecolor("#e74c3c"); spine.set_linewidth(2.0)

    fig.text(0.5, 0.998,
             "Discrepancy sheet — ref=optical | det=v3 | red border=mismatch | "
             "S2 RGB | SAR dB | ratio+water bands",
             color="white", fontsize=4.5, ha="center", va="top")

    out_png = OUT_DIR / "discrepancy_sheet.png"
    fig.savefig(out_png, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nSaved: {out_png} ({out_png.stat().st_size//1024} KB)")
    plt.close(fig)


if __name__ == "__main__":
    main()
