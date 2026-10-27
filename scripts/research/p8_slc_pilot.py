"""
p8_slc_pilot.py — Task 1.3: SLC burst pilot for Kharg DESC-35  (rev 2)

Approach:
  1. Search CDSE for S1 SLC product covering Kharg on 2024-10-03
  2. Download annotation XML only (~100 KB) — not the full SAFE
  3. Parse burstList + geolocationGrid → burst covering Kharg lat=29.23
     byteOffset from annotation if present; else parse BigTIFF IFD header
  4. HTTP Range download of just that burst (~150 MB)
  5. Verify: np.frombuffer → complex int16, shape, amplitude stats
  6. xarray-sentinel attempt (requires full .SAFE; notes limitation if unavailable)

Files committed: run_p8_slc_*.txt  (*.tiff, *.SAFE, *.bin in .gitignore)
"""

import os, sys, time, struct, xml.etree.ElementTree as ET
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

CDSE_TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
CDSE_DL_BASE     = "https://download.dataspace.copernicus.eu/odata/v1"

CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

PILOT_DATE = "2024-10-03"
KHARG_LAT  = 29.23
KHARG_LON  = 50.33

OUT_DIR = Path("diagnostics/mega")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG = OUT_DIR / f"run_p8_slc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M')}.txt"
_log_fh = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _log_fh.write(msg + "\n")
    _log_fh.flush()


# ── 1. Token ──────────────────────────────────────────────────────────────────

def cdse_token():
    import requests
    log("=== Step 1: CDSE token ===")
    r = requests.post(
        CDSE_TOKEN_URL,
        data={"grant_type":"client_credentials","client_id":CLIENT_ID,"client_secret":CLIENT_SECRET},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"  FATAL: {r.status_code}: {r.text[:300]}"); sys.exit(1)
    token = r.json()["access_token"]
    log(f"  token OK (len={len(token)})")
    return token


# ── 2. Product search ─────────────────────────────────────────────────────────

