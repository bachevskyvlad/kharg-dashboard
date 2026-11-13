"""
berth_snapshots.py — Generate SAR terminal overview PNGs for the two most recent
berth detection passes, annotate with hull detections (v5.0 format), upload to
Supabase Storage bucket berth_snapshots.

Outputs:
  data/berth_snapshots.json  — {prev:{url,date,orbit,summary}, latest:{...}, change:...}
  Storage: previous.png, latest.png, snapshots.json

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
from matplotlib.patches import Polygon as MplPolygon
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
SNAPS_JSON = ROOT / "data" / "berth_snapshots.json"
BUCKET     = "berth_snapshots"

# Terminal overview AOI — all 3 berths in one frame
SNAP_AOI = BBox(bbox=[50.265, 29.198, 50.362, 29.252], crs=CRS.WGS84)
SNAP_RES  = 20   # metres per pixel
PX_PER_M  = 1.0 / SNAP_RES

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
BERTH_LABELS = {
    "sea_island":     "Sea Island",
    "east_t_head":    "East T-Head",
    "southeast_pier": "SE Pier",
}
VLCC_COLOR = "#ef4444"
SUEZ_COLOR = "#f97316"

# ── Geometry helpers ──────────────────────────────────────────────────────────

def geo_to_px(lon, lat, bbox, w, h):
    col = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    row = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return col, row

def berth_axis_uvec(pts, bbox, w, h):
    """Unit vector along berth axis in pixel space."""
    px_pts = [geo_to_px(lon, lat, bbox, w, h) for lon, lat in pts]
    dx = px_pts[-1][0] - px_pts[0][0]
    dy = px_pts[-1][1] - px_pts[0][1]
    L  = np.hypot(dx, dy)
    if L < 1e-6:
        return (1.0, 0.0)
    return (dx / L, dy / L)

def corridor_corners(pts, bbox, w, h, half_m=160, end_m=120):
    """Dashed corridor outline corners (4 corners of rect around berth axis)."""
    px_pts = [geo_to_px(lon, lat, bbox, w, h) for lon, lat in pts]
    ux, uy = berth_axis_uvec(pts, bbox, w, h)
    rx, ry = -uy, ux
    end_px  = end_m * PX_PER_M
    half_px = half_m * PX_PER_M
    p0 = (px_pts[0][0] - ux * end_px, px_pts[0][1] - uy * end_px)
    p1 = (px_pts[-1][0] + ux * end_px, px_pts[-1][1] + uy * end_px)
    return [
        (p0[0] - rx * half_px, p0[1] - ry * half_px),
        (p0[0] + rx * half_px, p0[1] + ry * half_px),
        (p1[0] + rx * half_px, p1[1] + ry * half_px),
        (p1[0] - rx * half_px, p1[1] - ry * half_px),
    ]

def hull_bbox_corners(hull, pts, bbox, w, h):
    """Rotated bbox corners for a detected hull."""
    lon, lat = hull["lon"], hull["lat"]
    len_m    = hull["len_m"]
    wid_m    = max(hull.get("width_m", 25), 15)
    cx, cy   = geo_to_px(lon, lat, bbox, w, h)
    ux, uy   = berth_axis_uvec(pts, bbox, w, h)
    rx, ry   = -uy, ux
    half_len_px = (len_m / 2) * PX_PER_M
    half_wid_px = max((wid_m / 2) * PX_PER_M, 2.5)
    return [
        (cx - ux * half_len_px - rx * half_wid_px,
         cy - uy * half_len_px - ry * half_wid_px),
        (cx - ux * half_len_px + rx * half_wid_px,
         cy - uy * half_len_px + ry * half_wid_px),
        (cx + ux * half_len_px + rx * half_wid_px,
         cy + uy * half_len_px + ry * half_wid_px),
        (cx + ux * half_len_px - rx * half_wid_px,
         cy + uy * half_len_px - ry * half_wid_px),
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

def render_snapshot(scene_linear, pass_data, date_str, orbit):
    """
    pass_data: dict {berth: {det, hulls}} from v5.0 passes entry
    Returns (png_bytes, summary_str, ship_counts_dict)
    """
    h, w = scene_linear.shape
    eps = 1e-10
    db  = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))

    valid = db[db > -50]
    p2, p98 = (np.percentile(valid, [2, 98]) if len(valid) > 100 else (-25.0, 5.0))
    img = np.clip((db - p2) / max(p98 - p2, 1e-6), 0.0, 1.0)

    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    ax.imshow(img, cmap="gray", vmin=0, vmax=1, origin="upper", aspect="equal")

    ship_counts = {"total": 0, "VLCC": 0, "SuezAframax": 0}

    for berth, pts in BERTH_COORDS.items():
        bd    = pass_data.get(berth, {})
        hulls = bd.get("hulls", [])
        det   = bd.get("det", "—")
        lname = BERTH_LABELS[berth]

        # Dashed grey corridor outline
        corr_corners = corridor_corners(pts, SNAP_AOI, w, h)
        if corr_corners:
            poly = MplPolygon(corr_corners, closed=True,
                              edgecolor="#555555", facecolor="none",
                              linewidth=0.8, linestyle="--", zorder=2)
            ax.add_patch(poly)

        # Berth label at top of corridor
        cx_c = sum(c[0] for c in corr_corners) / 4
        cy_c = min(c[1] for c in corr_corners) - 6
        ax.text(cx_c, cy_c, lname, color="#888888", fontsize=6,
                ha="center", va="bottom", zorder=5)

        if hulls:
            for hull in hulls:
                cls    = hull.get("class", "SuezAframax")
                len_m  = hull.get("len_m", 0)
                is_new = hull.get("new_load", False)
                color  = VLCC_COLOR if cls == "VLCC" else SUEZ_COLOR

                ship_counts["total"] += 1
                ship_counts[cls]      = ship_counts.get(cls, 0) + 1

                corners = hull_bbox_corners(hull, pts, SNAP_AOI, w, h)
                poly_h = MplPolygon(corners, closed=True,
                                    edgecolor=color, facecolor=color,
                                    alpha=0.25, linewidth=1.5, zorder=3)
                ax.add_patch(poly_h)
                poly_e = MplPolygon(corners, closed=True,
                                    edgecolor=color, facecolor="none",
                                    linewidth=1.5, zorder=4)
                ax.add_patch(poly_e)

                # Hull label
                lbl_x = sum(c[0] for c in corners) / 4
                lbl_y = sum(c[1] for c in corners) / 4
                lbl   = f"{cls} · {int(len_m)}m · {'новое' if is_new else 'то же'}"
                ax.text(lbl_x, lbl_y, lbl, color=color, fontsize=6,
                        ha="center", va="center", fontweight="bold", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.2", facecolor="#000000",
                                  alpha=0.6, edgecolor="none"))
        elif det == "EMP":
            # Empty label
            cx = sum(c[0] for c in corr_corners) / 4
            cy = sum(c[1] for c in corr_corners) / 4
            ax.text(cx, cy, "empty", color="#666666", fontsize=6.5,
                    ha="center", va="center", style="italic", zorder=5)

    n   = ship_counts["total"]
    vlc = ship_counts.get("VLCC", 0)
    sml = ship_counts.get("SuezAframax", 0)
    summary = f"У причалов: {n} судов (VLCC {vlc}, меньше {sml})"

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
    return buf.read(), summary, ship_counts

# ── Supabase upload ───────────────────────────────────────────────────────────

def upload_png(png_bytes, filename):
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

def upload_json(data, filename):
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{filename}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "x-upsert": "true",
    }
    payload = json.dumps(data).encode()
    try:
        resp = requests.put(url, headers=headers, data=payload, timeout=30)
        if resp.status_code not in (200, 201):
            print(f"  json upload failed {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  json upload error: {e}"); return None
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(JSON_IN) as f:
        occ_data = json.load(f)

    # Support v5.0 (passes) and v4.x (rows) formats
    if occ_data.get("version", "").startswith("v5"):
        passes = occ_data.get("passes", [])
        if not passes:
            sys.exit("No passes in berth_occupancy.json")
        last2 = passes[-2:] if len(passes) >= 2 else passes * 2
        def to_pass_data(p):
            result = {}
            for berth in BERTH_COORDS:
                bd = p.get(berth, {})
                result[berth] = {"det": bd.get("det", "—"), "hulls": bd.get("hulls", [])}
            return result
    else:
        # Legacy v4: rows are (date, orbit, sea_island, east_t_head, southeast_pier)
        rows = occ_data.get("rows", [])
        if not rows:
            sys.exit("No rows in berth_occupancy.json")
        last2 = rows[-2:] if len(rows) >= 2 else rows * 2
        def to_pass_data(p):
            result = {}
            for berth in BERTH_COORDS:
                result[berth] = {"det": p.get(berth, "—"), "hulls": []}
            return result

    pairs = [("prev", last2[0]), ("latest", last2[-1])]
    snaps = {}
    counts_by_label = {}

    for label, row in pairs:
        date_str = row["date"]
        orbit    = int(row["orbit"])
        pass_data = to_pass_data(row)
        filename  = "previous.png" if label == "prev" else "latest.png"

        print(f"\n── {label}: {date_str} o{orbit} ──")

        scene_dt = find_scene_dt(date_str, orbit)
        if scene_dt is None:
            print(f"  WARN: no scene found, skipping")
            snaps[label] = {"url": None, "date": date_str, "orbit": orbit, "summary": ""}
            counts_by_label[label] = {"total": 0}
            continue
        print(f"  scene_dt: {scene_dt}")

        scene = fetch_scene(scene_dt)
        if scene is None:
            print(f"  WARN: fetch failed, skipping")
            snaps[label] = {"url": None, "date": date_str, "orbit": orbit, "summary": ""}
            counts_by_label[label] = {"total": 0}
            continue
        print(f"  scene shape: {scene.shape}")

        png_bytes, summary, counts = render_snapshot(scene, pass_data, date_str, orbit)
        pub_url = upload_png(png_bytes, filename)
        if pub_url:
            print(f"  uploaded: {pub_url}")

        snaps[label] = {
            "url":     pub_url,
            "date":    date_str,
            "orbit":   orbit,
            "summary": summary,
        }
        counts_by_label[label] = counts

    # Build change summary
    prev_n   = counts_by_label.get("prev", {}).get("total", 0)
    latest_n = counts_by_label.get("latest", {}).get("total", 0)
    delta    = latest_n - prev_n

    # Find berths with new loads in latest pass
    new_berths = []
    if occ_data.get("version", "").startswith("v5"):
        latest_p = last2[-1]
        for berth in BERTH_COORDS:
            bd = latest_p.get(berth, {})
            if any(h.get("new_load") for h in bd.get("hulls", [])):
                new_berths.append(BERTH_LABELS[berth])

    if delta > 0 and new_berths:
        change = f"+{delta} новое судно у {', '.join(new_berths)}"
    elif delta < 0:
        change = f"{delta} судно покинуло терминал"
    else:
        change = "без изменений"

    snaps["change"] = change

    # Upload snapshots.json to Storage
    upload_json(snaps, "snapshots.json")

    # Write local copy
    SNAPS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPS_JSON, "w") as f:
        json.dump(snaps, f, indent=2)
    print(f"\nWrote: {SNAPS_JSON}")
    print(json.dumps(snaps, indent=2))


if __name__ == "__main__":
    main()
