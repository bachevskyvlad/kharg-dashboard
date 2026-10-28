"""
p8e_burst_overview.py — нарисовать весь бёрст целиком и найти Kharg

Для каждого бёрста IW2/VV продукта 2024-10-03:
  - скачать, нарисовать amplitude dB с прореживанием ×8 по дальности
  - отметить GCP-предсказание центра фермы (29.2296N, 50.3126E)
  - вывести координаты GCP-точки в пикселях

Море ≈ −20..−25 dB, суша ≈ −5..−15 dB — разница должна быть видна.
"""

import io, math, os, re, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
LOG = OUT_DIR / f"p8e_burst_overview_{TS}.txt"
_lf = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _lf.write(msg + "\n")

CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG   = "https://catalogue.dataspace.copernicus.eu/odata/v1"
CDSE_DL_BASE   = "https://download.dataspace.copernicus.eu/odata/v1"

KHARG_LAT = 29.2296
KHARG_LON = 50.3126
DATE      = "2024-10-03"
DIRECTION = "DESCENDING"

# downsample factor for the overview image
DS_RG = 8   # every 8th sample in range → 25448/8 ≈ 3181 cols
DS_AZ = 1   # keep all azimuth lines (1510)


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
        log(f"  _list_nodes HTTP {r.status_code}: {r.text[:200]}")
        return []
    d = r.json()
    return d.get("value", d.get("result", []))


def search_slc(token):
    d1 = (datetime.strptime(DATE, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (f"{CDSE_CATALOG}/Products"
           f"?$filter=Collection/Name eq 'SENTINEL-1'"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/Value eq 'IW_SLC__1S')"
           f" and OData.CSC.Intersects(area=geography'SRID=4326;POINT({KHARG_LON} {KHARG_LAT})')"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'orbitDirection' and a/Value eq 'DESCENDING')"
           f" and ContentDate/Start ge {DATE}T00:00:00.000Z"
           f" and ContentDate/Start lt {d1}T00:00:00.000Z"
           f"&$top=5")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def find_iw2_vv_annotation(token, pid, safe_name):
    safe_node = safe_name if safe_name.endswith(".SAFE") else safe_name + ".SAFE"
    url = f"{CDSE_DL_BASE}/Products({pid})/Nodes({safe_node})/Nodes(annotation)/Nodes"
    nodes = _list_nodes(token, url)
    for n in nodes:
        nid = n.get("Id", "")
        if re.search(r"iw2.*vv", nid, re.I) and nid.lower().endswith(".xml"):
            return nid, safe_node
    return None, safe_node


def download_annotation_xml(token, pid, safe_node, xml_node):
    url = (f"{CDSE_DL_BASE}/Products({pid})"
           f"/Nodes({safe_node})/Nodes(annotation)/Nodes({xml_node})/$value")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.content


def parse_annotation(xml_bytes):
    root = ET.fromstring(xml_bytes)
    rps  = float(root.findtext(".//rangePixelSpacing"))
    lpb  = int(root.findtext(".//linesPerBurst"))
    spb  = int(root.findtext(".//samplesPerBurst"))
    aps_raw = root.findtext(".//azimuthPixelSpacing")
    aps = float(aps_raw) if aps_raw else float(root.findtext(".//azimuthTimeInterval")) * 7300.0
    gcps = [{"line": int(p.findtext("line")), "pixel": int(p.findtext("pixel")),
              "lat":  float(p.findtext("latitude")), "lon": float(p.findtext("longitude"))}
             for p in root.findall(".//geolocationGridPoint")]
    n_bursts = len(root.findall(".//burst"))
    return {"rps": rps, "aps": aps, "lpb": lpb, "spb": spb,
            "gcps": gcps, "n_bursts": n_bursts, "root": root}


def gcp_predict(gcps, lat, lon):
    """Return (line_global, sample) via griddata from GCPs."""
    from scipy.interpolate import griddata
    pts  = np.array([[g["lat"], g["lon"]] for g in gcps])
    lines   = np.array([g["line"]  for g in gcps], dtype=np.float64)
    samples = np.array([g["pixel"] for g in gcps], dtype=np.float64)
    q = np.array([[lat, lon]])
    li = float(griddata(pts, lines,   q, method="linear")[0])
    sa = float(griddata(pts, samples, q, method="linear")[0])
    return li, sa


def burst_byte_range(root, burst_idx):
    bursts = root.findall(".//burst")
    b = bursts[burst_idx]
    bo_el = b.find("byteOffset")
    if bo_el is None:
        return None, None
    lpb = int(root.findtext(".//linesPerBurst"))
    spb = int(root.findtext(".//samplesPerBurst"))
    bo  = int(bo_el.text)
    return bo, bo + lpb * spb * 4 - 1


def download_burst(token, pid, safe_name, tiff_node, b_start, b_end):
    url = (f"{CDSE_DL_BASE}/Products({pid})"
           f"/Nodes({safe_name})/Nodes(measurement)/Nodes({tiff_node})/$value")
    headers = {"Authorization": f"Bearer {token}", "Range": f"bytes={b_start}-{b_end}"}
    log(f"    Range {b_start}-{b_end}  ({(b_end-b_start+1)/1e6:.1f} MB)")
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


