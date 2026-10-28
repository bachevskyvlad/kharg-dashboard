"""
p8d_coastline_check.py — геопривязка SLC: береговая линия Kharg

Берёт бёрст 2024-10-03 (уже скачан в p8c), строит amplitude PNG
3×3 км вокруг западного берега острова и накладывает береговую
линию Natural Earth (ne_10m_coastline) в географических координатах.

Если береговая линия ложится точно → привязка верна.
Если смещена → печатает смещение в метрах по дальности и азимуту.
"""

import csv, io, math, os, re, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
LOG = OUT_DIR / f"p8d_coastline_{TS}.txt"
_lf = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _lf.write(msg + "\n")

CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG   = "https://catalogue.dataspace.copernicus.eu/odata/v1"
CDSE_DL_BASE   = "https://download.dataspace.copernicus.eu/odata/v1"

# западный берег Kharg: ~29.23 N, 50.295 E — центр окна
WIN_LAT = 29.2300
WIN_LON = 50.2950
WIN_M   = 1500.0   # ±1500 m → 3×3 km
DATE    = "2024-10-03"
DIRECTION = "DESCENDING"


def get_token():
    r = requests.post(CDSE_TOKEN_URL,
        data={"grant_type": "client_credentials",
              "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _list_nodes(token, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        log(f"    _list_nodes HTTP {r.status_code}: {r.text[:300]}")
        return []
    d = r.json()
    return d.get("value", d.get("result", []))


def search_slc(token, date_str):
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (f"{CDSE_CATALOG}/Products"
           f"?$filter=Collection/Name eq 'SENTINEL-1'"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/Value eq 'IW_SLC__1S')"
           f" and OData.CSC.Intersects(area=geography'SRID=4326;POINT({WIN_LON} {WIN_LAT})')"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'orbitDirection' and a/Value eq 'DESCENDING')"
           f" and ContentDate/Start ge {date_str}T00:00:00.000Z"
           f" and ContentDate/Start lt {d1}T00:00:00.000Z"
           f"&$top=5")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def find_iw2_vv_annotation(token, pid, safe_name):
    safe_node = safe_name if safe_name.endswith(".SAFE") else safe_name + ".SAFE"
    url = f"{CDSE_DL_BASE}/Products({pid})/Nodes({safe_node})/Nodes(annotation)/Nodes"
    nodes = _list_nodes(token, url)
    log(f"    annotation nodes ({len(nodes)}): {[n.get('Id','?') for n in nodes[:5]]}")
    for n in nodes:
        nid = n.get("Id", "")
        if re.search(r"iw2.*vv", nid, re.I) and nid.lower().endswith(".xml"):
            return nid, safe_node
    return None, safe_node


def download_xml(token, pid, safe_node, xml_node, sub=None):
    if sub:
        url = (f"{CDSE_DL_BASE}/Products({pid})"
               f"/Nodes({safe_node})/Nodes(annotation)/Nodes({sub})/Nodes({xml_node})/$value")
    else:
        url = (f"{CDSE_DL_BASE}/Products({pid})"
               f"/Nodes({safe_node})/Nodes(annotation)/Nodes({xml_node})/$value")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.content


def find_calibration_node(token, pid, safe_node, xml_node):
    url = f"{CDSE_DL_BASE}/Products({pid})/Nodes({safe_node})/Nodes(annotation)/Nodes(calibration)/Nodes"
    nodes = _list_nodes(token, url)
    stem = xml_node.replace(".xml", "")
    for n in nodes:
        nid = n.get("Id", ""); nid_l = nid.lower()
        if nid_l.startswith("calibration-") and stem in nid and nid_l.endswith(".xml"):
            return nid
    for n in nodes:
        nid = n.get("Id", ""); nid_l = nid.lower()
        if nid_l.startswith("calibration-") and re.search(r"iw2.*vv", nid_l) and nid_l.endswith(".xml"):
            return nid
    return None


def parse_calibration(cal_bytes):
    from scipy.interpolate import RegularGridInterpolator
    root = ET.fromstring(cal_bytes)
    vectors  = root.findall(".//calibrationVector")
    cal_lines  = np.array([int(v.findtext("line"))   for v in vectors], dtype=np.float64)
    cal_pixels = np.array([int(p) for p in vectors[0].findtext("pixel").split()], dtype=np.float64)
    cal_sigma  = np.array([[float(s) for s in v.findtext("sigmaNought").split()] for v in vectors], dtype=np.float64)
    return RegularGridInterpolator((cal_lines, cal_pixels), cal_sigma,
                                   method="linear", bounds_error=False, fill_value=None)


