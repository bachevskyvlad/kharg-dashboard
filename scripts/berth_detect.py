"""
berth_detect.py — Berth-level ship detector for Kharg Island.

ISOLATION: does NOT touch ship_backfill.py, update_anchorage.yml, or data/anchorage.json.
Output: diagnostics/berth/  (CSV + PNGs only, no Supabase writes).

AOI: 50.26–50.40 E, 29.17–29.31 N  (~13 × 15 km, island + jetties only)
Resolution: 10 m/px
Land mask: OSM coastline polygon (shapely, no network call — inline approx)
Platforms: flagged flag_platform=1, NOT masked

Per-detection fields:
  date, scene_id, rel_orbit, orbit_direction, platform,
  lat, lon, length_m, width_m, orientation_deg, peak_db,
  area_px, in_old_mask, flag_platform, zone

Usage:
  python berth_detect.py --dates 2018-03-15 2021-06-20 2024-01-10 2026-05-08
  python berth_detect.py --dates 2018-03-15  (single date)
"""

import os, sys, json, csv, argparse
from pathlib import Path
from datetime import datetime

import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFont

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder
)
from shapely.geometry import Point, Polygon, shape

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_berth", service_url="https://sh.dataspace.copernicus.eu"
)

# ── AOI ───────────────────────────────────────────────────────────────────────
AOI_BBOX = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RESOLUTION = 10
size = bbox_to_dimensions(AOI_BBOX, resolution=RESOLUTION)

# ── Old anchorage mask rectangle (for in_old_mask flag) ──────────────────────
OLD_MASK = (50.285, 29.185, 50.375, 29.300)   # lon_min, lat_min, lon_max, lat_max

# ── Known offshore platform points ───────────────────────────────────────────
PLATFORMS = [
    (50.14, 29.38), (50.10, 29.35), (50.08, 29.42), (50.05, 29.30),
    (50.18, 29.48), (50.22, 29.50), (50.28, 29.52), (50.35, 29.55),
    (50.40, 29.50), (50.45, 29.45), (50.15, 29.20), (50.12, 29.15),
]
PLATFORM_RADIUS_M = 150   # flag_platform if detection centroid within this

# ── Berth zone polygons (draft; reviewer confirms) ────────────────────────────
BERTH_ZONES_FILE = Path(__file__).parent.parent / "diagnostics" / "berth" / "berth_zones.geojson"

def load_berth_zones():
    with open(BERTH_ZONES_FILE) as f:
        fc = json.load(f)
    return {feat["properties"]["zone"]: shape(feat["geometry"]) for feat in fc["features"]}

# ── Approx land mask (Kharg Island polygon, inline) ──────────────────────────
# Kharg Island coastline — coarse approximation (~500 m accuracy).
# OSM download would require network in GHA; this avoids it.
# Reviewer can replace with precise shapefile.
KHARG_COAST_APPROX = Polygon([
    (50.287, 29.193), (50.294, 29.187), (50.312, 29.183), (50.332, 29.183),
    (50.350, 29.189), (50.368, 29.198), (50.378, 29.211), (50.382, 29.225),
    (50.379, 29.240), (50.372, 29.253), (50.360, 29.263), (50.344, 29.271),
    (50.325, 29.275), (50.305, 29.272), (50.290, 29.265), (50.281, 29.253),
    (50.278, 29.238), (50.280, 29.222), (50.284, 29.208), (50.287, 29.193),
])
LAND_BUFFER_DEG = 20 / 111320   # 20 m in degrees

# ── Helpers ───────────────────────────────────────────────────────────────────

def px_to_lonlat(px, py, bbox, sz):
    lon = bbox.min_x + (px + 0.5) / sz[0] * (bbox.max_x - bbox.min_x)
    lat = bbox.max_y - (py + 0.5) / sz[1] * (bbox.max_y - bbox.min_y)
    return lon, lat

