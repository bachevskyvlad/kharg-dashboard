"""
berth_pc_fetch.py — Planetary Computer S1-RTC catalog + rasterio fetch.
Replaces sentinelhub-based S1 discovery and fetch in all berth scripts.
No API key required; collection sentinel-1-rtc is openly accessible.
"""
import os
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
from rasterio.crs import CRS as RioCRS

os.environ.setdefault("GDAL_HTTP_UNSAFESSL", "YES")

PC_STAC_URL  = "https://planetarycomputer.microsoft.com/api/stac/v1"
S1_COLLECTION = "sentinel-1-rtc"
_RESOLUTION   = 10  # metres


def _make_client():
    import pystac_client
    import planetary_computer
    return pystac_client.Client.open(PC_STAC_URL, modifier=planetary_computer.sign_inplace)


def discover_dates_pc(date_from, date_to, bbox_wgs84, orbits=(35, 101)):
    """
    Search PC STAC for S1-RTC scenes covering bbox in [date_from, date_to].
    bbox_wgs84: [min_lon, min_lat, max_lon, max_lat]
    orbits: tuple of relative orbit numbers to keep.
    Returns sorted list of dicts: {date_str, orbit, item, dt_raw}.
    """
    client = _make_client()
    search = client.search(
        collections=[S1_COLLECTION],
        bbox=bbox_wgs84,
        datetime=f"{date_from}/{date_to}",
    )
    grouped = {}
    for item in search.items():
        props = item.properties
        orb = props.get("sat:relative_orbit")
        if orb not in orbits:
            continue
        dt_obj = item.datetime
        dt_raw = dt_obj.strftime("%Y-%m-%dT%H:%M:%SZ") if dt_obj else ""
        date_str = dt_raw[:10]
        key = (date_str, int(orb))
        # keep the first (or any) item per (date, orbit)
        if key not in grouped:
            grouped[key] = {"date_str": date_str, "orbit": int(orb),
                            "item": item, "dt_raw": dt_raw}
    return sorted(grouped.values(), key=lambda x: (x["date_str"], x["orbit"]))


def find_item_pc(date_str, orbit, bbox_wgs84):
    """
    Find a single S1-RTC item for the given date + orbit.
    bbox_wgs84: [min_lon, min_lat, max_lon, max_lat]
    Returns item or None.
    """
    client = _make_client()
    search = client.search(
        collections=[S1_COLLECTION],
        bbox=bbox_wgs84,
        datetime=f"{date_str}/{date_str}",
        query={"sat:relative_orbit": {"eq": int(orbit)}},
    )
    items = list(search.items())
    if not items:
        return None
    items.sort(key=lambda x: x.datetime or "", reverse=True)
    return items[0]


def fetch_s1_pc(item, bbox_obj, resolution=_RESOLUTION):
    """
    Fetch VV linear backscatter from a PC S1-RTC item, cropped to bbox_obj.
    bbox_obj: sentinelhub BBox (or any object with .min_x/.min_y/.max_x/.max_y).
    Returns float32 numpy array (gamma0-RTC linear power), or None on error.
    """
    target_bbox = [bbox_obj.min_x, bbox_obj.min_y, bbox_obj.max_x, bbox_obj.max_y]
    lat_mid  = (bbox_obj.min_y + bbox_obj.max_y) / 2
    width_m  = (bbox_obj.max_x - bbox_obj.min_x) * 111320 * np.cos(np.deg2rad(lat_mid))
    height_m = (bbox_obj.max_y - bbox_obj.min_y) * 111320
    target_w = max(1, round(width_m  / resolution))
    target_h = max(1, round(height_m / resolution))
    dst_crs       = RioCRS.from_epsg(4326)
    dst_transform = from_bounds(*target_bbox, target_w, target_h)

    asset = item.assets.get("vv") or item.assets.get("VV")
    if asset is None:
        print(f"  no vv asset for {item.id}")
        return None

    try:
        with rasterio.open(asset.href) as src:
            out = np.zeros((target_h, target_w), dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=out,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
        # nodata / negative → small positive (consistent with SH min-clip of 1e-10)
        out = np.where(out <= 0, np.float32(1e-10), out)
        return out
    except Exception as e:
        print(f"  PC fetch err ({item.id}): {e}")
        return None
