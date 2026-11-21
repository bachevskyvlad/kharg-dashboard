"""
berth_optics_vs_sar.py — Sentinel-2 optical vs SAR contact sheet for ground-truth annotation.

For each validation date+orbit, searches for Sentinel-2 L2A scene on the same day
with cloud cover < 20% over pier bboxes.
Outputs:
  diagnostics/berth_v4/contact_sheet_optics/optics_contact_sheet.png
  diagnostics/berth_v4/contact_sheet_optics/optics_dates.csv  (template for K to fill)
"""

import os, sys, csv
from pathlib import Path

import numpy as np
from scipy.ndimage import zoom as nd_zoom

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_optics", service_url="https://sh.dataspace.copernicus.eu"
)
S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2l2a_cdse", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT    = Path(__file__).parent.parent
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "contact_sheet_optics"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}
BERTHS     = ["sea_island", "east_t_head", "southeast_pier"]
CELL_KM    = 1.0
RESOLUTION = 10
CLOUD_MAX  = 20.0   # max cloud cover % to accept S2 scene

VALIDATION_DATES_ORBITS = [
    ("2019-01-15", 35),  ("2019-03-08", 101), ("2019-06-24", 101),
    ("2020-01-10", 35),  ("2020-03-14", 101), ("2020-06-14", 35),
    ("2020-06-30", 101), ("2021-01-08", 101), ("2021-04-10", 35),
    ("2021-06-25", 101), ("2022-01-15", 101), ("2022-06-16", 35),
    ("2023-07-09", 101), ("2024-01-13", 35),  ("2024-06-29", 35),
    ("2024-07-03", 101), ("2025-01-07", 35),  ("2025-01-11", 101),
    ("2026-04-14", 35),  ("2026-04-18", 101),
    ("2017-03-06", 101), ("2017-08-29", 35),  ("2018-01-12", 101),
    ("2018-05-20", 35),  ("2019-05-15", 35),  ("2019-10-22", 101),
    ("2020-04-03", 35),  ("2020-05-13", 101), ("2021-02-01", 101),
    ("2021-07-27", 35),  ("2022-02-04", 35),  ("2022-02-08", 101),
    ("2023-09-03", 35),  ("2023-10-25", 101), ("2024-05-16", 101),
    ("2024-12-26", 35),  ("2025-09-02", 101), ("2025-11-15", 35),
    ("2026-01-02", 35),  ("2026-09-28", 101),
]

# ── Evalscripts ───────────────────────────────────────────────────────────────
EVALSCRIPT_S2_RGB = """
//VERSION=3
function setup() {
  return {
    input: [{bands: ["B04","B03","B02"]}],
    output: {bands: 3, sampleType: "FLOAT32"}
  };
}
function evaluatePixel(s) {
  return [s.B04 * 3.5, s.B03 * 3.5, s.B02 * 3.5];
}
"""

EVALSCRIPT_S1_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
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

# ── Catalog search ────────────────────────────────────────────────────────────
def find_s2_scene(date_str, bbox_obj):
    """
    Search for S2 L2A scene on given date with cloud cover < CLOUD_MAX.
    Returns (datetime_str, cloud_cover) or (None, None).
    """
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S2_CDSE, bbox=bbox_obj,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include": ["properties.datetime", "properties.eo:cloud_cover"]},
        ))
    except Exception as e:
        print(f"    S2 catalog error ({date_str}): {e}")
        return None, None

    best_dt = None; best_cc = 999.0
    for r in results:
        p = r.get("properties", {})
        cc = float(p.get("eo:cloud_cover", 100))
        dt = p.get("datetime", "")
        if cc < best_cc:
            best_cc = cc; best_dt = dt

    if best_dt and best_cc < CLOUD_MAX:
        return best_dt, best_cc
    return None, None


def find_s1_scene(date_str, orbit, bbox_obj):
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S1_CDSE, bbox=bbox_obj,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"    S1 catalog error ({date_str}): {e}")
        return None
    for r in results:
        p = r.get("properties", {})
        if int(p.get("sat:relative_orbit", 0)) == orbit:
            return p.get("datetime", "")
    return None


# ── Fetch ─────────────────────────────────────────────────────────────────────
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
        data = req.get_data()
        arr = data[0]
        if arr.ndim == 2: arr = np.stack([arr]*3, axis=-1)
        return np.clip(arr, 0, 1).astype(np.float32)
    except Exception as e:
        print(f"    S2 fetch failed ({dt_str[:10]}): {e}")
        return None