def parse_annotation(xml_bytes):
    root = ET.fromstring(xml_bytes)
    rps  = float(root.findtext(".//rangePixelSpacing"))
    lpb  = int(root.findtext(".//linesPerBurst"))
    spb  = int(root.findtext(".//samplesPerBurst"))
    aps_raw = root.findtext(".//azimuthPixelSpacing")
    aps = float(aps_raw) if aps_raw else float(root.findtext(".//azimuthTimeInterval")) * 7300.0
    gcps = [{"line": int(p.findtext("line")), "pixel": int(p.findtext("pixel")),
              "lat": float(p.findtext("latitude")), "lon": float(p.findtext("longitude"))}
             for p in root.findall(".//geolocationGridPoint")]
    n_bursts = len(root.findall(".//burst"))
    return {"rps": rps, "aps": aps, "lpb": lpb, "spb": spb,
            "gcps": gcps, "n_bursts": n_bursts, "root": root}


def build_interp(gcps):
    from scipy.interpolate import griddata
    pts     = np.array([[g["lat"], g["lon"]] for g in gcps])
    lines   = np.array([g["line"]  for g in gcps], dtype=np.float64)
    samples = np.array([g["pixel"] for g in gcps], dtype=np.float64)
    def interp(lat, lon):
        q  = np.array([[lat, lon]])
        return float(griddata(pts, lines, q, method="linear")[0]), \
               float(griddata(pts, samples, q, method="linear")[0])
    return interp


def build_pixel_to_latlon(gcps, lpb, rps, aps, burst_idx):
    """Build inverse: (row_in_burst, sample) → (lat, lon) via GCP bilinear."""
    from scipy.interpolate import griddata
    pts_ls = np.array([[g["line"], g["pixel"]] for g in gcps], dtype=np.float64)
    lats   = np.array([g["lat"] for g in gcps])
    lons   = np.array([g["lon"] for g in gcps])
    def pix2ll(row_burst, sample):
        global_line = burst_idx * lpb + row_burst
        q = np.array([[global_line, sample]])
        lat = float(griddata(pts_ls, lats, q, method="linear")[0])
        lon = float(griddata(pts_ls, lons, q, method="linear")[0])
        return lat, lon
    return pix2ll


def burst_byte_range(root, burst_idx):
    bursts = root.findall(".//burst")
    b = bursts[burst_idx]
    bo_el = b.find("byteOffset")
    if bo_el is None:
        return None, None
    lpb  = int(root.findtext(".//linesPerBurst"))
    spb  = int(root.findtext(".//samplesPerBurst"))
    bo   = int(bo_el.text)
    return bo, bo + lpb * spb * 4 - 1


def download_burst(token, pid, safe_name, tiff_node, b_start, b_end):
    url = (f"{CDSE_DL_BASE}/Products({pid})"
           f"/Nodes({safe_name})/Nodes(measurement)/Nodes({tiff_node})/$value")
    headers = {"Authorization": f"Bearer {token}", "Range": f"bytes={b_start}-{b_end}"}
    log(f"    Range bytes={b_start}-{b_end}  ({(b_end-b_start+1)/1e6:.1f} MB)")
    r = requests.get(url, headers=headers, stream=True, timeout=600)
    if r.status_code not in (200, 206):
        log(f"    HTTP {r.status_code}"); return None
    chunks = []; n = 0
    for chunk in r.iter_content(chunk_size=10*1024*1024):
        chunks.append(chunk); n += len(chunk)
        log(f"    ... {n//1024//1024} MB")
    data = b"".join(chunks)
    log(f"    done {len(data)/1e6:.1f} MB")
    return data


def apply_sigma0(z_complex, burst_idx, lpb, cal_interp):
    n_lines, n_samples = z_complex.shape
    global_lines = burst_idx * lpb + np.arange(n_lines, dtype=np.float64)
    samples      = np.arange(n_samples, dtype=np.float64)
    gg, ss = np.meshgrid(global_lines, samples, indexing="ij")
    coords  = np.stack([gg.ravel(), ss.ravel()], axis=-1)
    A = cal_interp(coords).reshape(n_lines, n_samples)
    A = np.where(A > 0, A, 1.0)
    return ((np.abs(z_complex) ** 2) / (A ** 2)).astype(np.float32)


