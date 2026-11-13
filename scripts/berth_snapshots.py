"""
berth_snapshots.py — Generate SAR terminal overview PNGs for the two most recent
berth detection passes, annotate berth corridors with detection results,
upload to Supabase Storage bucket berth_snapshots.

Outputs:
  data/berth_snapshots.json  — {prev:{url,date,orbit,dets}, latest:{...}}

Env vars required:
  CLIENT_ID, CLIENT_SECRET     — Sentinel Hub CDSE
  SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import os, sys, csv, json, io
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import requests

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)

# ── Env ───────────────────────────────────────────────────────────────────────
def _require(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"FATAL: {name} not set")
    return v

CLIENT_ID     = _require("CLIENT_ID")
CLIENT_SECRET = _require("CLIENT_SECRET")
SUPABASE_URL  = _require("SUPABASE_URL").rstrip("/")
SUPABASE_KEY  = _require("SUPABASE_SERVICE_KEY")

# ── Sentinel Hub ──────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = CLIENT_ID
config.sh_client_secret = CLIENT_SECRET
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_bsnap", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT       = Path(__file__).parent.parent
JSON_IN    = ROOT / "data" / "berth_occupancy.json"
CSV_IN     = ROOT / "diagnostics" / "berth_v4" / "history_v4" / "berth_v4_history.csv"
SNAPS_JSON = ROOT / "data" / "berth_snapshots.json"
BUCKET     = "berth_snapshots"

# Terminal overview AOI — all 3 berths in one frame
SNAP_AOI = BBox(bbox=[50.265, 29.198, 50.362, 29.252], crs=CRS.WGS84)
SNAP_RES = 20  # metres per pixel

EVALSCRIPT = """
//VERSION=3
function setup() { return { input: ["VV"], output: { bands:1, sampleType:"FLOAT32" } }; }
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

BERTH_COORDS = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}
BERTH_COLORS = {
    "sea_island":     "#4DD9FF",
    "east_t_head":    "#FF5050",
    "southeast_pier": "#A0FF50",
}
BERTH_LABELS = {
    "sea_island":     "Sea Island",
    "east_t_head":    "East T-Head",
    "southeast_pier": "SE Pier",
}

# ── Geometry helpers ──────────────────────────────────────────────────────────

def geo_to_px(lon, lat, bbox, w, h):
    col = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    row = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return col, row

def corridor_corners(pts, bbox, w, h, half_m=160, end_m=120):
    lat_mid = sum(p[1] for p in pts) / len(pts)
    mpd_lat = 111320.0
    mpd_lon = 111320.0 * np.cos(np.deg2rad(lat_mid))
    # pixels per metre (isotropic approximation)
    px_m_lon = w / ((bbox.max_x - bbox.min_x) * mpd_lon)
    px_m_lat = h / ((bbox.max_y - bbox.min_y) * mpd_lat)
    px_m = (px_m_lon + px_m_lat) / 2

    px_pts = [geo_to_px(lon, lat, bbox, w, h) for lon, lat in pts]
    dx = px_pts[-1][0] - px_pts[0][0]
    dy = px_pts[-1][1] - px_pts[0][1]
    L = np.hypot(dx, dy)
    if L < 1e-6:
        return None
    ux, uy = dx / L, dy / L   # along-axis unit vector
    rx, ry = -uy, ux           # perpendicular unit vector

    end_px  = end_m * px_m
    half_px = half_m * px_m
    p0 = (px_pts[0][0] - ux * end_px, px_pts[0][1] - uy * end_px)
    p1 = (px_pts[-1][0] + ux * end_px, px_pts[-1][1] + uy * end_px)
    return [
        (p0[0] - rx * half_px, p0[1] - ry * half_px),
        (p0[0] + rx * half_px, p0[1] + ry * half_px),
        (p1[0] + rx * half_px, p1[1] + ry * half_px),
        (p1[0] - rx * half_px, p1[1] - ry * half_px),
    ]

# ── Sentinel Hub helpers ──────────────────────────────────────────────────────

def find_scene_dt(date_str, orbit):
    catalog = SentinelHubCatalog(config=config)
    for delta in [0, -1, 1]:
        d = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            results = list(catalog.search(
                S1_CDSE, bbox=SNAP_AOI,
                time=(d + "T00:00:00Z", d + "T23:59:59Z"),
                fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
            ))
        except Exception as e:
            print(f"  catalog search err ({d}): {e}"); continue
        for r in results:
            if r["properties"].get("sat:relative_orbit") == orbit:
                return r["properties"]["datetime"]
    return None

def fetch_scene(scene_dt):
    sz = bbox_to_dimensions(SNAP_AOI, resolution=SNAP_RES)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(scene_dt[:19].replace("T", " "), scene_dt[:19].replace("T", " ")),
            other_args={"processing": {
                "backCoeff": "SIGMA0_ELLIPSOID",
                "orthorectify": True,
                "demInstance": "COPERNICUS",
            }},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=SNAP_AOI, size=sz, config=config,
    )
    try:
        data = req.get_data()
        a = data[0]
        if a.ndim == 3:
            a = a[:, :, 0]
        return a.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch err: {e}"); return None

# ── Rendering ─────────────────────────────────────────────────────────────────

