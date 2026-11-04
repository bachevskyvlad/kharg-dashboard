"""
Stage 0-full — Optical radius/center measurement for ALL Saudi tanks.
Terminals: ras_tanura (66), juwaymah (19), yanbu (12) = 97 tanks total.

Strategy: fetch ONE S2 L2A image per cluster (4 fetches total), then
crop and measure each tank from the shared tile. Much cheaper than 97
individual API calls.

Clusters:
  RT-south  — rt_0..rt_4, rt_11..rt_65 (61 tanks)
  RT-north  — rt_5..rt_10              (6 tanks)
  JW        — jw_0..jw_18             (19 tanks)
  YB        — yb_0..yb_11             (12 tanks)

Outputs:
  diagnostics/stage0_full/crops/<tank_id>.png
  diagnostics/stage0_full/stage0_full_report.txt
  registry_candidates/saudi_geometry_full.py  (per-tank measured geometry)

"Worst step" tanks are flagged in the report:
  JW: jw_1, jw_17
  YB: yb_3, yb_6
  RT: rt_65 (known edge artefact), rt_3, rt_4 (largest, densest cluster edge)
"""

import os, math, textwrap
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scipy.ndimage import gaussian_filter, map_coordinates
from sentinelhub import (
    SentinelHubRequest, DataCollection, MimeType,
    CRS, BBox, bbox_to_dimensions, SHConfig
)

# ── SH config (no supabase dependency) ───────────────────────────────────────
sh = SHConfig()
sh.sh_client_id     = os.environ.get("CLIENT_ID")     or ""
sh.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
sh.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
sh.sh_base_url      = "https://sh.dataspace.copernicus.eu"

# ── Constants ─────────────────────────────────────────────────────────────────
RESOLUTION  = 2.5   # m/px
BUFFER_M    = 250   # cluster fetch buffer (metres around bbox)
CROP_FACTOR = 2.2   # per-tank crop = CROP_FACTOR × r_reg on each side
N_RAYS      = 16
SMOOTH_PX   = 1.0
MIN_GRAD    = 0.008
S2_END      = datetime(2026, 9, 1)
S2_START    = S2_END - timedelta(days=365)

# ── Worst-step flags ──────────────────────────────────────────────────────────
WORST_TANKS = {"jw_1", "jw_17", "yb_3", "yb_6", "rt_65", "rt_3", "rt_4"}

# ── API 650 h_max table (radius → h_max, nearest) ────────────────────────────
H_MAX_TABLE = [(60,18),(55,17),(50,16),(45,15),(40,14),(35,13),(30,12),(25,12),(20,9),(15,7)]
def hmax_for_radius(r):
    return min(H_MAX_TABLE, key=lambda x: abs(x[0]-r))[1]

# ── Saudi tank definitions ────────────────────────────────────────────────────
RT_SOUTH_IDS = (
    [f"rt_{i}" for i in range(5)] +          # rt_0..rt_4
    [f"rt_{i}" for i in range(11, 66)]        # rt_11..rt_65
)
RT_NORTH_IDS = [f"rt_{i}" for i in range(5, 11)]  # rt_5..rt_10
JW_IDS = [f"jw_{i}" for i in range(19)]
YB_IDS = [f"yb_{i}" for i in range(12)]

