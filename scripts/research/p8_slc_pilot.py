"""
p8_slc_pilot.py — Task 1.3: SLC burst pilot for Kharg DESC-35

Pilot: one date 2024-10-03
Goal:  print CDSE OData node tree, file size, download time,
       confirm xarray-sentinel can read the burst.

Method:
  1. CDSE token via client_credentials
  2. OData product search with $filter
  3. Node tree traversal → identify IW subswath + burst covering Kharg
  4. Download one burst tiff (~30-80 MB)
  5. Read burst with xarray-sentinel, print shape/CRS

Secrets: CLIENT_ID, CLIENT_SECRET (GHA secrets, CDSE credentials)
"""

import io, json, math, os, sys, time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

CDSE_TOKEN_URL    = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG_URL  = "https://catalogue.dataspace.copernicus.eu/odata/v1"
CDSE_DL_BASE      = "https://download.dataspace.copernicus.eu/odata/v1"

CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

PILOT_DATE = "2024-10-03"

# Kharg Island centre
KHARG_LON = 50.33
KHARG_LAT = 29.23

OUT_DIR = Path("diagnostics/mega")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG = OUT_DIR / f"run_p8_slc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M')}.txt"

_log_fh = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _log_fh.write(msg + "\n")
    _log_fh.flush()


# ── 1. Token ────────────────────────────────────────────────────────────────

def cdse_token():
    import requests
    log("=== Step 1: CDSE token ===")
    r = requests.post(
        CDSE_TOKEN_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )
    if r.status_code != 200:
        log(f"  FATAL: token request failed {r.status_code}: {r.text[:400]}")
        sys.exit(1)
    token = r.json()["access_token"]
    log(f"  token OK (len={len(token)})")
    return token


# ── 2. Product search ────────────────────────────────────────────────────────

def search_slc(token, date_str):
    import requests
    log(f"\n=== Step 2: OData search SLC {date_str} ===")
    d0 = date_str + "T00:00:00.000Z"
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00.000Z"

    filt = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and contains(Name,'SLC') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;POINT({KHARG_LON} {KHARG_LAT})') "
        f"and ContentDate/Start gt {d0} "
        f"and ContentDate/Start lt {d1}"
    )

    url = f"{CDSE_CATALOG_URL}/Products?$filter={filt}&$orderby=ContentDate/Start desc&$top=5"
    log(f"  URL: {url}")

    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        log(f"  FATAL: search failed {r.status_code}: {r.text[:400]}")
        sys.exit(1)

    items = r.json().get("value", [])
    log(f"  products found: {len(items)}")
    for it in items:
        log(f"    id={it['Id']}  name={it['Name']}  size={it.get('ContentLength',0)//1024//1024} MB")

    if not items:
        log("  FATAL: no SLC product for this date/location")
        sys.exit(1)

    return items[0]


# ── 3. Node tree ─────────────────────────────────────────────────────────────

def get_node_tree(token, product_id, safe_name):
    import requests
    log(f"\n=== Step 3: node tree ===")

    # suffix is the part after Products(<id>)/ e.g. "" or "Nodes(X.SAFE)" or "Nodes(X.SAFE)/Nodes(measurement)"
    def list_nodes(suffix=""):
        if suffix:
            url = f"{CDSE_DL_BASE}/Products({product_id})/{suffix}"
        else:
            url = f"{CDSE_DL_BASE}/Products({product_id})/Nodes"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if r.status_code != 200:
            log(f"    list_nodes({suffix!r}) failed {r.status_code}: {r.text[:300]}")
            return []
        data = r.json()
        return data.get("result", data.get("value", []))

    # top level
    root = list_nodes()
    log(f"  root nodes: {[n['Id'] for n in root]}")

    # SAFE dir
    safe_node = safe_name + ".SAFE"
    nodes_l1 = list_nodes(f"Nodes({safe_node})")
    log(f"  {safe_node}/ nodes: {[n['Id'] for n in nodes_l1]}")

    # measurement dir
    nodes_meas = list_nodes(f"Nodes({safe_node})/Nodes(measurement)")
    log(f"  measurement/ nodes ({len(nodes_meas)}):")
    tiff_files = []
    for n in nodes_meas:
        log(f"    {n['Id']}  size={n.get('ContentLength',0)//1024//1024} MB")
        if n["Id"].endswith(".tiff"):
            tiff_files.append(n)

    return safe_node, tiff_files