def render_snapshot(scene_linear, dets, lengths, date_str, orbit):
    h, w = scene_linear.shape
    eps = 1e-10
    db = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))

    # Percentile stretch
    valid = db[db > -50]
    p2, p98 = (np.percentile(valid, [2, 98]) if len(valid) > 100
               else (-25.0, 5.0))
    img = np.clip((db - p2) / max(p98 - p2, 1e-6), 0.0, 1.0)

    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    ax.imshow(img, cmap="gray", vmin=0, vmax=1, origin="upper", aspect="equal")

    legend_handles = []
    for berth, pts in BERTH_COORDS.items():
        det     = dets.get(berth, "—")
        length  = lengths.get(berth)
        color   = BERTH_COLORS[berth]
        lname   = BERTH_LABELS[berth]
        is_occ  = (det == "OCC")
        is_emp  = (det == "EMP")

        corners = corridor_corners(pts, SNAP_AOI, w, h)
        if corners is None:
            continue

        edge_color  = color if is_occ else ("#888888" if is_emp else "#444444")
        fill_alpha  = 0.22 if is_occ else (0.07 if is_emp else 0.03)
        edge_lw     = 2.0  if is_occ else (1.0  if is_emp else 0.5)

        # Filled corridor
        poly_fill = mpatches.Polygon(corners, closed=True,
                                      edgecolor="none", facecolor=edge_color,
                                      alpha=fill_alpha, zorder=2)
        ax.add_patch(poly_fill)
        # Edge outline
        poly_edge = mpatches.Polygon(corners, closed=True,
                                      edgecolor=edge_color, facecolor="none",
                                      linewidth=edge_lw, zorder=3)
        ax.add_patch(poly_edge)

        # Label at corridor centroid
        cx = sum(c[0] for c in corners) / 4
        cy = sum(c[1] for c in corners) / 4

        if is_occ:
            lbl = f"{lname}\n{int(length) if length else 'OCC'} m"
            txt_color = color
        elif is_emp:
            lbl = f"{lname}\nempty"
            txt_color = "#aaaaaa"
        else:
            lbl = f"{lname}\n—"
            txt_color = "#555555"

        ax.text(cx, cy, lbl, color=txt_color, fontsize=7,
                ha="center", va="center",
                fontweight="bold" if is_occ else "normal", zorder=4,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#000000",
                          alpha=0.55, edgecolor="none"))

        legend_handles.append(
            mpatches.Patch(color=edge_color,
                            label=f"{lname}: {'OCC' if is_occ else det}"))

    if legend_handles:
        ax.legend(handles=legend_handles, loc="lower right", fontsize=7,
                   facecolor="#111111", edgecolor="#444444",
                   labelcolor="white", framealpha=0.85)

    ax.set_title(f"Kharg Island Terminal · {date_str} · orbit {orbit}",
                  fontsize=10, color="white", pad=6)
    fig.patch.set_facecolor("#000000")
    ax.set_facecolor("#000000")
    ax.axis("off")
    plt.tight_layout(pad=0.5)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="#000000")
    plt.close()
    buf.seek(0)
    return buf.read()

# ── Supabase upload ───────────────────────────────────────────────────────────

def upload(png_bytes, filename):
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{filename}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/png",
        "x-upsert": "true",
    }
    try:
        resp = requests.put(url, headers=headers, data=png_bytes, timeout=90)
        if resp.status_code not in (200, 201):
            print(f"  upload failed {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  upload error: {e}"); return None
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(JSON_IN) as f:
        occ_data = json.load(f)
    rows = occ_data.get("rows", [])
    if not rows:
        sys.exit("No rows in berth_occupancy.json")

    # Last 2 unique (date, orbit) rows
    last2  = rows[-2:] if len(rows) >= 2 else rows * 2
    passes = [("prev", last2[0]), ("latest", last2[-1])]

    # Build length_m lookup from CSV
    lengths_map = {}
    if CSV_IN.exists():
        with open(CSV_IN) as f:
            for r in csv.DictReader(f):
                try:
                    lm = float(r["length_m"]) if r.get("length_m") else None
                except ValueError:
                    lm = None
                lengths_map[(r["date"], int(r["orbit"]), r["berth"])] = lm

    snaps = {}
    for label, row in passes:
        date_str = row["date"]
        orbit    = int(row["orbit"])
        dets     = {b: row.get(b, "—") for b in BERTH_COORDS}
        lengths  = {b: lengths_map.get((date_str, orbit, b)) for b in BERTH_COORDS}

        print(f"\n── {label}: {date_str} o{orbit} ──")
        print(f"  dets: { {b: v for b,v in dets.items()} }")

        scene_dt = find_scene_dt(date_str, orbit)
        if scene_dt is None:
            print(f"  WARN: no scene found, skipping")
            snaps[label] = {"url": None, "date": date_str, "orbit": orbit, "dets": dets}
            continue
        print(f"  scene_dt: {scene_dt}")

        scene = fetch_scene(scene_dt)
        if scene is None:
            print(f"  WARN: fetch failed, skipping")
            snaps[label] = {"url": None, "date": date_str, "orbit": orbit, "dets": dets}
            continue
        print(f"  scene shape: {scene.shape}")

        png_bytes = render_snapshot(scene, dets, lengths, date_str, orbit)
        filename  = f"{date_str}_o{orbit}_terminal.png"
        pub_url   = upload(png_bytes, filename)
        if pub_url:
            print(f"  uploaded: {pub_url}")

        snaps[label] = {
            "url":    pub_url,
            "date":   date_str,
            "orbit":  orbit,
            "dets":   dets,
        }

    with open(SNAPS_JSON, "w") as f:
        json.dump(snaps, f, indent=2)
    print(f"\nWrote: {SNAPS_JSON}")
    print(json.dumps(snaps, indent=2))


if __name__ == "__main__":
    main()