def fetch_ne_coastline():
    """Download Natural Earth 10m coastline shapefile and return list of (lat,lon) polylines."""
    import zipfile, tempfile
    url = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_coastline.zip"
    log(f"  downloading NE coastline from {url} ...")
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        log(f"  NE primary failed: {e}, trying backup ...")
        url2 = "https://github.com/nvkelso/natural-earth-vector/raw/master/10m_physical/ne_10m_coastline.zip"
        r = requests.get(url2, timeout=60)
        r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    # extract to temp dir
    tmpdir = Path("/tmp/ne_coastline_slc")
    tmpdir.mkdir(exist_ok=True)
    zf.extractall(tmpdir)
    shp = next(tmpdir.glob("*.shp"), None)
    if shp is None:
        log("  ERROR: no .shp in archive"); return []
    import shapefile
    sf = shapefile.Reader(str(shp))
    lines = []
    for sr in sf.shapeRecords():
        pts = sr.shape.points  # [(lon, lat), ...]
        parts = list(sr.shape.parts) + [len(pts)]
        for i in range(len(parts) - 1):
            seg = pts[parts[i]:parts[i+1]]
            # filter to Kharg bounding box with margin
            seg_filtered = [(lon, lat) for (lon, lat) in seg
                            if 50.25 <= lon <= 50.40 and 29.18 <= lat <= 29.28]
            if len(seg_filtered) >= 2:
                lines.append(seg_filtered)
    log(f"  NE coastline: {len(lines)} segments near Kharg")
    return lines