# Registry coordinates (lat, lon, radius_m) — imported from tank_registry.py data
_RT_TANKS_RAW = [
    ("rt_0",  26.638626, 50.154236, 60),("rt_1",  26.640430, 50.153296, 60),
    ("rt_2",  26.641913, 50.152682, 60),("rt_3",  26.650024, 50.152807, 60),
    ("rt_4",  26.652085, 50.149920, 60),("rt_5",  26.688442, 50.118714, 60),
    ("rt_6",  26.687011, 50.117969, 60),("rt_7",  26.687548, 50.116596, 60),
    ("rt_8",  26.689090, 50.117193, 60),("rt_9",  26.688215, 50.114879, 60),
    ("rt_10", 26.689749, 50.115695, 60),("rt_11", 26.651657, 50.148320, 35),
    ("rt_12", 26.649988, 50.148177, 35),("rt_13", 26.650865, 50.148946, 35),
    ("rt_14", 26.650985, 50.150605, 35),("rt_15", 26.648982, 50.148984, 35),
    ("rt_16", 26.649609, 50.149935, 35),("rt_17", 26.650144, 50.151086, 35),
    ("rt_18", 26.648029, 50.149726, 35),("rt_19", 26.648818, 50.150545, 35),
    ("rt_20", 26.649339, 50.151734, 35),("rt_21", 26.646997, 50.150259, 35),
    ("rt_22", 26.647971, 50.151020, 35),("rt_23", 26.648509, 50.152502, 35),
    ("rt_24", 26.648872, 50.153435, 35),("rt_25", 26.647181, 50.151415, 35),
    ("rt_26", 26.647496613, 50.152485930, 35),("rt_27", 26.647870961, 50.153402153, 35),
    ("rt_28", 26.648243784, 50.154304015, 35),("rt_29", 26.646272546, 50.151974459, 35),
    ("rt_30", 26.646641703, 50.152926255, 35),("rt_31", 26.647029129, 50.153832257, 35),
    ("rt_32", 26.647394612, 50.154733110, 35),("rt_33", 26.645433932, 50.152392769, 35),
    ("rt_34", 26.645827818, 50.153347593, 35),("rt_35", 26.646200394, 50.154249852, 35),
    ("rt_36", 26.646570259, 50.155158460, 35),("rt_37", 26.643842240, 50.153203110, 35),
    ("rt_38", 26.644232379, 50.154144912, 35),("rt_39", 26.644607973, 50.155046837, 35),
    ("rt_40", 26.644975530, 50.155944016, 35),("rt_41", 26.643016459, 50.153611656, 35),
    ("rt_42", 26.643406095, 50.154572485, 35),("rt_43", 26.643782414, 50.155472085, 35),
    ("rt_44", 26.644156293, 50.156383567, 35),("rt_45", 26.641814725, 50.154215785, 35),
    ("rt_46", 26.642210411, 50.155178684, 35),("rt_47", 26.642577330, 50.156072461, 35),
    ("rt_48", 26.642950972, 50.156992054, 35),("rt_49", 26.641339934, 50.155599952, 35),
    ("rt_50", 26.641729102, 50.156516229, 35),("rt_51", 26.642073615, 50.157423258, 35),
    ("rt_52", 26.640476856, 50.156043180, 35),("rt_53", 26.640847523, 50.156946870, 35),
    ("rt_54", 26.641188536, 50.157794617, 35),("rt_55", 26.641760390, 50.159183452, 35),
    ("rt_56", 26.640105425, 50.157328512, 35),("rt_57", 26.640432763, 50.158165902, 35),
    ("rt_58", 26.641106377, 50.158670772, 35),("rt_59", 26.640346939, 50.159052659, 35),
    ("rt_60", 26.639675405,       50.158553012,       35),
    ("rt_61", 26.643554305621972, 50.152191100588105, 35),
    ("rt_62", 26.645139789560954, 50.151448430921810, 35),
    ("rt_63", 26.645949000703600, 50.151042337312750, 35),
    ("rt_64", 26.639988864810530, 50.154953473357590, 35),
    ("rt_65", 26.637591408424406, 50.160445373274800, 20),
]
_JW_TANKS_RAW = [
    ("jw_0",  26.814939449818950, 49.987095744022476, 50),
    ("jw_1",  26.816115167835957, 49.985801747261085, 50),
    ("jw_2",  26.817719856216030, 49.984028796562500, 50),
    ("jw_3",  26.818881636808456, 49.982719201457790, 50),
    ("jw_4",  26.816309495888724, 49.988651189962226, 50),
    ("jw_5",  26.817523593073770, 49.987380783090224, 50),
    ("jw_6",  26.819128015846970, 49.985623716671796, 50),
    ("jw_7",  26.820299858791643, 49.984324056007070, 50),
    ("jw_8",  26.818163062259000, 49.990692300527390, 50),
    ("jw_9",  26.819531458247540, 49.992284316605510, 50),
    ("jw_10", 26.819300433907060, 49.989417325119426, 50),
    ("jw_11", 26.820698661135080, 49.991014367679170, 50),
    ("jw_12", 26.820894127364330, 49.987648761169076, 50),
    ("jw_13", 26.822307642984722, 49.989232681648080, 50),
    ("jw_14", 26.822084302491750, 49.986363350107820, 50),
    ("jw_15", 26.823495358779535, 49.987952836960950, 50),
    ("jw_16", 26.823098922243503, 49.983801260652840, 50),
    ("jw_17", 26.824515110824244, 49.985368840304960, 50),
    ("jw_18", 26.825690135207460, 49.984073983323120, 50),
]
_YB_TANKS_RAW = [
    ("yb_0",  23.959074, 38.244398, 60),("yb_1",  23.962590, 38.247925, 60),
    ("yb_2",  23.964053, 38.245977, 60),("yb_3",  23.965544, 38.244086, 60),
    ("yb_4",  23.964986, 38.250081, 60),("yb_5",  23.966460, 38.248119, 60),
    ("yb_6",  23.967960, 38.246187, 60),("yb_7",  23.967392, 38.252229, 60),
    ("yb_8",  23.968866, 38.250263, 60),("yb_9",  23.970331, 38.248336, 60),
    ("yb_10", 23.969788, 38.254362, 60),("yb_11", 23.971259, 38.252416, 60),
]