def search_slc(token):
    import requests
    log(f"\n=== Step 2: search SLC {PILOT_DATE} ===")
    d0 = PILOT_DATE + "T00:00:00.000Z"
    d1 = (datetime.strptime(PILOT_DATE, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00.000Z"
    filt = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and contains(Name,'SLC') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;POINT({KHARG_LON} {KHARG_LAT})') "
        f"and ContentDate/Start gt {d0} "
        f"and ContentDate/Start lt {d1}"
    )
    r = requests.get(
        f"{CDSE_CATALOG_URL}/Products?$filter={filt}&$top=5",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    items = r.json().get("value", [])
    log(f"  found {len(items)} products")
    for it in items:
        log(f"    {it['Id']}  {it['Name']}  {it.get('ContentLength',0)//1024//1024} MB")
    if not items:
        log("  FATAL: no product"); sys.exit(1)
    return items[0]


# ── 3. Node tree ──────────────────────────────────────────────────────────────

def get_nodes(token, product_id, safe_node):
    import requests
    log(f"\n=== Step 3: node tree ===")

    def list_nodes(path):
        r = requests.get(
            f"{CDSE_DL_BASE}/Products({product_id})/{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if r.status_code != 200:
            log(f"  list_nodes({path!r}) → {r.status_code}: {r.text[:200]}")
            return []
        d = r.json()
        return d.get("value", d.get("result", []))

    annot_nodes = list_nodes(f"Nodes({safe_node})/Nodes(annotation)/Nodes")
    log(f"  annotation/ ({len(annot_nodes)} files):")
    annot_node = None
    for n in annot_nodes:
        sz = n.get("ContentLength", 0) // 1024
        log(f"    {n['Id']}  {sz} KB")
        if "-iw2-slc-vv-" in n["Id"].lower() and n["Id"].endswith(".xml"):
            annot_node = n

    meas_nodes = list_nodes(f"Nodes({safe_node})/Nodes(measurement)/Nodes")
    log(f"  measurement/ ({len(meas_nodes)} files):")
    tiff_node = None
    for n in meas_nodes:
        sz = n.get("ContentLength", 0) // 1024 // 1024
        log(f"    {n['Id']}  {sz} MB")
        if "-iw2-slc-vv-" in n["Id"].lower() and n["Id"].endswith(".tiff"):
            tiff_node = n

    if not annot_node:
        log("  FATAL: no IW2-VV annotation XML"); sys.exit(1)
    if not tiff_node:
        log("  FATAL: no IW2-VV tiff"); sys.exit(1)
    return annot_node, tiff_node


# ── 4. Download annotation XML ────────────────────────────────────────────────

def download_annotation(token, product_id, safe_node, annot_node):
    import requests
    annot_id = annot_node["Id"]
    sz = annot_node.get("ContentLength", 0)
    log(f"\n=== Step 4: download annotation ({sz//1024} KB) ===")
    url = (f"{CDSE_DL_BASE}/Products({product_id})"
           f"/Nodes({safe_node})/Nodes(annotation)/Nodes({annot_id})/$value")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    if r.status_code != 200:
        log(f"  FATAL: {r.status_code}: {r.text[:200]}"); sys.exit(1)
    out = OUT_DIR / annot_id
    out.write_bytes(r.content)
    log(f"  saved {len(r.content)//1024} KB → {out.name}")
    return out


# ── 5. Parse burst covering Kharg ─────────────────────────────────────────────

def find_kharg_burst(xml_path):
    log(f"\n=== Step 5: parse annotation XML ===")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    swath           = root.find('.//swathTiming')
    lines_per_burst = int(swath.find('linesPerBurst').text)
    samp_per_burst  = int(swath.find('samplesPerBurst').text)
    burst_list      = swath.find('burstList')
    n_bursts        = int(burst_list.get('count'))
    log(f"  {n_bursts} bursts × {lines_per_burst} lines × {samp_per_burst} samples")

    # Map latitude → burst via geolocationGridPointList
    gcps = []
    for pt in root.findall('.//geolocationGridPoint'):
        line = int(pt.find('line').text)
        lat  = float(pt.find('latitude').text)
        gcps.append((line, lat))

    if gcps:
        best_line = min(gcps, key=lambda p: abs(p[1] - KHARG_LAT))[0]
        burst_idx = min(best_line // lines_per_burst, n_bursts - 1)
        log(f"  GCP nearest lat={KHARG_LAT}: line={best_line} → burst_idx={burst_idx}")
    else:
        burst_idx = n_bursts // 2
        log(f"  no GCPs — middle burst {burst_idx}")

    bursts_elems = list(burst_list)
    b = bursts_elems[burst_idx]
    az_time = (b.find('azimuthTime').text if b.find('azimuthTime') is not None else "?")
    byte_offset_elem = b.find('byteOffset')
    byte_offset = int(byte_offset_elem.text) if byte_offset_elem is not None else None

    burst_line_start = burst_idx * lines_per_burst
    burst_line_end   = burst_line_start + lines_per_burst - 1
    burst_bytes      = lines_per_burst * samp_per_burst * 4  # complex int16 = 4 bytes/px

    log(f"  burst {burst_idx}: lines {burst_line_start}–{burst_line_end}")
    log(f"  azimuthTime: {az_time}")
    log(f"  byteOffset from annotation: {'N/A' if byte_offset is None else byte_offset}")
    log(f"  burst size: {burst_bytes//1024//1024} MB  ({burst_bytes} bytes)")

    return {
        'index':            burst_idx,
        'line_start':       burst_line_start,
        'line_end':         burst_line_end,
        'lines_per_burst':  lines_per_burst,
        'samples_per_burst':samp_per_burst,
        'n_bursts':         n_bursts,
        'byte_offset':      byte_offset,
        'burst_bytes':      burst_bytes,
        'az_time':          az_time,
    }


# ── 6. Get byte range (annotation byteOffset or BigTIFF IFD) ─────────────────

def _parse_bigtiff_strip_offsets(data, burst_info):
    """Parse BigTIFF IFD from header bytes; return (byte_start, byte_end) for burst."""
    e = '<' if data[:2] == b'II' else '>'
    magic = struct.unpack_from(e+'H', data, 2)[0]
    if magic == 43:
        ifd0 = struct.unpack_from(e+'Q', data, 8)[0]
        n_entries = struct.unpack_from(e+'Q', data, ifd0)[0]
        entry_size = 20
        base_offset = ifd0 + 8
    elif magic == 42:
        ifd0 = struct.unpack_from(e+'I', data, 4)[0]
        n_entries = struct.unpack_from(e+'H', data, ifd0)[0]
        entry_size = 12
        base_offset = ifd0 + 2
    else:
        raise ValueError(f"unknown TIFF magic {magic}")

    log(f"  magic={magic} ({'BigTIFF' if magic==43 else 'TIFF'}), ifd@{ifd0}, {n_entries} entries")

    type_sz = {1:1,2:1,3:2,4:4,5:8,6:1,8:2,9:4,11:4,12:8,16:8,17:8,18:8}

    strip_offsets = None
    strip_counts  = None
    rows_per_strip = 1

    for i in range(n_entries):
        pos = base_offset + i * entry_size
        if magic == 43:
            tag, typ, cnt = struct.unpack_from(e+'HHQ', data, pos)
            val_raw = data[pos+12:pos+20]
            tsz = type_sz.get(typ, 1)
            inline = cnt * tsz <= 8
        else:
            tag, typ, cnt = struct.unpack_from(e+'HHI', data, pos)
            val_raw = data[pos+8:pos+12]
            tsz = type_sz.get(typ, 1)
            inline = cnt * tsz <= 4

        if inline:
            if typ in (16,18): v = struct.unpack_from(e+'Q', val_raw)[0]
            elif typ in (4,13): v = struct.unpack_from(e+'I', val_raw)[0]
            elif typ in (3,8):  v = struct.unpack_from(e+'H', val_raw)[0]
            else: v = None
        else:
            off_sz = 8 if magic == 43 else 4
            off = struct.unpack_from(e+('Q' if off_sz==8 else 'I'), val_raw)[0]
            end = off + cnt * tsz
            if end > len(data):
                log(f"  tag {tag}: data @{off}+{cnt*tsz} exceeds buffer {len(data)}")
                v = None
            else:
                fmt_char = {16:'Q',17:'q',18:'Q',4:'I',9:'i',3:'H',8:'h'}.get(typ,'B')
                v = struct.unpack(e + fmt_char * cnt, data[off:end])

        if tag == 273:   strip_offsets  = v
        elif tag == 279: strip_counts   = v
        elif tag == 278: rows_per_strip = v if isinstance(v, int) else 1

    if strip_offsets is None or strip_counts is None:
        return None

    log(f"  rows_per_strip={rows_per_strip}, n_strips={len(strip_offsets)}")

    ls = burst_info['line_start']
    le = burst_info['line_end']
    s_start = ls // rows_per_strip
    s_end   = min(le // rows_per_strip, len(strip_offsets) - 1)

    byte_start = strip_offsets[s_start]
    byte_end   = strip_offsets[s_end] + strip_counts[s_end] - 1
    log(f"  strips {s_start}–{s_end}: bytes {byte_start}–{byte_end} ({(byte_end-byte_start)//1024//1024} MB)")
    return byte_start, byte_end


def get_burst_byte_range(token, tiff_url, burst_info):
    import requests
    log(f"\n=== Step 6: byte range for burst ===")

    # Priority 1: byteOffset from annotation XML
    if burst_info['byte_offset'] is not None:
        bo = burst_info['byte_offset']
        be = bo + burst_info['burst_bytes'] - 1
        log(f"  source: annotation byteOffset={bo}, range bytes={bo}-{be} ({burst_info['burst_bytes']//1024//1024} MB)")
        return bo, be

    # Priority 2: parse BigTIFF IFD via Range header
    log("  no byteOffset in annotation — fetching TIFF header (512 KB)")
    r = requests.get(
        tiff_url,
        headers={"Authorization": f"Bearer {token}", "Range": "bytes=0-524287"},
        timeout=120,
    )
    log(f"  header Range status: {r.status_code}, got {len(r.content)//1024} KB")
    if r.status_code not in (200, 206):
        log("  Range not supported by server")
        return None

    try:
        result = _parse_bigtiff_strip_offsets(r.content, burst_info)
        if result is None:
            # Try larger header (2 MB)
            log("  strip offsets not in 512 KB — fetching 2 MB header")
            r2 = requests.get(
                tiff_url,
                headers={"Authorization": f"Bearer {token}", "Range": "bytes=0-2097151"},
                timeout=180,
            )
            log(f"  2 MB Range status: {r2.status_code}, got {len(r2.content)//1024} KB")
            if r2.status_code in (200, 206):
                result = _parse_bigtiff_strip_offsets(r2.content, burst_info)
        return result
    except Exception as ex:
        log(f"  IFD parse error: {ex}")
        return None


# ── 7. Range-download burst ───────────────────────────────────────────────────

def download_burst(token, tiff_url, byte_start, byte_end, burst_info):
    import requests
    sz_mb = (byte_end - byte_start + 1) / 1024 / 1024
    log(f"\n=== Step 7: download burst via Range ({sz_mb:.0f} MB) ===")
    log(f"  Range: bytes={byte_start}-{byte_end}")

    out_path = OUT_DIR / f"burst_{burst_info['index']}_iw2_vv.bin"
    t0 = time.time()
    total = 0
    chunks = []

    with requests.get(
        tiff_url,
        headers={"Authorization": f"Bearer {token}", "Range": f"bytes={byte_start}-{byte_end}"},
        stream=True, timeout=600,
    ) as r:
        if r.status_code not in (200, 206):
            log(f"  FATAL: {r.status_code}: {r.text[:200]}")
            return None, None
        with open(out_path, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=1024*1024):
                fh.write(chunk)
                chunks.append(chunk)
                total += len(chunk)
                if total % (10*1024*1024) < 1024*1024:
                    log(f"  ... {total//1024//1024} MB")

    elapsed = time.time() - t0
    actual_mb = out_path.stat().st_size / 1024 / 1024
    log(f"  done: {actual_mb:.1f} MB in {elapsed:.1f}s ({actual_mb/elapsed:.1f} MB/s)")
    return out_path, b''.join(chunks)


# ── 8. Verify complex array ───────────────────────────────────────────────────

def verify_burst_complex(burst_data, burst_info):
    log(f"\n=== Step 8: verify complex int16 ===")
    L = burst_info['lines_per_burst']
    S = burst_info['samples_per_burst']
    expected = L * S * 4

    got = len(burst_data)
    log(f"  expected {expected} bytes ({L}×{S}×4),  got {got} bytes")

    if got != expected:
        log(f"  WARNING: size mismatch ({got} vs {expected})")
        n = got // 4
        arr16 = np.frombuffer(burst_data[:n*4], dtype=np.int16).reshape(n, 2)
        log(f"  int16 shape: ({n}, 2) — partial burst")
        return

    arr16 = np.frombuffer(burst_data, dtype=np.int16).reshape(L, S, 2)
    z     = arr16[:, :, 0].astype(np.float32) + 1j * arr16[:, :, 1].astype(np.float32)
    amp   = np.abs(z)

    log(f"  complex shape: {z.shape}  dtype={z.dtype}  ✓")
    log(f"  amplitude:  min={amp.min():.2f}  max={amp.max():.2f}  mean={amp.mean():.2f}  std={amp.std():.2f}")
    log(f"  non-zero px: {np.count_nonzero(amp)}/{amp.size} ({100*np.count_nonzero(amp)/amp.size:.1f}%)")


# ── 9. xarray-sentinel ────────────────────────────────────────────────────────

def try_xarray_sentinel(safe_dir, tiff_local_path, annot_path):
    log(f"\n=== Step 9: xarray-sentinel (group='IW2/VV') ===")
    try:
        import xarray as xr
        import xarray_sentinel  # noqa
    except ImportError as e:
        log(f"  import failed: {e}"); return

    if not tiff_local_path.exists():
        log("  full subswath tiff not downloaded — xarray-sentinel requires full .SAFE measurement file")
        log("  annotation + burst-bytes verified above via numpy; SAFE read skipped")
        return

    # Build minimal SAFE structure
    (safe_dir / "annotation").mkdir(parents=True, exist_ok=True)
    (safe_dir / "measurement").mkdir(parents=True, exist_ok=True)
    import shutil
    dst_a = safe_dir / "annotation" / annot_path.name
    dst_t = safe_dir / "measurement" / tiff_local_path.name
    if not dst_a.exists():
        shutil.copy2(annot_path, dst_a)
    if not dst_t.exists():
        dst_t.symlink_to(tiff_local_path.resolve())

    try:
        import xarray as xr
        ds = xr.open_dataset(str(safe_dir), engine="sentinel-1", group="IW2/VV")
        log(f"  dataset opened OK")
        log(f"  dims: {dict(ds.dims)}")
        for vname, var in ds.data_vars.items():
            log(f"  var '{vname}': shape={var.shape} dtype={var.dtype}")
        log(f"  CRS: {ds.attrs.get('crs', ds.attrs.get('spatial_ref', 'N/A'))}")
    except Exception as e:
        log(f"  xarray-sentinel failed: {e}")
        log("  (requires complete .SAFE — annotation + measurement + manifest.safe etc.)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log(f"p8_slc_pilot.py rev2  {datetime.now(timezone.utc).isoformat()}")
    log(f"pilot: {PILOT_DATE}  Kharg: POINT({KHARG_LON} {KHARG_LAT})")

    token   = cdse_token()
    product = search_slc(token)

    prod_id   = product["Id"]
    safe_name = product["Name"].replace(".SAFE", "")
    safe_node = safe_name + ".SAFE"

    annot_node, tiff_node = get_nodes(token, prod_id, safe_node)

    annot_path = download_annotation(token, prod_id, safe_node, annot_node)
    burst_info = find_kharg_burst(annot_path)

    tiff_id  = tiff_node["Id"]
    tiff_url = (f"{CDSE_DL_BASE}/Products({prod_id})"
                f"/Nodes({safe_node})/Nodes(measurement)/Nodes({tiff_id})/$value")

    byte_range = get_burst_byte_range(token, tiff_url, burst_info)

    if byte_range is None:
        log("\n=== Range header not available — full tiff required (1.3 GB) ===")
        log("  Annotation parsed OK, burst metadata valid.")
        log("  To proceed: set DOWNLOAD_FULL=1 env var and re-run.")
    else:
        byte_start, byte_end = byte_range
        burst_path, burst_data = download_burst(token, tiff_url, byte_start, byte_end, burst_info)
        if burst_data:
            verify_burst_complex(burst_data, burst_info)
        safe_dir = OUT_DIR / (safe_name + ".SAFE")
        tiff_local = OUT_DIR / tiff_id  # won't exist (not downloaded whole)
        try_xarray_sentinel(safe_dir, tiff_local, annot_path)

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _log_fh.close()


if __name__ == "__main__":
    main()
