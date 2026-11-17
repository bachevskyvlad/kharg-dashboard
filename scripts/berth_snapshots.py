"""
berth_snapshots.py — SAR terminal overview PNGs for the two most recent
berth detection passes, annotated with hull detections (v5.0 format).

Source: berth_pc_fetch.py (Planetary Computer sentinel-1-rtc). No CDSE/SH.
Both frames use the same p2/p98 dB stretch computed across both images.

Env vars:
  PC_SDK_SUBSCRIPTION_KEY (optional — PC public collection is anonymous)
  SUPABASE_URL, SUPABASE_SERVICE_KEY (optional)
    present  → upload to Storage bucket berth_snapshots (previous.png, latest.png)
    absent   → write to data/berth_snapshots/*.png, serve from site

Outputs:
  data/berth_snapshots.json  — {prev:{url,date,orbit,summary}, latest:{...}, change}
"""
import os, sys, json, io
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
import requests

sys.path.insert(0, str(Path(__file__).parent))
from berth_pc_fetch import find_item_pc, fetch_s1_pc

ROOT       = Path(__file__).parent.parent
JSON_IN    = ROOT / "data" / "berth_occupancy.json"
SNAPS_JSON = ROOT / "data" / "berth_snapshots.json"
SNAPS_DIR  = ROOT / "data" / "berth_snapshots"
BUCKET     = "berth_snapshots"

SNAP_AOI_LIST = [50.265, 29.198, 50.362, 29.252]   # [min_lon, min_lat, max_lon, max_lat]
SNAP_RES  = 20    # metres per pixel
PX_PER_M  = 1.0 / SNAP_RES


class _BBox:
    """Minimal bbox compatible with fetch_s1_pc (needs .min_x/y .max_x/y)."""
    def __init__(self, min_x, min_y, max_x, max_y):
        self.min_x, self.min_y, self.max_x, self.max_y = min_x, min_y, max_x, max_y


SNAP_BBOX = _BBox(*SNAP_AOI_LIST)

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
VLCC_COLOR = "#f87171"
SUEZ_COLOR = "#fbbf24"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ── Geometry helpers ──────────────────────────────────────────────────────────

def geo_to_px(lon, lat, bbox, w, h):
    col = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    row = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return col, row

def berth_axis_uvec(pts, bbox, w, h):
    px_pts = [geo_to_px(lon, lat, bbox, w, h) for lon, lat in pts]
    dx = px_pts[-1][0] - px_pts[0][0]
    dy = px_pts[-1][1] - px_pts[0][1]
    L  = np.hypot(dx, dy)
    if L < 1e-6:
        return (1.0, 0.0)
    return (dx / L, dy / L)

def corridor_corners(pts, bbox, w, h, half_m=160, end_m=120):
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

# ── Rendering ─────────────────────────────────────────────────────────────────