# ── 4. Pick subswath burst covering Kharg ────────────────────────────────────

def pick_burst(tiff_files):
    """
    IW SLC tiffs are named: s1a-iw<N>-slc-vv-*.tiff
    Pick IW subswath with best overlap for Kharg lat=29.23.
    Heuristic: DESC-35 → IW2 typically covers the Gulf coast.
    Try IW2 first, fall back to IW1/IW3.
    """
    log(f"\n=== Step 4: pick subswath burst ===")
    iw_order = ["iw2", "iw1", "iw3"]
    for iw in iw_order:
        for t in tiff_files:
            if f"-{iw}-slc-vv-" in t["Id"].lower():
                log(f"  selected: {t['Id']}  size={t.get('ContentLength',0)//1024//1024} MB")
                return t
    # fallback: first vv tiff
    for t in tiff_files:
        if "-vv-" in t["Id"].lower():
            log(f"  fallback selected: {t['Id']}")
            return t
    log("  FATAL: no VV tiff found in measurement/")
    sys.exit(1)


# ── 5. Download burst tiff ────────────────────────────────────────────────────

def download_tiff(token, product_id, safe_node, tiff_node):
    import requests
    tiff_id = tiff_node["Id"]
    size_mb  = tiff_node.get("ContentLength", 0) / 1024 / 1024
    log(f"\n=== Step 5: download {tiff_id} ({size_mb:.1f} MB) ===")

    url = f"{CDSE_DL_BASE}/Products({product_id})/Nodes({safe_node})/Nodes(measurement)/Nodes({tiff_id})/$value"
    log(f"  URL: {url}")

    out_path = OUT_DIR / tiff_id
    t0 = time.time()
    with requests.get(url, headers={"Authorization": f"Bearer {token}"},
                      stream=True, timeout=600) as r:
        if r.status_code != 200:
            log(f"  FATAL: download failed {r.status_code}: {r.text[:200]}")
            sys.exit(1)
        total = 0
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024*1024):
                fh.write(chunk)
                total += len(chunk)
                if total % (10*1024*1024) < 1024*1024:
                    log(f"  ... {total//1024//1024} MB downloaded")

    elapsed = time.time() - t0
    actual_mb = out_path.stat().st_size / 1024 / 1024
    log(f"  done: {actual_mb:.1f} MB in {elapsed:.1f}s ({actual_mb/elapsed:.1f} MB/s)")
    return out_path


# ── 6. Read burst with xarray-sentinel ───────────────────────────────────────

def read_burst(tiff_path):
    log(f"\n=== Step 6: xarray-sentinel read ===")
    try:
        import xarray as xr
        import xarray_sentinel  # noqa — registers the sentinel engine
        log(f"  xarray-sentinel imported OK")
    except ImportError as e:
        log(f"  FATAL: import error: {e}")
        sys.exit(1)

    try:
        ds = xr.open_dataset(tiff_path, engine="sentinel-1")
        log(f"  dataset: {ds}")
        log(f"  dims: {dict(ds.dims)}")
        log(f"  coords: {list(ds.coords)}")
        log(f"  CRS: {ds.attrs.get('crs', ds.attrs.get('spatial_ref', 'N/A'))}")
    except Exception as e:
        log(f"  xarray-sentinel open failed: {e}")
        log(f"  trying rasterio fallback...")
        try:
            import rasterio
            with rasterio.open(tiff_path) as src:
                log(f"  rasterio: shape={src.height}×{src.width} bands={src.count} CRS={src.crs}")
        except Exception as e2:
            log(f"  rasterio also failed: {e2}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    log(f"p8_slc_pilot.py  start {datetime.now(timezone.utc).isoformat()}")
    log(f"pilot date: {PILOT_DATE}  location: POINT({KHARG_LON} {KHARG_LAT})")

    token   = cdse_token()
    product = search_slc(token, PILOT_DATE)

    prod_id   = product["Id"]
    safe_name = product["Name"].replace(".SAFE", "")  # e.g. S1A_IW_SLC__...

    safe_node, tiff_files = get_node_tree(token, prod_id, safe_name)
    tiff_node = pick_burst(tiff_files)

    tiff_path = download_tiff(token, prod_id, safe_node, tiff_node)
    read_burst(tiff_path)

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _log_fh.close()


if __name__ == "__main__":
    main()
