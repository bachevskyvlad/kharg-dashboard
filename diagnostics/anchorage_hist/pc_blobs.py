"""ТЗ: blob patches in Supabase + FWHM length. Scenes are read ONCE; any later length
formula works from the table (anchorage_blobs), never from scenes again.

Source: Microsoft Planetary Computer STAC, collection sentinel-1-rtc, asset 'vv'
(signed with planetary_computer.sign_inplace), warped (rasterio WarpedVRT) onto the
v2 frame grid. 0 Sentinel Hub PU. Detection = ТЗ-12.2/v2.1 unchanged (tz12_2_test.detect).

Modes:
  --probe            step 0 (table schema + select) and step 1 (port check on 2026-11-05 and
                     2024-05-12 vs v2.1 daily.csv/ships.csv), both warp modes
  --run              step 2: all dates from dates.csv, resumable by anchorage_blobs dates;
                     per blob: patch bbox+8 px, uint8=round(norm*255), base64 -> upsert
  --fwhm             steps 3–4: FWHM length from the table + acceptance; step 5 only if passed
"""
import os, sys, io, csv, json, time, base64, argparse
from datetime import datetime

import numpy as np
import requests
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'diagnostics', 'anchorage_test'))
import tz12_2_test as t

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
SB_URL = os.environ.get('SUPABASE_URL', 'https://yoekhkczwdeyauwarsyy.supabase.co')
SB_KEY = os.environ.get('SUPABASE_WRITE_KEY', '')
H = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}', 'Content-Type': 'application/json'}
TABLE = 'anchorage_blobs'
PAD = 8
RUN_ID = os.environ.get('RUN_ID') or datetime.utcnow().strftime('local-%Y%m%dT%H%M%SZ')
MODE = os.environ.get('PC_WARP', 'frame')        # 'frame' = warp straight to 4.17 m grid; '10m' = 10 m + bilinear upsample (as v2.1)
LOG = os.path.join(HERE, 'logs', 'pc_blobs.txt')