def render_snapshot(scene_linear, pass_data, date_str, orbit, vmin, vmax):
    """
    Render one SAR frame with hull annotations.
    vmin/vmax: shared dB stretch computed across both frames by the caller.
    """
    h, w = scene_linear.shape
    eps = 1e-10
    db  = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))
    img = np.clip((db - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)

    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    ax.imshow(img, cmap="gray", vmin=0, vmax=1, origin="upper", aspect="equal")

    ship_counts = {"total": 0, "VLCC": 0, "SuezAframax": 0}

    for berth, pts in BERTH_COORDS.items():
        bd    = pass_data.get(berth, {})
        hulls = bd.get("hulls", [])
        det   = bd.get("det", "—")
        lname = BERTH_LABELS[berth]

        corr_corners = corridor_corners(pts, SNAP_BBOX, w, h)
        ax.add_patch(MplPolygon(corr_corners, closed=True,
                                edgecolor="#aaaaaa", facecolor="none",
                                linewidth=1.2, linestyle="-", zorder=2))

        cx_c = sum(c[0] for c in corr_corners) / 4
        cy_c = min(c[1] for c in corr_corners) - 6
        ax.text(cx_c, cy_c, lname, color="#dddddd", fontsize=7,
                ha="center", va="bottom", zorder=5,
                bbox=dict(boxstyle="round,pad=0.15", facecolor="#000000",
                          alpha=0.55, edgecolor="none"))

        large_hulls = [h for h in hulls if h.get("class") in ("VLCC", "SuezAframax")]
        if large_hulls:
            for hull in large_hulls:
                cls    = hull.get("class", "SuezAframax")
                is_new = hull.get("new_load", False)
                color  = VLCC_COLOR if cls == "VLCC" else SUEZ_COLOR

                ship_counts["total"] += 1
                ship_counts[cls] = ship_counts.get(cls, 0) + 1

                corners = hull_bbox_corners(hull, pts, SNAP_BBOX, w, h)
                ax.add_patch(MplPolygon(corners, closed=True,
                                        edgecolor=color, facecolor=color,
                                        alpha=0.25, linewidth=1.5, zorder=3))
                ax.add_patch(MplPolygon(corners, closed=True,
                                        edgecolor=color, facecolor="none",
                                        linewidth=1.5, zorder=4))

                lbl_x = sum(c[0] for c in corners) / 4
                lbl_y = sum(c[1] for c in corners) / 4
                if berth == "southeast_pier":
                    lbl = f"n/a · {'arrived' if is_new else 'same'}"
                else:
                    lbl = f"{cls} · {'arrived' if is_new else 'same'}"
                ax.text(lbl_x, lbl_y, lbl, color=color, fontsize=6,
                        ha="center", va="center", fontweight="bold", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.2", facecolor="#000000",
                                  alpha=0.6, edgecolor="none"))
        elif det == "EMP" or not large_hulls:
            cx = sum(c[0] for c in corr_corners) / 4
            cy = sum(c[1] for c in corr_corners) / 4
            ax.text(cx, cy, "empty", color="#666666", fontsize=6.5,
                    ha="center", va="center", style="italic", zorder=5)

    n   = ship_counts["total"]
    vlc = ship_counts.get("VLCC", 0)
    sml = ship_counts.get("SuezAframax", 0)
    summary = f"Berths: {n} ships (VLCC {vlc}, Suez/Aframax {sml})"

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
    try:
        resp = requests.put(url, headers=headers,
                            data=json.dumps(data).encode(), timeout=30)
        if resp.status_code not in (200, 201):
            print(f"  json upload failed {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  json upload error: {e}"); return None
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}"

# ── Main ──────────────────────────────────────────────────────────────────────

def has_large_hull(pass_row):
    """True if pass has any VLCC or SuezAframax hull."""
    for berth in BERTH_COORDS:
        for h in pass_row.get(berth, {}).get("hulls", []):
            if h.get("class") in ("VLCC", "SuezAframax"):
                return True
    return False