ALL_TANKS = (
    [(tid, lat, lon, r, "ras_tanura") for tid, lat, lon, r in _RT_TANKS_RAW] +
    [(tid, lat, lon, r, "juwaymah")   for tid, lat, lon, r in _JW_TANKS_RAW] +
    [(tid, lat, lon, r, "yanbu")      for tid, lat, lon, r in _YB_TANKS_RAW]
)
TANK_BY_ID = {t[0]: t for t in ALL_TANKS}

# ── Clusters for batch fetch ──────────────────────────────────────────────────
CLUSTERS = [
    {"name": "rt_south", "ids": RT_SOUTH_IDS},
    {"name": "rt_north", "ids": RT_NORTH_IDS},
    {"name": "juwaymah", "ids": JW_IDS},
    {"name": "yanbu",    "ids": YB_IDS},
]

OUT_DIR = Path("diagnostics/stage0_full/crops")
REP_DIR = Path("diagnostics/stage0_full")
CAN_DIR = Path("registry_candidates")

# ─────────────────────────────────────────────────────────────────────────────

def deg_per_m(lat):
    return 1/111320.0, 1/(111320.0 * math.cos(math.radians(lat)))

def cluster_bbox(ids, buf_m=BUFFER_M):
    lats = [TANK_BY_ID[i][1] for i in ids]
    lons = [TANK_BY_ID[i][2] for i in ids]
    clat = (max(lats)+min(lats))/2
    dlat, dlon = deg_per_m(clat)
    return (
        min(lons) - buf_m*dlon,
        min(lats) - buf_m*dlat,
        max(lons) + buf_m*dlon,
        max(lats) + buf_m*dlat,
    )