def lonlat_to_px(lon, lat, bbox, sz):
    px = int((lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * sz[0])
    py = int((bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * sz[1])
    return px, py

def haversine_m(lon1, lat1, lon2, lat2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371000
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ/2)**2 + cos(φ1)*cos(φ2)*sin(Δλ/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def adaptive_threshold(arr):
    valid = arr[np.isfinite(arr) & (arr > 0.01)]
    if len(valid) == 0:
        return 0.80
    sea_level = np.percentile(valid, 70)
    sea_pixels = valid[valid <= sea_level]
    if len(sea_pixels) < 100:
        return sea_level + 0.20
    return float(np.clip(sea_level + 3.5 * sea_pixels.std(), 0.55, 0.92))

def build_land_mask(bbox, sz):
    """Rasterise Kharg coastline approximation + 20 m buffer."""
    h, w = sz[1], sz[0]
    mask = np.zeros((h, w), dtype=bool)
    # Buffered island polygon → check each pixel
    buffered = KHARG_COAST_APPROX.buffer(LAND_BUFFER_DEG)
    # Sample pixel centres
    lons = np.linspace(bbox.min_x + 0.5*(bbox.max_x-bbox.min_x)/w,
                       bbox.max_x - 0.5*(bbox.max_x-bbox.min_x)/w, w)
    lats = np.linspace(bbox.max_y - 0.5*(bbox.max_y-bbox.min_y)/h,
                       bbox.min_y + 0.5*(bbox.max_y-bbox.min_y)/h, h)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    from shapely.vectorized import contains
    try:
        mask = contains(buffered, lon_grid, lat_grid)
    except Exception:
        # fallback: rasterise bounding box of island
        ilon0, ilat0, ilon1, ilat1 = OLD_MASK
        ix0, iy1 = lonlat_to_px(ilon0, ilat0, bbox, sz)
        ix1, iy0 = lonlat_to_px(ilon1, ilat1, bbox, sz)
        mask[max(0,iy0):min(h,iy1), max(0,ix0):min(w,ix1)] = True
    return mask

# ── Evalscript (VV in dB, normalised) ────────────────────────────────────────
EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  var db = 10 * Math.log10(s.VV + 1e-10);
  var norm = Math.max(0, Math.min(1, (db + 25) / 40));
  return [norm];
}
"""

def fetch_scene(date_str):
    """Fetch SAR image for exact date. Returns (arr_norm, scene_meta) or (None, None)."""
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE,
        bbox=AOI_BBOX,
        time=(date_str + "T00:00:00Z", date_str + "T23:59:59Z"),
        fields={"include": ["id", "properties.datetime",
                            "properties.sat:relative_orbit",
                            "properties.sat:orbit_state",
                            "properties.platform"]},
    ))
    if not results:
        print(f"  No scene found for {date_str}")
        return None, None

    scene = results[0]
    props = scene.get("properties", {})
    meta = {
        "scene_id":        scene.get("id", ""),
        "rel_orbit":       props.get("sat:relative_orbit"),
        "orbit_direction": props.get("sat:orbit_state", ""),
        "platform":        props.get("platform", ""),
    }

    request = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, date_str),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "DV"}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=AOI_BBOX, size=size, config=config,
    )
    try:
        data = request.get_data()
    except Exception as e:
        print(f"  Fetch error: {e}")
        return None, None

    if not data or data[0] is None:
        return None, None
    raw = np.array(data[0])
    if raw.ndim == 3:
        arr = raw[:, :, 0] if raw.shape[2] >= 1 else raw[0]
    else:
        arr = raw
    if arr.max() < 0.01:
        print(f"  Empty array for {date_str}")
        return None, None
    return arr, meta


def detect(arr, meta, date_str, berth_zones, land_mask):
    """Run detection on one scene. Returns list of detection dicts."""
    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[land_mask] = 0          # suppress land (NOT platforms)

    threshold = adaptive_threshold(arr_m)
    print(f"  threshold={threshold:.3f}")

    labeled, num = ndimage.label(arr_m > threshold)
    detections = []

    for i in range(1, num + 1):
        comp = (labeled == i)
        area_px = int(comp.sum())
        if area_px < 3 or area_px > 600:   # 600 px @ 10m = 60000 m² (very large)
            continue

        ys, xs = np.where(comp)

        # Centroid lon/lat
        cx_px, cy_px = float(xs.mean()), float(ys.mean())
        lon, lat = px_to_lonlat(cx_px, cy_px, AOI_BBOX, size)

        # Peak dB: back-convert norm → dB
        peak_norm = float(arr[comp].max())
        peak_db = (peak_norm * 40) - 25

        # Length/width via PCA on component pixels
        pts = np.column_stack([xs - xs.mean(), ys - ys.mean()])
        if len(pts) >= 2:
            cov = np.cov(pts.T)
            eigvals, eigvecs = np.linalg.eigh(cov)
            idx = np.argsort(eigvals)[::-1]
            eigvals, eigvecs = eigvals[idx], eigvecs[:, idx]
            proj0 = pts @ eigvecs[:, 0]
            proj1 = pts @ eigvecs[:, 1]
            length_px = float(proj0.max() - proj0.min()) + 1
            width_px  = float(proj1.max() - proj1.min()) + 1
            angle_rad = np.arctan2(eigvecs[1, 0], eigvecs[0, 0])
            orientation_deg = float(np.degrees(angle_rad)) % 180
        else:
            length_px = width_px = 1.0
            orientation_deg = 0.0

        length_m = length_px * RESOLUTION
        width_m  = width_px  * RESOLUTION

        # in_old_mask
        olon0, olat0, olon1, olat1 = OLD_MASK
        in_old = int(olon0 <= lon <= olon1 and olat0 <= lat <= olat1)

        # flag_platform
        flag_plat = 0
        for plon, plat in PLATFORMS:
            if haversine_m(lon, lat, plon, plat) <= PLATFORM_RADIUS_M:
                flag_plat = 1
                break

        # zone
        pt = Point(lon, lat)
        zone = "other"
        for zname, zpoly in berth_zones.items():
            if zpoly.contains(pt):
                zone = zname
                break

        detections.append({
            "date":            date_str,
            "scene_id":        meta["scene_id"],
            "rel_orbit":       meta["rel_orbit"],
            "orbit_direction": meta["orbit_direction"],
            "platform":        meta["platform"],
            "lat":             round(lat, 6),
            "lon":             round(lon, 6),
            "length_m":        round(length_m, 1),
            "width_m":         round(width_m, 1),
            "orientation_deg": round(orientation_deg, 1),
            "peak_db":         round(peak_db, 2),
            "area_px":         area_px,
            "in_old_mask":     in_old,
            "flag_platform":   flag_plat,
            "zone":            zone,
            # pixel coords for rendering
            "_cx": int(round(cx_px)), "_cy": int(round(cy_px)),
            "_xs_min": int(xs.min()), "_xs_max": int(xs.max()),
            "_ys_min": int(ys.min()), "_ys_max": int(ys.max()),
        })

    return detections, threshold


ZONE_COLORS = {
    "berth_east": (255, 80,  80),   # red
    "berth_west": (80,  180, 255),  # blue
    "other":      (200, 200,  50),  # yellow
}

def render_aoi(arr, detections, date_str, threshold, out_dir, suffix=""):
    """SAR-crop of full AOI with detection boxes."""
    img_arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
    img = Image.fromarray(img_arr, mode='L').convert('RGB')
    draw = ImageDraw.Draw(img)

    for det in detections:
        color = ZONE_COLORS.get(det["zone"], (200, 200, 50))
        x0 = det["_xs_min"] - 2; x1 = det["_xs_max"] + 2
        y0 = det["_ys_min"] - 2; y1 = det["_ys_max"] + 2
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = f"{det['zone'][0].upper()}{det['length_m']:.0f}m"
        if det["flag_platform"]:
            label += " PLT"
        draw.text((x1 + 2, y0), label, fill=color)

    # Draw berth zone rectangles
    berth_zones_raw = json.loads(BERTH_ZONES_FILE.read_text())
    for feat in berth_zones_raw["features"]:
        coords = feat["geometry"]["coordinates"][0]
        z = feat["properties"]["zone"]
        color = ZONE_COLORS.get(z, (200, 200, 50))
        px_coords = [lonlat_to_px(c[0], c[1], AOI_BBOX, size) for c in coords]
        draw.polygon(px_coords, outline=color)

    fname = f"{date_str}_aoi{suffix}.png"
    img.save(out_dir / fname)
    print(f"  Saved: {fname}")
    return fname


def render_closeup(arr, lon_c, lat_c, half_m, detections_in_crop, date_str, label, out_dir):
    """Render a 600×600 m crop (or half_m × 2 on each side) centred at lon_c, lat_c."""
    half_deg_lon = half_m / (111320 * np.cos(np.deg2rad(lat_c)))
    half_deg_lat = half_m / 111320
    crop_bbox = BBox([lon_c - half_deg_lon, lat_c - half_deg_lat,
                      lon_c + half_deg_lon, lat_c + half_deg_lat], crs=CRS.WGS84)

    # Map crop_bbox to pixel slice in full arr
    ix0, iy1 = lonlat_to_px(crop_bbox.min_x, crop_bbox.min_y, AOI_BBOX, size)
    ix1, iy0 = lonlat_to_px(crop_bbox.max_x, crop_bbox.max_y, AOI_BBOX, size)
    ix0 = max(0, min(ix0, size[0]-1)); ix1 = max(1, min(ix1, size[0]))
    iy0 = max(0, min(iy0, size[1]-1)); iy1 = max(1, min(iy1, size[1]))

    crop = arr[iy0:iy1, ix0:ix1]
    if crop.size == 0:
        return None

    img_arr = (np.clip(crop, 0, 1) * 255).astype(np.uint8)
    img = Image.fromarray(img_arr, mode='L').convert('RGB')
    draw = ImageDraw.Draw(img)

    for det in detections_in_crop:
        color = ZONE_COLORS.get(det["zone"], (200, 200, 50))
        x0 = det["_xs_min"] - ix0 - 2; x1 = det["_xs_max"] - ix0 + 2
        y0 = det["_ys_min"] - iy0 - 2; y1 = det["_ys_max"] - iy0 + 2
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)

    draw.text((4, 4), label, fill=(255, 255, 255))
    fname = f"{label}.png"
    img.save(out_dir / fname)
    return fname


def render_berth_closeup(arr, zone_name, zone_poly, detections, date_str, out_dir):
    """3×3 km crop centred on berth zone centroid."""
    centroid = zone_poly.centroid
    return render_closeup(
        arr, centroid.x, centroid.y, half_m=1500,
        detections_in_crop=[d for d in detections if d["zone"] == zone_name],
        date_str=date_str,
        label=f"{date_str}_{zone_name}_closeup",
        out_dir=out_dir,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="+", required=True,
                        help="Dates to process YYYY-MM-DD")
    args = parser.parse_args()

    out_dir = Path(__file__).parent.parent / "diagnostics" / "berth"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "detections.csv"

    berth_zones = load_berth_zones()
    land_mask = build_land_mask(AOI_BBOX, size)
    print(f"AOI size: {size[0]}x{size[1]} px @ {RESOLUTION}m/px")
    print(f"Land mask: {land_mask.sum()} px masked")

    all_detections = []

    # Load existing CSV to avoid re-processing
    existing_dates = set()
    if csv_path.exists():
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                existing_dates.add(row["date"])
                all_detections.append(row)

    FIELDS = ["date", "scene_id", "rel_orbit", "orbit_direction", "platform",
              "lat", "lon", "length_m", "width_m", "orientation_deg", "peak_db",
              "area_px", "in_old_mask", "flag_platform", "zone"]

    for date_str in args.dates:
        if date_str in existing_dates:
            print(f"\n[{date_str}] already processed, skipping")
            continue

        print(f"\n[{date_str}] Fetching ...")
        arr, meta = fetch_scene(date_str)
        if arr is None:
            print(f"  No data.")
            continue

        print(f"  scene_id={meta['scene_id']}  orbit={meta['rel_orbit']}  dir={meta['orbit_direction']}")

        detections, threshold = detect(arr, meta, date_str, berth_zones, land_mask)
        print(f"  Detections: {len(detections)} "
              f"(east:{sum(1 for d in detections if d['zone']=='berth_east')} "
              f"west:{sum(1 for d in detections if d['zone']=='berth_west')} "
              f"other:{sum(1 for d in detections if d['zone']=='other')} "
              f"plt:{sum(1 for d in detections if d['flag_platform'])})")

        # Render full AOI
        render_aoi(arr, detections, date_str, threshold, out_dir)

        # Berth closeups
        for zname, zpoly in berth_zones.items():
            render_berth_closeup(arr, zname, zpoly, detections, date_str, out_dir)

        # Confident detections (b): up to 2 per date, length_m >= 100, not platform
        confident = sorted(
            [d for d in detections if d["length_m"] >= 100 and not d["flag_platform"]],
            key=lambda d: d["peak_db"], reverse=True
        )[:2]
        for j, det in enumerate(confident):
            render_closeup(
                arr, det["lon"], det["lat"], half_m=300,
                detections_in_crop=[det],
                date_str=date_str,
                label=f"{date_str}_{det['zone']}_confident_{j+1}",
                out_dir=out_dir,
            )

        # No-detection spots (c): berth zone centroids where nothing detected within 400 m
        for zname, zpoly in berth_zones.items():
            zone_dets = [d for d in detections if d["zone"] == zname]
            if not zone_dets:
                centroid = zpoly.centroid
                render_closeup(
                    arr, centroid.x, centroid.y, half_m=300,
                    detections_in_crop=[],
                    date_str=date_str,
                    label=f"{date_str}_{zname}_no_detection",
                    out_dir=out_dir,
                )

        # Potential false positives (c): platform-flagged detections
        false_pos = [d for d in detections if d["flag_platform"]][:5]
        for j, det in enumerate(false_pos):
            render_closeup(
                arr, det["lon"], det["lat"], half_m=300,
                detections_in_crop=[det],
                date_str=date_str,
                label=f"{date_str}_platform_fp_{j+1}",
                out_dir=out_dir,
            )

        # Save ESRI bounding boxes for reviewer
        esri_boxes = []
        for det in confident + false_pos:
            half_deg_lon = 300 / (111320 * np.cos(np.deg2rad(det["lat"])))
            half_deg_lat = 300 / 111320
            esri_boxes.append({
                "label": f"{det['zone']}_l{det['length_m']:.0f}m",
                "bbox": [det["lon"] - half_deg_lon, det["lat"] - half_deg_lat,
                         det["lon"] + half_deg_lon, det["lat"] + half_deg_lat],
            })
        if esri_boxes:
            boxes_path = out_dir / f"{date_str}_esri_boxes.json"
            boxes_path.write_text(json.dumps(esri_boxes, indent=2))
            print(f"  ESRI boxes: {boxes_path.name}")

        # Strip pixel coords before saving
        for det in detections:
            for k in list(det.keys()):
                if k.startswith("_"):
                    del det[k]
        all_detections.extend(detections)

    # Write CSV
    if all_detections:
        clean = [d for d in all_detections if all(not k.startswith("_") for k in d)]
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(clean)
        print(f"\nWrote {len(clean)} detections → {csv_path}")
    else:
        print("\nNo detections written (all dates empty or skipped).")

    # Summary table
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"{'date':<12} {'orbit':>6} {'dir':>5} {'east':>5} {'west':>5} {'other':>6} {'total':>6}")
    from collections import defaultdict
    by_date = defaultdict(list)
    for d in all_detections:
        if not any(str(k).startswith("_") for k in d):
            by_date[d["date"]].append(d)
    for dt in sorted(by_date):
        recs = by_date[dt]
        orb = recs[0].get("rel_orbit", "?") if recs else "?"
        dr  = (recs[0].get("orbit_direction", "?") or "?")[:4] if recs else "?"
        e = sum(1 for r in recs if r["zone"] == "berth_east")
        w = sum(1 for r in recs if r["zone"] == "berth_west")
        o = sum(1 for r in recs if r["zone"] == "other")
        print(f"{dt:<12} {str(orb):>6} {dr:>5} {e:>5} {w:>5} {o:>6} {len(recs):>6}")


if __name__ == "__main__":
    main()