def main():
    log(f"p8d_coastline_check.py  {datetime.now(timezone.utc).isoformat()}")

    # ensure pyshp is available
    try:
        import shapefile
    except ImportError:
        log("installing pyshp ...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyshp", "-q"])
        import shapefile

    token = get_token()
    log("token OK")

    log(f"\n=== SLC {DATE} ===")
    items = search_slc(token, DATE)
    if not items:
        log("FATAL: no SLC product"); sys.exit(1)
    prod = items[0]
    pid  = prod["Id"]
    safe = prod["Name"]
    log(f"  {safe}")

    xml_node, safe_node = find_iw2_vv_annotation(token, pid, safe)
    if not xml_node:
        log("FATAL: no IW2/VV annotation"); sys.exit(1)
    log(f"  annotation: {xml_node}")

    xml_bytes = download_xml(token, pid, safe_node, xml_node)
    info      = parse_annotation(xml_bytes)
    log(f"  rps={info['rps']:.3f}m  aps={info['aps']:.3f}m  lpb={info['lpb']}  spb={info['spb']}")

    cal_node = find_calibration_node(token, pid, safe_node, xml_node)
    if not cal_node:
        log("FATAL: no calibration node"); sys.exit(1)
    cal_bytes  = download_xml(token, pid, safe_node, cal_node, sub="calibration")
    cal_interp = parse_calibration(cal_bytes)

    interp    = build_interp(info["gcps"])
    li_c, sa_c = interp(WIN_LAT, WIN_LON)
    burst_idx  = int(li_c // info["lpb"])
    log(f"  window centre → line_global={li_c:.1f}  sample={sa_c:.1f}  burst={burst_idx}")

    b_start, b_end = burst_byte_range(info["root"], burst_idx)
    if b_start is None:
        log("FATAL: no byteOffset"); sys.exit(1)

    tiff_node = xml_node.replace(".xml", ".tiff")
    raw = download_burst(token, pid, safe_node, tiff_node, b_start, b_end)
    if raw is None:
        log("FATAL: download failed"); sys.exit(1)

    arr16 = np.frombuffer(raw, dtype=np.int16).reshape(info["lpb"], info["spb"], 2)
    z     = arr16[:, :, 0].astype(np.float32) + 1j * arr16[:, :, 1].astype(np.float32)

    sigma0 = apply_sigma0(z, burst_idx, info["lpb"], cal_interp)
    log(f"  sigma0 shape={sigma0.shape}  mean_dB={10*np.log10(sigma0[sigma0>0].mean()):.2f}")

    # ── crop 3×3 km window ──────────────────────────────────────────────────
    rps = info["rps"]; aps = info["aps"]; lpb = info["lpb"]
    li_b = li_c - burst_idx * lpb   # row within burst (unaveraged)
    half_rg = WIN_M / rps
    half_az = WIN_M / aps
    l0 = max(0, int(li_b - half_az)); l1 = min(sigma0.shape[0], int(li_b + half_az) + 1)
    s0 = max(0, int(sa_c - half_rg)); s1 = min(sigma0.shape[1], int(sa_c + half_rg) + 1)
    crop = sigma0[l0:l1, s0:s1]
    log(f"  crop shape={crop.shape}  l0={l0} l1={l1}  s0={s0} s1={s1}")

    db = 10 * np.log10(np.clip(crop, 1e-10, None))
    vmin, vmax = np.percentile(db, [2, 98])

    # ── pixel → lat/lon grid for the crop ───────────────────────────────────
    pix2ll = build_pixel_to_latlon(info["gcps"], lpb, rps, aps, burst_idx)
    # corner coordinates for imshow extent
    lat_tl, lon_tl = pix2ll(l0, s0)
    lat_tr, lon_tr = pix2ll(l0, s1)
    lat_bl, lon_bl = pix2ll(l1, s0)
    lat_br, lon_br = pix2ll(l1, s1)
    lon_min = min(lon_tl, lon_tr, lon_bl, lon_br)
    lon_max = max(lon_tl, lon_tr, lon_bl, lon_br)
    lat_min = min(lat_tl, lat_tr, lat_bl, lat_br)
    lat_max = max(lat_tl, lat_tr, lat_bl, lat_br)
    log(f"  crop extent: lat [{lat_min:.5f}, {lat_max:.5f}]  lon [{lon_min:.5f}, {lon_max:.5f}]")

    # ── also compute per-pixel lat/lon grid for distortion correction ────────
    # sample every 10th pixel to build dense grid
    row_idxs = np.arange(l0, l1, 10)
    col_idxs = np.arange(s0, s1, 10)
    from scipy.interpolate import griddata
    pts_ls   = np.array([[g["line"], g["pixel"]] for g in info["gcps"]], dtype=np.float64)
    gcp_lats = np.array([g["lat"] for g in info["gcps"]])
    gcp_lons = np.array([g["lon"] for g in info["gcps"]])
    RR, CC = np.meshgrid(row_idxs + burst_idx * lpb, col_idxs, indexing="ij")
    pts_q = np.stack([RR.ravel(), CC.ravel()], axis=-1)
    lat_grid_s = griddata(pts_ls, gcp_lats, pts_q, method="linear").reshape(len(row_idxs), len(col_idxs))
    lon_grid_s = griddata(pts_ls, gcp_lons, pts_q, method="linear").reshape(len(row_idxs), len(col_idxs))
    log(f"  dense grid computed ({lat_grid_s.shape})")

    # ── fetch coastline ──────────────────────────────────────────────────────
    coast_segs = fetch_ne_coastline()

    # ── convert coastline lon/lat → pixel offsets in the crop ───────────────
    # approximate: use linear mapping from extent
    # also project via inverse interp for accuracy check
    from scipy.interpolate import RegularGridInterpolator as RGI
    row_axis = np.arange(len(row_idxs), dtype=np.float64)
    col_axis = np.arange(len(col_idxs), dtype=np.float64)
    # clip NaN
    lat_grid_s = np.where(np.isfinite(lat_grid_s), lat_grid_s, np.nanmean(lat_grid_s))
    lon_grid_s = np.where(np.isfinite(lon_grid_s), lon_grid_s, np.nanmean(lon_grid_s))

    # ── plot ─────────────────────────────────────────────────────────────────
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    fig, axes = plt.subplots(1, 2, figsize=(14, 7))

    # Panel 1: imshow with simple linear geographic extent (may show shear)
    ax = axes[0]
    ax.imshow(db, extent=[lon_min, lon_max, lat_min, lat_max],
              cmap="gray", vmin=vmin, vmax=vmax,
              origin="upper", aspect="auto", interpolation="nearest")
    for seg in coast_segs:
        lons_s = [p[0] for p in seg]
        lats_s = [p[1] for p in seg]
        ax.plot(lons_s, lats_s, "-", color="cyan", lw=1.2, label="NE coastline")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title(f"SLC sigma0 dB  +  NE coastline\n{DATE}  IW2/VV  (linear extent approx)")
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)

    # Panel 2: close-up with extent from dense GCP grid centre columns
    ax2 = axes[1]
    # use centre row/col for proper lat/lon axis
    mid_row = len(row_idxs) // 2
    mid_col = len(col_idxs) // 2
    lat_centre = float(lat_grid_s[mid_row, mid_col])
    lon_centre = float(lon_grid_s[mid_row, mid_col])

    # measure pixel → metre offsets to check scale
    lat_top = float(lat_grid_s[0, mid_col])
    lat_bot = float(lat_grid_s[-1, mid_col])
    lon_left  = float(lon_grid_s[mid_row, 0])
    lon_right = float(lon_grid_s[mid_row, -1])
    h_m = abs(lat_top - lat_bot) * 111320
    w_m = abs(lon_right - lon_left) * 111320 * math.cos(math.radians(lat_centre))
    log(f"  dense grid span: {w_m:.0f} m W-E  ×  {h_m:.0f} m N-S  (should be ~3000×3000)")

    ax2.imshow(db, extent=[lon_min, lon_max, lat_min, lat_max],
               cmap="gray", vmin=vmin, vmax=vmax,
               origin="upper", aspect="equal", interpolation="nearest")
    for seg in coast_segs:
        lons_s = [p[0] for p in seg]; lats_s = [p[1] for p in seg]
        ax2.plot(lons_s, lats_s, "-", color="#00ffff", lw=1.5)
    ax2.plot(WIN_LON, WIN_LAT, "+", color="yellow", ms=10, lw=1.5, label="window centre")
    ax2.set_xlabel("Longitude"); ax2.set_ylabel("Latitude")
    ax2.set_title(f"aspect=equal  |  lat_ctr={lat_centre:.4f}  lon_ctr={lon_centre:.4f}")
    ax2.set_xlim(lon_min, lon_max); ax2.set_ylim(lat_min, lat_max)

    fig.suptitle(f"Kharg western shoreline — SLC IW2/VV  {DATE}  (burst {burst_idx})", fontsize=12)
    fig.tight_layout()

    out_png = OUT_DIR / f"p8d_coastline_{TS}.png"
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    log(f"  saved {out_png.name}")

    # ── quantify offset: find water/land boundary pixel and compare to NE coast ──
    # water pixels: median sigma0 in left column strip (should be water, lon < 50.29)
    # find column where sigma0 transitions from low (water) to high (land)
    # use profile at row=mid crop
    mid_row_crop = (l1 - l0) // 2
    profile_row  = db[mid_row_crop, :]
    # sample lon along that row using dense grid interpolation
    col_dense_idxs = np.arange(0, crop.shape[1], max(1, crop.shape[1] // 200))
    # recompute actual lons for that row
    global_row = burst_idx * lpb + l0 + mid_row_crop
    pts_row = np.stack([np.full(len(col_dense_idxs), global_row),
                        (col_dense_idxs + s0).astype(np.float64)], axis=-1)
    lon_row = griddata(pts_ls, gcp_lons, pts_row, method="linear")
    lat_row = griddata(pts_ls, gcp_lats, pts_row, method="linear")

    # NE coastline lon at the latitude of mid_row (Kharg western shore ~ 50.295–50.305)
    coast_lon_at_lat = []
    for seg in coast_segs:
        for i in range(len(seg) - 1):
            la0, lo0 = seg[i][1],  seg[i][0]
            la1, lo1 = seg[i+1][1], seg[i+1][0]
            target = lat_row[len(lat_row)//2] if np.isfinite(lat_row[len(lat_row)//2]) else WIN_LAT
            if (la0 - target) * (la1 - target) <= 0 and (la1 - la0) != 0:
                t   = (target - la0) / (la1 - la0)
                coast_lon_at_lat.append(lo0 + t * (lo1 - lo0))
    log(f"  NE coast lon at lat={WIN_LAT}: {coast_lon_at_lat}")

    # find transition in SAR profile (water→land = dB rising sharply)
    sigma0_row = 10 ** (profile_row / 10)
    smooth = np.convolve(sigma0_row, np.ones(5)/5, mode='same')
    grad   = np.gradient(smooth)
    # find peak gradient (sharpest water→land transition), only in left half
    left_half = len(grad) // 2
    if left_half > 0:
        peak_col_local = int(np.argmax(grad[:left_half]))
    else:
        peak_col_local = 0
    peak_col_global = s0 + peak_col_local
    pts_peak = np.array([[float(global_row), float(peak_col_global)]])
    lon_peak = float(griddata(pts_ls, gcp_lons, pts_peak, method="linear")[0])
    lat_peak = float(griddata(pts_ls, gcp_lats, pts_peak, method="linear")[0])
    log(f"  SAR transition lon={lon_peak:.5f}  lat={lat_peak:.5f}")

    if coast_lon_at_lat:
        ne_lon = np.median(coast_lon_at_lat)
        delta_lon = lon_peak - ne_lon
        delta_rg_m = delta_lon * 111320 * math.cos(math.radians(WIN_LAT))
        log(f"  NE coast lon={ne_lon:.5f}  SAR transition lon={lon_peak:.5f}")
        log(f"  OFFSET range: {delta_rg_m:+.1f} m  (positive = SAR right/east of NE)")
        log(f"  OFFSET azimuth: estimated 0 m (not computed from single row)")
        if abs(delta_rg_m) < 50:
            log("  RESULT: offset < 50 m → geolocation CORRECT")
        else:
            log(f"  RESULT: offset {delta_rg_m:+.0f} m → CHECK geolocation")
    else:
        log("  RESULT: no NE coastline segment found at this latitude — check bbox")

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