def fetch_s2(bbox_deg, res=RESOLUTION):
    bb   = BBox(bbox=bbox_deg, crs=CRS.WGS84)
    size = bbox_to_dimensions(bb, resolution=res)
    # cap at 2500×2500 px
    if max(size) > 2500:
        scale = 2500 / max(size)
        size  = (int(size[0]*scale), int(size[1]*scale))

    evalscript = """
//VERSION=3
function setup(){return{input:["B04","B03","B02","dataMask"],output:{bands:4}};}
function evaluatePixel(s){return[3.5*s.B04,3.5*s.B03,3.5*s.B02,s.dataMask];}
"""
    S2L2A = DataCollection.SENTINEL2_L2A.define_from(
        "s2l2a_cdse", service_url="https://sh.dataspace.copernicus.eu"
    )
    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2L2A,
            time_interval=(S2_START.strftime("%Y-%m-%d"), S2_END.strftime("%Y-%m-%d")),
            other_args={
                "dataFilter": {"maxCloudCoverage": 15},
                "processing": {"upsampling": "BICUBIC"},
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
        bbox=bb, size=size, config=sh,
    )
    imgs = req.get_data()
    if not imgs or imgs[0] is None:
        raise RuntimeError("No S2 data")
    return imgs[0][:, :, :3], bbox_deg   # rgb uint8, bbox

def latlon_to_px(lat, lon, bbox_deg, img_shape):
    h, w = img_shape[:2]
    lon_min, lat_min, lon_max, lat_max = bbox_deg
    cx = (lon - lon_min) / (lon_max - lon_min) * w
    cy = (lat_max - lat) / (lat_max - lat_min) * h
    return cx, cy

def measure_tank_in_tile(rgb, cx, cy, r_reg_m, bbox_deg, res=RESOLUTION):
    h, w = rgb.shape[:2]
    gray = rgb.astype(np.float32).mean(axis=2)
    gray_s = gaussian_filter(gray, sigma=SMOOTH_PX)
    gy, gx = np.gradient(gray_s)
    gmag = np.sqrt(gx**2 + gy**2)

    r_min_px = int(r_reg_m * 0.35 / res)
    r_max_px = int(r_reg_m * 1.45 / res)

    edge_xs, edge_ys, r_readings = [], [], []
    for ang in np.linspace(0, 2*math.pi, N_RAYS, endpoint=False):
        rs = np.arange(max(3, r_min_px), r_max_px)
        xs = cx + rs * math.cos(ang)
        ys = cy + rs * math.sin(ang)
        # clip to image bounds
        ok = (xs >= 0) & (xs < w-1) & (ys >= 0) & (ys < h-1)
        if ok.sum() < 3:
            continue
        xs, ys, rs = xs[ok], ys[ok], rs[ok]
        vals = map_coordinates(gmag, [ys, xs], order=1, mode='nearest')
        if vals.max() < MIN_GRAD:
            continue
        pk = int(np.argmax(vals))
        r_readings.append(rs[pk] * res)
        edge_xs.append(float(xs[pk]))
        edge_ys.append(float(ys[pk]))

    if len(r_readings) < 5:
        return None

    r_meas = float(np.median(r_readings))
    new_cx = float(np.mean(edge_xs)) if edge_xs else cx
    new_cy = float(np.mean(edge_ys)) if edge_ys else cy

    shift_px = math.sqrt((new_cx - cx)**2 + (new_cy - cy)**2)
    shift_m  = shift_px * res

    # Refined centre → back to lat/lon
    lon_min, lat_min, lon_max, lat_max = bbox_deg
    new_lon = lon_min + (new_cx / rgb.shape[1]) * (lon_max - lon_min)
    new_lat = lat_max - (new_cy / rgb.shape[0]) * (lat_max - lat_min)

    return dict(
        r_meas=r_meas, shift_m=shift_m,
        new_lat=new_lat, new_lon=new_lon,
        cx_reg=cx, cy_reg=cy, cx_new=new_cx, cy_new=new_cy,
        n_rays=len(r_readings),
    )