def log(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a') as f:
        f.write(s + '\n')


# ── Supabase helpers ─────────────────────────────────────────────────────────
CANDIDATE_COLS = ['date', 'x0', 'y0', 'x1', 'y1', 'w', 'h', 'width', 'height', 'patch_x0', 'patch_y0',
                  'patch_w', 'patch_h', 'patch', 'patch_b64', 'patch_png_b64', 'peak_norm', 'sea_p70',
                  'threshold', 'area_px', 'length_bbox_m', 'length_axis_m', 'class_v21', 'lat', 'lon',
                  'res_m', 'pad_px', 'source', 'scene_ids', 'run_id', 'created_at', 'id']


def table_columns():
    """The publishable key cannot read the OpenAPI schema, so probe columns one by one:
    select=<col>&limit=0 -> 200 if the column exists, 400 (42703) if not, 404 if no table."""
    cols = []
    for c in CANDIDATE_COLS:
        r = requests.get(f'{SB_URL}/rest/v1/{TABLE}?select={c}&limit=0', headers=H, timeout=30)
        if r.status_code == 404:
            return None, []
        if r.status_code == 200:
            cols.append(c)
    return cols, []


def blob_dates():
    out, off = set(), 0
    while True:
        r = requests.get(f'{SB_URL}/rest/v1/{TABLE}?select=date&order=date&limit=1000&offset={off}', headers=H, timeout=60)
        r.raise_for_status(); rows = r.json()
        out |= {x['date'] for x in rows}
        if len(rows) < 1000:
            return out
        off += 1000


# ── Planetary Computer read ──────────────────────────────────────────────────
def pc_items(d):
    import pystac_client, planetary_computer
    cat = pystac_client.Client.open(STAC, modifier=planetary_computer.sign_inplace)
    s = cat.search(collections=['sentinel-1-rtc'], bbox=[t.LON0, t.LAT0, t.LON1, t.LAT1],
                   datetime=f'{d}T00:00:00Z/{d}T23:59:59Z')
    items = [i for i in s.items() if i.properties.get('sat:relative_orbit') == t.REL_ORBIT]
    items.sort(key=lambda i: i.properties.get('datetime', ''), reverse=True)   # most recent first
    return items


def pc_norm(d):
    """Return norm array on the frame grid (NaN = no data) and the item ids."""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    items = pc_items(d)
    if not items:
        return None, []
    if MODE == '10m':
        from sentinelhub import BBox, CRS, bbox_to_dimensions
        gw, gh = bbox_to_dimensions(BBox([t.LON0, t.LAT0, t.LON1, t.LAT1], crs=CRS.WGS84), resolution=10)
    else:
        gw, gh = t.W, t.H
    tr = from_bounds(t.LON0, t.LAT0, t.LON1, t.LAT1, gw, gh)
    out = np.full((gh, gw), np.nan, dtype='float32')
    for it in items:
        with rasterio.open(it.assets['vv'].href) as src:
            with WarpedVRT(src, crs='EPSG:4326', transform=tr, width=gw, height=gh,
                           resampling=Resampling.bilinear, src_nodata=src.nodata, nodata=np.nan) as vrt:
                a = vrt.read(1).astype('float32')
        a[~np.isfinite(a) | (a <= 0)] = np.nan
        fill = np.isnan(out) & np.isfinite(a)
        out[fill] = a[fill]
    db = 10 * np.log10(out + 1e-10)
    norm = np.clip((db + 25) / 40, 0, 1).astype('float32')
    norm[~np.isfinite(out)] = np.nan
    if MODE == '10m':
        norm = t.upsample(norm)
    return norm, [i.id for i in items]


def sea_p70(arr, mask):
    a = arr.copy(); a[~np.isfinite(a)] = 0; a[mask] = 0
    v = a[a > 0.01]
    return float(np.percentile(v, 70)) if len(v) else None


# ── patches ──────────────────────────────────────────────────────────────────
def encode_patch(arr, box):
    x0, y0, x1, y1 = box
    px0, py0 = max(x0 - PAD, 0), max(y0 - PAD, 0)
    px1, py1 = min(x1 + PAD + 1, t.W), min(y1 + PAD + 1, t.H)
    p = np.nan_to_num(arr[py0:py1, px0:px1], nan=0.0)
    u8 = np.round(np.clip(p, 0, 1) * 255).astype('uint8')
    buf = io.BytesIO(); Image.fromarray(u8, 'L').save(buf, format='PNG')   # lossless, self-describing dims
    return base64.b64encode(buf.getvalue()).decode(), (px0, py0, px1 - px0, py1 - py0)


def decode_patch(b64):
    raw = base64.b64decode(b64)
    return np.array(Image.open(io.BytesIO(raw)), dtype='float32') / 255.0


# ── step 0 + 1 ───────────────────────────────────────────────────────────────
def probe():
    cols, req = table_columns()
    log(f'STEP0 {TABLE}: columns={cols} required={req}')
    r = requests.get(f'{SB_URL}/rest/v1/{TABLE}?select=*&limit=1', headers=H, timeout=60)
    log(f'STEP0 select: http={r.status_code} body={r.text[:300]}')
    t.set_scale(0)
    gj, mask = t.load_masks()
    ref_daily = {r['date']: r for r in csv.DictReader(open(os.path.join(HERE, 'daily.csv')))}
    ref_ships = {}
    for r in csv.DictReader(open(os.path.join(HERE, 'ships.csv'))):
        ref_ships.setdefault(r['date'], []).append((float(r['lat']), float(r['lon'])))
    px_deg = (t.LON1 - t.LON0) / t.W
    global MODE
    for mode in ('frame', '10m'):
        MODE = mode
        for d in ('2026-11-05', '2024-05-12'):
            t0 = time.time()
            arr, ids = pc_norm(d)
            if arr is None:
                log(f'STEP1 [{mode}] {d}: no PC item for orbit {t.REL_ORBIT}'); continue
            det, counts, thr = t.detect(arr, mask)
            tot = sum(counts.values())
            ref = ref_daily.get(d, {})
            # positions: nearest ref ship per detection, in px
            rs = ref_ships.get(d, [])
            dists = []
            for s in det:
                if rs:
                    dists.append(min(np.hypot((s['lat'] - a) / px_deg, (s['lon'] - b) / px_deg) for a, b in rs))
            within1 = sum(1 for x in dists if x <= 1.0)
            log(f'STEP1 [{mode}] {d}: items={ids} valid={np.mean(np.isfinite(arr)):.4f} thr={thr:.4f} '
                f'total={tot} (v2.1 {ref.get("total")}) no_small={tot - counts["small"]} (v2.1 {ref.get("total_no_small")}) '
                f'TOTAL {"MATCH" if str(tot) == str(ref.get("total")) else "DIFF"}; positions within 1 px: {within1}/{len(det)} '
                f'(median offset {np.median(dists) if dists else float("nan"):.2f} px) {time.time() - t0:.0f}s')


# ── step 2 ───────────────────────────────────────────────────────────────────
def run():
    cols, _ = table_columns()
    if not cols:
        log(f'STEP2 FAIL: table {TABLE} not visible'); sys.exit(2)
    t.set_scale(0)
    gj, mask = t.load_masks()
    dates = [r['date'] for r in csv.DictReader(open(os.path.join(HERE, 'dates.csv')))]
    done = blob_dates()
    todo = [d for d in dates if d not in done]
    log(f'STEP2 mode={MODE} dates={len(dates)} already in {TABLE}={len(done)} to do={len(todo)} columns={cols}')
    t0 = time.time(); nb = 0; holes = []
    for k, d in enumerate(todo, 1):
        arr, ids = pc_norm(d)
        if arr is None or np.mean(np.isfinite(arr)) < t.MIN_VALID_FRAC:
            holes.append(d); log(f'  [{len(done) + k}/{len(dates)}] {d}: HOLE (items={ids})'); continue
        det, counts, thr = t.detect(arr, mask)
        p70 = sea_p70(arr, mask)
        rows = []
        for s in det:
            x0, y0, x1, y1 = s['_box']
            b64, (px0, py0, pw, ph) = encode_patch(arr, s['_box'])
            full = {'date': d, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                    'patch_x0': px0, 'patch_y0': py0, 'patch_w': pw, 'patch_h': ph, 'w': pw, 'h': ph, 'width': pw, 'height': ph,
                    'patch': b64, 'patch_b64': b64, 'patch_png_b64': b64,
                    'peak_norm': round(float(np.nanmax(arr[y0:y1 + 1, x0:x1 + 1])), 4),
                    'sea_p70': None if p70 is None else round(p70, 4), 'threshold': round(float(thr), 4),
                    'area_px': s['area_px'], 'length_bbox_m': int(round((max(x1 - x0, y1 - y0) + 1) * t.PX_M)),
                    'length_axis_m': s['length_m'], 'class_v21': s['class'], 'lat': s['lat'], 'lon': s['lon'],
                    'res_m': t.PX_M, 'pad_px': PAD, 'source': 'pc_sentinel-1-rtc', 'scene_ids': ';'.join(ids),
                    'run_id': RUN_ID}
            rows.append({c: v for c, v in full.items() if c in cols})
        r = requests.delete(f'{SB_URL}/rest/v1/{TABLE}?date=eq.{d}', headers=H, timeout=60)
        if r.status_code >= 300:
            log(f'STEP2 delete {d}: {r.status_code} {r.text[:200]}'); sys.exit(2)
        for i in range(0, len(rows), 200):
            r = requests.post(f'{SB_URL}/rest/v1/{TABLE}?on_conflict=date,x0,y0', json=rows[i:i + 200],
                              headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=120)
            if r.status_code >= 300:
                log(f'STEP2 upsert {d}: {r.status_code} {r.text[:300]}'); sys.exit(2)
        nb += len(rows)
        log(f'  [{len(done) + k}/{len(dates)}] {d}: blobs={len(rows)} total={sum(counts.values())} thr={thr:.3f} '
            f'p70={p70:.3f} items={len(ids)} elapsed={(time.time() - t0) / 60:.1f} min')
    log(f'STEP2 DONE: dates processed={len(todo) - len(holes)} blobs={nb} holes={holes} minutes={(time.time() - t0) / 60:.1f} PU=0')


# ── step 3 + 4 (+5 gated) ────────────────────────────────────────────────────
def fwhm_length(patch, level):
    from scipy import ndimage
    py, px = np.unravel_index(np.argmax(patch), patch.shape)
    peak = patch[py, px]
    lab, _ = ndimage.label(patch >= level * peak, structure=np.ones((3, 3)))
    comp = lab == lab[py, px]
    ys, xs = np.nonzero(comp)
    if len(xs) < 3:
        return float(len(xs)) * t.PX_M
    c = np.stack([xs - xs.mean(), ys - ys.mean()])
    w, v = np.linalg.eigh(np.cov(c))
    pr = v[:, np.argmax(w)] @ c
    return float(pr.max() - pr.min() + 1) * t.PX_M


def fwhm():
    t.set_scale(0)
    rows, off = [], 0
    while True:
        r = requests.get(f'{SB_URL}/rest/v1/{TABLE}?select=*&order=date&limit=500&offset={off}', headers=H, timeout=120)
        r.raise_for_status(); part = r.json(); rows += part
        if len(part) < 500:
            break
        off += 500
    pcol = next((c for c in ('patch', 'patch_b64', 'patch_png_b64') if rows and c in rows[0]), None)
    log(f'STEP3 read {len(rows)} blobs from {TABLE}, patch column={pcol}')
    for r in rows:
        p = decode_patch(r[pcol])
        r['L50'] = fwhm_length(p, 0.5); r['L65'] = fwhm_length(p, 0.65)
        r['Lmed'] = float(np.median([r['L50'], r['L65']]))
    with open(os.path.join(HERE, 'blobs_fwhm.csv'), 'w', newline='') as f:
        keys = ['date', 'x0', 'y0', 'lat', 'lon', 'peak_norm', 'area_px', 'length_bbox_m', 'L50', 'L65', 'Lmed']
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    cls3 = lambda L: 'large' if L >= 250 else 'med' if L >= 100 else 'small'
    results = {}
    for key in ('L50', 'L65', 'Lmed'):
        L = np.array([r[key] for r in rows])
        big = L[L >= 250]
        h, e = np.histogram(big, bins=np.arange(250, 510, 10))
        mode = float(e[np.argmax(h)] + 5) if len(big) else float('nan')
        # (б) pair 2026-10-30 / 2026-11-05, nearest within 200 m
        a = [r for r in rows if r['date'] == '2026-10-30']; b = [r for r in rows if r['date'] == '2026-11-05']
        used, flips, bflips, matched = set(), 0, 0, 0
        for ra in a:
            best = None
            for j, rb in enumerate(b):
                if j in used or 'lat' not in ra:
                    continue
                dm = np.hypot((ra['lat'] - rb['lat']) * 110574, (ra['lon'] - rb['lon']) * 96900)
                if dm <= 200 and (best is None or dm < best[0]):
                    best = (dm, j)
            if best:
                used.add(best[1]); matched += 1
                la, lb = ra[key], b[best[1]][key]
                if cls3(la) != cls3(lb):
                    flips += 1
                    if min(abs(x - 250) for x in (la, lb)) <= 25 or min(abs(x - 100) for x in (la, lb)) <= 25:
                        bflips += 1
        b_ok = flips <= 2 and flips == bflips
        c_ok = 330 <= mode <= 350
        results[key] = (mode, matched, flips, bflips, b_ok, c_ok)
        log(f'STEP4 [{key}] (в) mode of large = {mode:.0f} m -> {"PASS" if c_ok else "FAIL"}; '
            f'(б) matched {matched} ships 30.10/05.11, class flips {flips} (on boundary {bflips}) -> {"PASS" if b_ok else "FAIL"}')
    et = os.path.join(ROOT, 'diagnostics', 'anchorage_cal', 'sar_vs_optical_lengths.csv')
    if not os.path.exists(et):
        log(f'STEP4 (а) NOT RUN: reference {et} is not in the repo yet. Acceptance incomplete -> no class upsert (step 5 blocked).')
        return
    ref = list(csv.DictReader(open(et)))
    log(f'STEP4 (а) reference rows={len(ref)} columns={list(ref[0].keys()) if ref else []}')
    lk = next((k for k in ('L_opt', 'l_opt', 'length_opt_m', 'optical_length_m', 'L_opt_m') if ref and k in ref[0]), None)
    if not lk or 'lat' not in ref[0]:
        log('STEP4 (а) cannot parse reference (need date, lat, lon, L_opt). Step 5 blocked.'); return
    for key in ('L50', 'L65', 'Lmed'):
        errs = []
        for e in ref:
            cand = [r for r in rows if r['date'] == e['date'] and 'lat' in r]
            if not cand:
                continue
            r = min(cand, key=lambda r: np.hypot((r['lat'] - float(e['lat'])) * 110574, (r['lon'] - float(e['lon'])) * 96900))
            errs.append(r[key] - float(e[lk]))
        mae = float(np.mean(np.abs(errs))) if errs else float('nan')
        mode, matched, flips, bflips, b_ok, c_ok = results[key]
        ok = mae <= 30 and b_ok and c_ok
        log(f'STEP4 [{key}] (а) MAE={mae:.1f} m on {len(errs)} ships -> {"PASS" if mae <= 30 else "FAIL"}; ALL={"PASS" if ok else "FAIL"}')
    log('STEP5: not executed by this script version — runs only after acceptance PASS is reported.')


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--probe', action='store_true'); g.add_argument('--run', action='store_true'); g.add_argument('--fwhm', action='store_true')
    a = ap.parse_args()
    log(f'== {datetime.utcnow().isoformat()}Z RUN_ID={RUN_ID} mode={MODE}')
    if a.probe: probe()
    elif a.run: run()
    else: fwhm()


if __name__ == '__main__':
    main()