def fetch_s1_db(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1_LINEAR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing": {"backCoeff": "SIGMA0_ELLIPSOID",
                                       "orthorectify": True,
                                       "demInstance": "COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        data = req.get_data()
        arr = data[0]
        if arr.ndim == 3: arr = arr[:,:,0]
        lin = arr.astype(np.float32)
        db = np.where(lin > 0, 10*np.log10(lin), np.nan)
        return db
    except Exception as e:
        print(f"    S1 fetch failed ({dt_str[:10]}): {e}")
        return None


# ── Drawing ───────────────────────────────────────────────────────────────────
def _hide_ticks(ax):
    ax.set_xticks([]); ax.set_yticks([])


def draw_s2(ax, rgb, cloud_pct, date_str):
    ax.set_facecolor("#111"); _hide_ticks(ax)
    if rgb is not None:
        ax.imshow(rgb, interpolation="nearest", origin="upper")
    else:
        ax.text(0.5, 0.5, "NO S2", color="#666",
                ha="center", va="center", transform=ax.transAxes, fontsize=5)
    cc_str = f"cc={cloud_pct:.0f}%" if cloud_pct is not None else ""
    ax.set_xlabel(f"S2 {date_str} {cc_str}", color="#aaa", fontsize=3.5, labelpad=1)


def draw_s1(ax, db):
    ax.set_facecolor("#111"); _hide_ticks(ax)
    if db is not None and np.isfinite(db).any():
        vmin, vmax = np.nanpercentile(db[np.isfinite(db)], [2, 98])
        ax.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest", origin="upper")
    else:
        ax.text(0.5, 0.5, "NO SAR", color="#666",
                ha="center", va="center", transform=ax.transAxes, fontsize=5)
    ax.set_xlabel("SAR VV dB", color="#aaa", fontsize=3.5, labelpad=1)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Cell bboxes
    cell_bboxes = {}
    for berth, pts in BERTH_SPEC.items():
        clon, clat = berth_center(pts)
        cell_bboxes[berth] = cell_bbox_for(clon, clat)

    # Use sea_island bbox as representative for catalog search (all berths close)
    aoi_full = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

    # Find dates with S2 coverage
    print("Searching S2 L2A scenes for all validation dates ...")
    rows_found = []   # (date_str, orbit, s2_dt, cloud_pct)
    rows_all   = []   # for CSV template

    for date_str, orbit in VALIDATION_DATES_ORBITS:
        s2_dt, cc = find_s2_scene(date_str, aoi_full)
        s1_dt     = find_s1_scene(date_str, orbit, aoi_full)
        has_s2 = s2_dt is not None
        has_s1 = s1_dt is not None
        print(f"  {date_str} o{orbit}: S1={'ok' if has_s1 else 'NO'} "
              f"S2={'ok cc={cc:.0f}%' if has_s2 else 'NO'}")
        rows_all.append({
            "date":  date_str, "orbit": orbit,
            "s1_ok": int(has_s1), "s2_ok": int(has_s2),
            "s2_cloud_pct": f"{cc:.1f}" if cc is not None else "",
            "sea_island_optical_ref":    "",
            "east_t_head_optical_ref":   "",
            "southeast_pier_optical_ref": "",
        })
        if has_s2 and has_s1:
            rows_found.append((date_str, orbit, s2_dt, cc, s1_dt))

    # Write CSV template
    csv_path = OUT_DIR / "optics_dates.csv"
    fieldnames = ["date","orbit","s1_ok","s2_ok","s2_cloud_pct",
                  "sea_island_optical_ref","east_t_head_optical_ref",
                  "southeast_pier_optical_ref"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows_all)
    print(f"CSV template: {csv_path} ({len(rows_all)} dates, {len(rows_found)} with S2)")

    if not rows_found:
        print("No dates with both S1 and S2 — no PNG to produce.")
        return

    # Build contact sheet for dates with S2
    n_rows = len(rows_found)
    n_cols = len(BERTHS)
    IMG_H  = 1.4
    SAR_H  = 1.0
    COL_W  = 1.4

    fig = plt.figure(figsize=(n_cols * COL_W,
                               n_rows * (IMG_H + SAR_H + 0.08)))
    fig.patch.set_facecolor("#1a1a1a")
    outer_gs = GridSpec(
        n_rows, n_cols, figure=fig,
        hspace=0.15, wspace=0.03,
        top=0.985, bottom=0.005, left=0.07, right=0.995,
    )

    print(f"Rendering {n_rows} x {n_cols} cells ...")

    for ri, (date_str, orbit, s2_dt, cc, s1_dt) in enumerate(rows_found):
        for ci, berth in enumerate(BERTHS):
            bbox_obj = cell_bboxes[berth]

            inner = GridSpecFromSubplotSpec(
                2, 1,
                subplot_spec=outer_gs[ri, ci],
                hspace=0.03,
                height_ratios=[IMG_H, SAR_H],
            )
            ax_s2  = fig.add_subplot(inner[0])
            ax_s1  = fig.add_subplot(inner[1])

            if ci == 0:
                ax_s2.set_ylabel(
                    f"{date_str}\no{orbit}",
                    color="#bbb", fontsize=3.5, rotation=0,
                    labelpad=30, va="center")
            if ri == 0:
                ax_s2.set_title(
                    berth.replace("_"," "),
                    color="white", fontsize=5.5, pad=2, fontweight="bold")

            rgb = fetch_s2_rgb(s2_dt, bbox_obj)
            db  = fetch_s1_db(s1_dt, bbox_obj)
            print(f"  {date_str} {berth}: "
                  f"S2={'ok' if rgb is not None else 'FAIL'} "
                  f"S1={'ok' if db is not None else 'FAIL'}")

            draw_s2(ax_s2, rgb, cc, date_str)
            draw_s1(ax_s1, db)

            # Annotation box in upper-right of S2 panel
            ax_s2.text(0.98, 0.98, "OCC / EMP / ?",
                       color="#ffcc44", ha="right", va="top",
                       transform=ax_s2.transAxes, fontsize=3.5,
                       bbox=dict(boxstyle="square,pad=0.1",
                                 fc="black", alpha=0.7, lw=0))

    fig.text(
        0.5, 0.998,
        f"Optical (S2 L2A, cloud<{CLOUD_MAX:.0f}%) vs SAR | "
        f"Label: OCC=ship at pier  EMP=no ship  ?=unclear",
        color="white", fontsize=5, ha="center", va="top")

    out_png = OUT_DIR / "optics_contact_sheet.png"
    fig.savefig(out_png, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"Saved: {out_png} ({out_png.stat().st_size // 1024} KB)")
    plt.close(fig)


if __name__ == "__main__":
    main()