def save_crop(rgb, cx, cy, r_reg_m, res, result, tank_id, out_path):
    # crop window
    buf_px = int(r_reg_m * CROP_FACTOR / res)
    h, w = rgb.shape[:2]
    x0 = max(0, int(cx) - buf_px); x1 = min(w, int(cx) + buf_px)
    y0 = max(0, int(cy) - buf_px); y1 = min(h, int(cy) + buf_px)
    crop = np.clip(rgb[y0:y1, x0:x1], 0, 255).astype(np.uint8)

    fig, ax = plt.subplots(1, 1, figsize=(5, 5))
    ax.imshow(crop)
    # circles in crop coords
    rcx, rcy = cx - x0, cy - y0
    r_reg_px = r_reg_m / res
    c_reg = plt.Circle((rcx, rcy), r_reg_px, color="red",  fill=False, lw=1.2)
    ax.add_patch(c_reg)
    if result:
        ncx, ncy = result["cx_new"] - x0, result["cy_new"] - y0
        r_new_px = result["r_meas"] / res
        c_new = plt.Circle((ncx, ncy), r_new_px, color="lime", fill=False, lw=1.2)
        ax.add_patch(c_new)
        flag = " ★WORST" if tank_id in WORST_TANKS else ""
        ax.set_title(f"{tank_id}{flag}  Δr={result['r_meas']-r_reg_m:+.1f}m  Δc={result['shift_m']:.1f}m",
                     fontsize=7)
    else:
        ax.set_title(f"{tank_id}  MEASURE_FAIL", fontsize=7, color="red")
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)

# ─────────────────────────────────────────────────────────────────────────────

def process_cluster(cluster_name, ids, rgb, bbox_deg):
    rows = []
    for tid in ids:
        entry = TANK_BY_ID[tid]
        _, lat, lon, r_reg = entry[:4]
        cx, cy = latlon_to_px(lat, lon, bbox_deg, rgb.shape)
        result = measure_tank_in_tile(rgb, cx, cy, r_reg, bbox_deg)
        crop_path = OUT_DIR / f"{tid}.png"
        save_crop(rgb, cx, cy, r_reg, RESOLUTION, result, tid, crop_path)
        rows.append((tid, entry[4], lat, lon, r_reg, result))
    return rows