def main():
    with open(JSON_IN) as f:
        occ_data = json.load(f)

    passes = occ_data.get("passes", [])
    if not passes:
        sys.exit("No passes in berth_occupancy.json")

    passes_sorted = sorted(passes, key=lambda p: p["date"])
    latest_pass   = passes_sorted[-1]

    large_hull_passes = [p for p in passes_sorted if has_large_hull(p)]
    last_ship_pass    = large_hull_passes[-1] if large_hull_passes else latest_pass

    same_pass = (last_ship_pass["date"] == latest_pass["date"] and
                 int(last_ship_pass["orbit"]) == int(latest_pass["orbit"]))

    def to_pass_data(p):
        return {
            berth: {"det":   p.get(berth, {}).get("det", "—"),
                    "hulls": p.get(berth, {}).get("hulls", [])}
            for berth in BERTH_COORDS
        }

    # (label, pass_row, filename)
    if same_pass:
        pairs = [("latest", latest_pass, "latest.png")]
    else:
        pairs = [("latest", latest_pass, "latest.png"),
                 ("last_ship", last_ship_pass, "last_ship.png")]

    # Phase 1: fetch SAR scenes (deduplicated by filename)
    scenes = {}  # filename -> arr
    for label, row, filename in pairs:
        if filename in scenes:
            continue
        date_str = row["date"]
        orbit    = int(row["orbit"])
        print(f"── fetch {filename}: {date_str} o{orbit} ──")
        item = find_item_pc(date_str, orbit, SNAP_AOI_LIST)
        if item is None:
            print(f"  WARN: no PC item found for {date_str} o{orbit}")
            scenes[filename] = None
            continue
        print(f"  item: {item.id}")
        arr = fetch_s1_pc(item, SNAP_BBOX, resolution=SNAP_RES)
        if arr is None:
            print(f"  WARN: fetch returned None")
        else:
            print(f"  shape: {arr.shape}")
        scenes[filename] = arr

    # Phase 2: shared p2/p98 stretch across both images
    eps = 1e-10
    all_db = []
    for arr in scenes.values():
        if arr is not None:
            db_arr = 10 * np.log10(np.where(arr < eps, eps, arr))
            all_db.append(db_arr[db_arr > -50].ravel())
    if all_db:
        combined = np.concatenate(all_db)
        vmin, vmax = (np.percentile(combined, [2, 98])
                      if len(combined) > 100 else (-25.0, 5.0))
    else:
        vmin, vmax = -25.0, 5.0
    print(f"Shared stretch: vmin={vmin:.1f} dB  vmax={vmax:.1f} dB")

    # Phase 3: render and store
    rendered_urls     = {}  # filename -> url
    rendered_summary  = {}  # filename -> summary
    rendered_counts   = {}  # filename -> counts

    for label, row, filename in pairs:
        date_str  = row["date"]
        orbit     = int(row["orbit"])
        pass_data = to_pass_data(row)
        arr = scenes.get(filename)

        if arr is None:
            rendered_urls[filename]    = None
            rendered_summary[filename] = ""
            rendered_counts[filename]  = {"total": 0}
            continue

        png_bytes, summary, counts = render_snapshot(
            arr, pass_data, date_str, orbit, vmin, vmax)

        pub_url = None
        if SUPABASE_KEY and SUPABASE_URL:
            pub_url = upload_png(png_bytes, filename)
            if pub_url:
                print(f"  uploaded → {pub_url}")

        if pub_url is None:
            SNAPS_DIR.mkdir(parents=True, exist_ok=True)
            local_path = SNAPS_DIR / filename
            local_path.write_bytes(png_bytes)
            pub_url = f"/data/berth_snapshots/{filename}"
            print(f"  saved → {local_path}")

        rendered_urls[filename]    = pub_url
        rendered_summary[filename] = summary
        rendered_counts[filename]  = counts

    # Resolve last_ship if same pass as latest
    latest_url = rendered_urls.get("latest.png")
    latest_sum = rendered_summary.get("latest.png", "")
    if same_pass:
        ls_url  = latest_url
        ls_sum  = latest_sum
        ls_date = latest_pass["date"]
        ls_orb  = int(latest_pass["orbit"])
    else:
        ls_url  = rendered_urls.get("last_ship.png", latest_url)
        ls_sum  = rendered_summary.get("last_ship.png", "")
        ls_date = last_ship_pass["date"]
        ls_orb  = int(last_ship_pass["orbit"])

    # Build change summary (compare latest vs last_ship pass)
    latest_n    = rendered_counts.get("latest.png",    {}).get("total", 0)
    last_ship_n = rendered_counts.get("last_ship.png", rendered_counts.get("latest.png", {})).get("total", 0)
    delta       = latest_n - last_ship_n

    new_berths = []
    for berth in BERTH_COORDS:
        bd = latest_pass.get(berth, {})
        if any(h.get("new_load") for h in bd.get("hulls", [])
               if h.get("class") in ("VLCC", "SuezAframax")):
            new_berths.append(BERTH_LABELS[berth])

    if delta > 0 and new_berths:
        change = f"+{delta} new ship at {', '.join(new_berths)}"
    elif delta < 0:
        change = f"{delta} ship left the terminal"
    else:
        change = "no change"

    snaps = {
        "latest":    {"url": latest_url, "date": latest_pass["date"],
                      "orbit": int(latest_pass["orbit"]), "summary": latest_sum},
        "last_ship": {"url": ls_url, "date": ls_date, "orbit": ls_orb, "summary": ls_sum},
        "change":    change,
    }

    if SUPABASE_KEY and SUPABASE_URL:
        upload_json(snaps, "snapshots.json")

    SNAPS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPS_JSON, "w") as f:
        json.dump(snaps, f, indent=2)
    print(f"\
Wrote: {SNAPS_JSON}")
    print(json.dumps(snaps, indent=2))


if __name__ == "__main__":
    main()