def plot_burst_overview(amp_db, burst_idx, lpb, spb, rps, aps,
                        li_g_farm, sa_farm, n_bursts, out_path):
    """Plot downsampled amplitude + mark predicted farm centre."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # downsample
    img = amp_db[:, ::DS_RG]
    n_rows, n_cols = img.shape
    vmin, vmax = np.percentile(img[np.isfinite(img)], [1, 99])

    # physical extents (metres from burst top-left)
    w_m = spb * rps
    h_m = lpb * aps

    fig, ax = plt.subplots(figsize=(16, 5))
    ax.imshow(img, cmap="gray", vmin=vmin, vmax=vmax,
              extent=[0, w_m/1000, h_m/1000, 0],
              aspect="auto", origin="upper", interpolation="nearest")

    # farm centre in burst-local coordinates
    li_b = li_g_farm - burst_idx * lpb   # row within this burst
    ax.plot(sa_farm * rps / 1000, li_b * aps / 1000,
            "r+", ms=14, lw=2, label=f"GCP farm ctr  row={li_b:.0f}  col={sa_farm:.0f}")
    ax.axhline(li_b * aps / 1000, color="red", lw=0.6, ls="--", alpha=0.5)
    ax.axvline(sa_farm * rps / 1000, color="red", lw=0.6, ls="--", alpha=0.5)

    ax.set_xlabel("Range (km)")
    ax.set_ylabel("Azimuth (km)")
    ax.set_title(f"IW2/VV burst {burst_idx}/{n_bursts-1}  {DATE}  DS×{DS_RG}rg  "
                 f"[{n_rows}×{n_cols} px shown]  vmin={vmin:.1f} vmax={vmax:.1f} dB\n"
                 f"GCP → line_global={li_g_farm:.0f}  burst_row={li_b:.0f}  sample={sa_farm:.0f}")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_xlim(0, w_m/1000); ax.set_ylim(h_m/1000, 0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    log(f"  PNG: {out_path.name}")


def main():
    log(f"p8e_burst_overview.py  {datetime.now(timezone.utc).isoformat()}")

    token = get_token()
    log("token OK")

    items = search_slc(token)
    if not items:
        log("FATAL: no SLC"); sys.exit(1)
    prod = items[0]
    pid  = prod["Id"]
    safe = prod["Name"]
    log(f"product: {safe}")

    xml_node, safe_node = find_iw2_vv_annotation(token, pid, safe)
    if not xml_node:
        log("FATAL: no IW2/VV annotation"); sys.exit(1)
    log(f"annotation: {xml_node}")

    xml_bytes = download_annotation_xml(token, pid, safe_node, xml_node)
    info = parse_annotation(xml_bytes)
    rps = info["rps"]; aps = info["aps"]
    lpb = info["lpb"]; spb = info["spb"]
    n_bursts = info["n_bursts"]
    log(f"rps={rps:.3f}m  aps={aps:.3f}m  lpb={lpb}  spb={spb}  bursts={n_bursts}")

    # GCP prediction for farm centre
    li_g_farm, sa_farm = gcp_predict(info["gcps"], KHARG_LAT, KHARG_LON)
    burst_gcp = int(li_g_farm // lpb)
    log(f"GCP farm ctr: line_global={li_g_farm:.1f}  sample={sa_farm:.1f}  → burst={burst_gcp}")

    # print all GCPs to understand the grid coverage
    lats = [g["lat"] for g in info["gcps"]]
    lons = [g["lon"] for g in info["gcps"]]
    log(f"GCP lat range: [{min(lats):.4f}, {max(lats):.4f}]")
    log(f"GCP lon range: [{min(lons):.4f}, {max(lons):.4f}]")

    tiff_node = xml_node.replace(".xml", ".tiff")

    # process all bursts — find the one containing the island
    for burst_idx in range(n_bursts):
        log(f"\n--- burst {burst_idx} ---")
        b_start, b_end = burst_byte_range(info["root"], burst_idx)
        if b_start is None:
            log("  no byteOffset, skip"); continue

        raw = download_burst(token, pid, safe_node, tiff_node, b_start, b_end)
        if raw is None:
            log("  download failed, skip"); continue

        arr16 = np.frombuffer(raw, dtype=np.int16).reshape(lpb, spb, 2)
        z     = arr16[:, :, 0].astype(np.float32) + 1j * arr16[:, :, 1].astype(np.float32)
        amp   = np.abs(z)
        db    = 20 * np.log10(np.clip(amp, 1.0, None))  # amplitude dB (not power)

        # statistics
        p5, p50, p95 = np.percentile(db[db > 0], [5, 50, 95])
        log(f"  amplitude dB: p5={p5:.1f}  p50={p50:.1f}  p95={p95:.1f}")

        # check dynamic range — Kharg should show ~10 dB sea/land contrast
        # look at left (near range) vs centre columns
        left_strip  = db[:, :200]
        right_strip = db[:, -200:]
        log(f"  left  cols 0-200:   median={np.median(left_strip):.1f} dB")
        log(f"  right cols -200:    median={np.median(right_strip):.1f} dB")

        out_png = OUT_DIR / f"p8e_burst{burst_idx}_{TS}.png"
        plot_burst_overview(db, burst_idx, lpb, spb, rps, aps,
                            li_g_farm, sa_farm, n_bursts, out_png)

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