def write_geometry_file(rows):
    CAN_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        '"""',
        'saudi_geometry_full.py — Stage 0-full measured geometry for RT+JW+YB.',
        f'Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
        'Source: Sentinel-2 L2A optical, 16-ray gradient profile, 2.5 m/px.',
        'Use these values to update tank_registry.py (centers + radii).',
        '"""',
        '',
        'MEASURED_GEOMETRY = [',
    ]
    for tid, terminal, lat_reg, lon_reg, r_reg, result in rows:
        if result:
            lat_new  = result["new_lat"]
            lon_new  = result["new_lon"]
            r_new    = result["r_meas"]
            h_new    = hmax_for_radius(r_new)
            dr_pct   = (r_new - r_reg) / r_reg * 100
            shift_m  = result["shift_m"]
            status   = "OK"
        else:
            lat_new, lon_new, r_new = lat_reg, lon_reg, r_reg
            h_new = hmax_for_radius(r_reg)
            dr_pct, shift_m = 0.0, 0.0
            status = "MEASURE_FAIL_KEEP_REGISTRY"
        worst = "  # WORST_STEP" if tid in WORST_TANKS else ""
        lines.append(
            f'    dict(id="{tid}", terminal="{terminal}",'
            f' lat={lat_new:.9f}, lon={lon_new:.9f},'
            f' radius_m={r_new:.1f}, h_max_m={h_new},'
            f' dr_pct={dr_pct:+.1f}, shift_m={shift_m:.1f},'
            f' status="{status}"),{worst}'
        )
    lines += [']', '']
    Path(CAN_DIR / "saudi_geometry_full.py").write_text('\n'.join(lines))

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REP_DIR.mkdir(parents=True, exist_ok=True)

    all_rows = []
    cluster_tiles = {}

    for cl in CLUSTERS:
        name, ids = cl["name"], cl["ids"]
        print(f"\n── Fetching {name} ({len(ids)} tanks) …")
        bbox_deg = cluster_bbox(ids)
        try:
            rgb, bbox_deg = fetch_s2(bbox_deg)
            print(f"   tile {rgb.shape[1]}×{rgb.shape[0]}px  "
                  f"bbox=[{bbox_deg[0]:.4f},{bbox_deg[1]:.4f},{bbox_deg[2]:.4f},{bbox_deg[3]:.4f}]")
            cluster_tiles[name] = (rgb, bbox_deg)
            rows = process_cluster(name, ids, rgb, bbox_deg)
        except Exception as e:
            print(f"   CLUSTER FETCH FAILED: {e}")
            rows = [(tid, TANK_BY_ID[tid][4],
                     TANK_BY_ID[tid][1], TANK_BY_ID[tid][2], TANK_BY_ID[tid][3],
                     None)
                    for tid in ids]
        all_rows.extend(rows)
        ok = sum(1 for r in rows if r[5] is not None)
        print(f"   measured {ok}/{len(rows)} tanks")

    # ── Write geometry file ───────────────────────────────────────────────────
    write_geometry_file(all_rows)

    # ── Write report ──────────────────────────────────────────────────────────
    rep = [
        "Stage 0-full — Saudi optical radius/center check",
        f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"Resolution: {RESOLUTION}m/px  Rays: {N_RAYS}",
        f"Tanks: {len(all_rows)} (RT {len(RT_SOUTH_IDS)+len(RT_NORTH_IDS)}, "
        f"JW {len(JW_IDS)}, YB {len(YB_IDS)})",
        "",
        f"{'tank':<10} {'terminal':<12} {'r_reg':>8} {'r_meas':>8} {'Δr%':>7} "
        f"{'shift_m':>9} {'rays':>5}  flag",
        "-"*72,
    ]

    dr_vals = []
    for tid, terminal, lat_reg, lon_reg, r_reg, result in all_rows:
        worst = " ★" if tid in WORST_TANKS else ""
        if result:
            r_m  = result["r_meas"]
            dr   = (r_m - r_reg) / r_reg * 100
            sh_m = result["shift_m"]
            n    = result["n_rays"]
            dr_vals.append(abs(dr))
            rep.append(f"{tid:<10} {terminal:<12} {r_reg:>8.1f} {r_m:>8.1f} {dr:>+7.1f}% "
                       f"{sh_m:>8.1f}m {n:>5}{worst}")
        else:
            rep.append(f"{tid:<10} {terminal:<12} {r_reg:>8.1f} {'N/A':>8} {'N/A':>8} "
                       f"{'N/A':>9} {'N/A':>5}{worst}  FAIL")

    rep.append("")
    rep.append(f"Measured:  {sum(1 for r in all_rows if r[5] is not None)}/{len(all_rows)}")
    if dr_vals:
        max_dr = max(dr_vals)
        rep.append(f"max |Δr|:  {max_dr:.1f}%")
        if max_dr > 10:
            verdict = f"STAGE 1 FULL — max |Δr|={max_dr:.1f}% > 10%; fix centers AND radii"
        else:
            verdict = f"STAGE 1 CENTERS ONLY — max |Δr|={max_dr:.1f}% ≤ 10%"
    else:
        verdict = "INCONCLUSIVE — all fetches failed"
    rep.append(f"VERDICT: {verdict}")

    rep_path = REP_DIR / "stage0_full_report.txt"
    rep_path.write_text("\n".join(rep) + "\n")

    print("\n" + "="*72)
    for line in rep:
        print(line)
    print(f"\nCrops   → {OUT_DIR}/")
    print(f"Report  → {rep_path}")
    print(f"Geometry→ {CAN_DIR}/saudi_geometry_full.py")

if __name__ == "__main__":
    main()
