"""ТЗ-12: validation pack for the Anchorage metric (Kharg roadstead).

Runs the CURRENT prod detector from scripts/ship_backfill.py unchanged
(same AOI, 25 m, same adaptive threshold, same masks, same class bounds) on
30 dates picked by select_dates.py, and dumps every detection + one PNG per
date. Per-component logging re-walks the exact prod loop; per-date counts are
asserted to equal ship_backfill.detect_ships() output.

Writes only to diagnostics/anchorage_valid/. Does not touch data/anchorage.json.
"""
import os, sys, csv, json, math, zipfile
from datetime import datetime, date as date_t

import numpy as np
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, HERE)

import ship_backfill as sb            # prod detector, imported as-is
from select_dates import load_records, select

RUN_ID = os.environ.get('RUN_ID') or datetime.utcnow().strftime('local-%Y%m%dT%H%M%SZ')
# Variant control. Default (A) = prod as-is. FETCH_POL overrides ONLY the
# dataFilter.polarization of the prod request (B: 'DV', the value anchorage.json
# was built with before a73f8a8). Everything else identical.
FETCH_POL = os.environ.get('FETCH_POL', '')
OUT = os.path.join(HERE, os.environ['OUT_SUBDIR']) if os.environ.get('OUT_SUBDIR') else HERE
os.makedirs(OUT, exist_ok=True)
PNG_DIR = os.path.join(OUT, 'png')
os.makedirs(PNG_DIR, exist_ok=True)

LAND_BUFFER_M = 500.0
PLATFORM_NEAR_M = 150.0
CLASSES = ['vlcc', 'suezmax', 'aframax', 'mr', 'small']
COLORS = {'vlcc': '#ff2d2d', 'suezmax': '#ff9900', 'aframax': '#ffee00',
          'mr': '#00e5ff', 'small': '#ff3dff'}


# ── geometry helpers ─────────────────────────────────────────────────────────
def px_to_lon_lat(x, y):
    """Inverse of sb.lon_lat_to_px (pixel coords as float, centre = +0.5)."""
    b, (w, h) = sb.bbox, sb.size
    lon = b.min_x + x / w * (b.max_x - b.min_x)
    lat = b.max_y - y / h * (b.max_y - b.min_y)
    return lon, lat


def _m(dlon, dlat, lat):
    return math.hypot(dlon * 111320.0 * math.cos(math.radians(lat)), dlat * 110574.0)


def dist_to_land_rect_m(lon, lat):
    lon0, lat0, lon1, lat1 = sb.KHARG_ISLAND_BBOX
    clon = min(max(lon, lon0), lon1)
    clat = min(max(lat, lat0), lat1)
    return _m(lon - clon, lat - clat, lat)


def dist_to_nearest_platform_m(lon, lat):
    return min(_m(lon - plon, lat - plat, lat) for plon, plat in sb.PLATFORMS)


def classify(length_m):
    # identical bounds to sb.detect_ships
    if length_m >= 290: return 'vlcc'
    if length_m >= 260: return 'suezmax'
    if length_m >= 220: return 'aframax'
    if length_m >= 100: return 'mr'
    return 'small'


# ── detector with per-component logging (mirrors sb.detect_ships 1:1) ───────
def detect_with_log(arr):
    h, w = arr.shape
    mask = np.zeros((h, w), dtype=bool)
    ilon0, ilat0, ilon1, ilat1 = sb.KHARG_ISLAND_BBOX
    ix0, iy1 = sb.lon_lat_to_px(ilon0, ilat0)
    ix1, iy0 = sb.lon_lat_to_px(ilon1, ilat1)
    mask[max(0, iy0):min(h, iy1), max(0, ix0):min(w, ix1)] = True
    B = sb.PLATFORM_BUFFER_PX
    for lon, lat in sb.PLATFORMS:
        px, py = sb.lon_lat_to_px(lon, lat)
        if 0 <= px < w and 0 <= py < h:
            mask[max(0, py - B):min(h, py + B), max(0, px - B):min(w, px + B)] = True
    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[mask] = 0
    thr = sb.adaptive_threshold(arr_m)
    labeled, num = ndimage.label(arr_m > thr)

    dets = []
    counts = {c: 0 for c in CLASSES}
    for i in range(1, num + 1):
        comp = (labeled == i)
        area = int(comp.sum())
        if area < 3 or area > 400:
            continue
        ys, xs = np.where(comp)
        length_px = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        length_m = int(length_px * sb.RESOLUTION)
        cls = classify(length_m)
        counts[cls] += 1
        lon, lat = px_to_lon_lat(xs.mean() + 0.5, ys.mean() + 0.5)
        d_land = dist_to_land_rect_m(lon, lat)
        d_plat = dist_to_nearest_platform_m(lon, lat)
        dets.append({
            'lat': round(lat, 6), 'lon': round(lon, 6), 'length_m': length_m,
            'class': cls, 'area_px': area,
            'peak_norm': round(float(arr_m[comp].max()), 4),
            'in_land_mask_buffer': int(d_land <= LAND_BUFFER_M),
            'near_platform': int(d_plat < PLATFORM_NEAR_M),
            'dist_land_rect_m': round(d_land, 1),
            'dist_platform_m': round(d_plat, 1),
            '_box': (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
        })
    return dets, counts, thr, mask


# ── scene ids (catalog; process API uses MOST_RECENT on the same day) ────────
def scene_ids_for(d):
    try:
        cat = sb.SentinelHubCatalog(config=sb.config)
        feats = list(cat.search(
            collection=sb.S1_CDSE, bbox=sb.bbox,
            time=(f'{d}T00:00:00Z', f'{d}T23:59:59Z'),
            fields={'include': ['id', 'properties.datetime']}, limit=50))
        feats.sort(key=lambda f: f.get('properties', {}).get('datetime', ''), reverse=True)
        return [f['id'] for f in feats]
    except Exception as e:
        print(f'  catalog error {d}: {e}')
        return []


# ── PNG ──────────────────────────────────────────────────────────────────────
def render_png(d, arr, dets, thr, scene_id, counts, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.lines import Line2D

    h, w = arr.shape
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, (h + 90) / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, h / (h + 90)])
    a = np.where(np.isfinite(arr), arr, 0)
    ax.imshow(a, cmap='gray', vmin=0, vmax=1, interpolation='nearest')
    ax.set_xlim(-0.5, w - 0.5); ax.set_ylim(h - 0.5, -0.5); ax.axis('off')

    # old land-mask rectangle (exact pixel rectangle the detector zeroes)
    ilon0, ilat0, ilon1, ilat1 = sb.KHARG_ISLAND_BBOX
    ix0, iy1 = sb.lon_lat_to_px(ilon0, ilat0)
    ix1, iy0 = sb.lon_lat_to_px(ilon1, ilat1)
    ax.add_patch(Rectangle((ix0 - 0.5, iy0 - 0.5), ix1 - ix0, iy1 - iy0,
                           fill=False, ec='#39ff14', lw=1.5, ls='--'))
    for lon, lat in sb.PLATFORMS:
        px, py = sb.lon_lat_to_px(lon, lat)
        ax.plot(px, py, marker='x', color='#39ff14', ms=12, mew=2)

    pad = 3
    for det in dets:
        x0, y0, x1, y1 = det['_box']
        c = COLORS[det['class']]
        ax.add_patch(Rectangle((x0 - pad - 0.5, y0 - pad - 0.5), x1 - x0 + 1 + 2 * pad,
                               y1 - y0 + 1 + 2 * pad, fill=False, ec=c, lw=1.2))
        ax.text(x1 + pad + 2, y0 - pad, f"{det['class']} {det['length_m']}m",
                color=c, fontsize=7, va='bottom', ha='left',
                bbox=dict(fc='black', alpha=0.55, pad=0.5, lw=0))

    tot = sum(counts[k] for k in ('vlcc', 'suezmax', 'aframax', 'mr'))
    title = (f"Kharg anchorage {d}   scene={scene_id or 'n/a'}   thr={thr:.3f}   "
             f"V:{counts['vlcc']} S:{counts['suezmax']} A:{counts['aframax']} "
             f"MR:{counts['mr']} small:{counts['small']}   total(no small)={tot}   run={RUN_ID}")
    fig.text(0.005, 1 - 30 / (h + 90), title, fontsize=11, color='black', va='center')
    handles = [Line2D([], [], color=COLORS[k], lw=3, label=k) for k in CLASSES]
    handles += [Line2D([], [], color='#39ff14', marker='x', lw=0, ms=9, mew=2, label='platform (12)'),
                Line2D([], [], color='#39ff14', ls='--', lw=1.5, label='old land-mask rect')]
    fig.legend(handles=handles, loc='upper left', bbox_to_anchor=(0.005, 1 - 45 / (h + 90)),
               ncol=len(handles), fontsize=9, frameon=False)
    fig.savefig(path, dpi=dpi, facecolor='white')
    plt.close(fig)
    # 128-colour palette PNG to keep the pack small (pixel grid unchanged)
    from PIL import Image
    im = Image.open(path).convert('RGB')
    im.quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)


def fetch(d):
    if not FETCH_POL:
        return sb.fetch_pass(d)                      # prod, untouched
    # copy of sb.fetch_pass with only the polarization value replaced
    ds = d.strftime('%Y-%m-%d')
    request = sb.SentinelHubRequest(
        evalscript=sb.evalscript,
        input_data=[sb.SentinelHubRequest.input_data(
            data_collection=sb.S1_CDSE, time_interval=(ds, ds),
            mosaicking_order=sb.MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": FETCH_POL}})],
        responses=[sb.SentinelHubRequest.output_response("default", sb.MimeType.TIFF)],
        bbox=sb.bbox, size=sb.size, config=sb.config)
    try:
        data = request.get_data()
    except Exception as e:
        print(f"  API error: {e}"); return None
    if not data or data[0] is None:
        return None
    raw = np.array(data[0])
    if raw.ndim == 3 and raw.shape[2] >= 1: arr = raw[:, :, 0]
    elif raw.ndim == 3 and raw.shape[0] == 1: arr = raw[0]
    elif raw.ndim == 2: arr = raw
    else: arr = raw.reshape(raw.shape[-2], raw.shape[-1])
    if arr.max() < 0.01:
        return None
    return arr


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    records = load_records()
    by_date = {r['date']: r for r in records}
    all_dates = sorted(by_date)
    picks = select(records)
    print(f'FETCH_POL={FETCH_POL or "prod(SV)"} OUT={OUT}')
    print(f'RUN_ID={RUN_ID}  AOI={list(sb.bbox)}  size={sb.size}  res={sb.RESOLUTION}m')
    print('Selected dates (before run):')
    for p in picks:
        print(f"  {p['year']} {p['slot']:<5} {p['date']} anch_total={p['total']}")

    used = {p['date'] for p in picks}
    det_rows, sum_rows, no_scene_final = [], [], 0

    for p in picks:
        orig = p['date']
        tried, d, arr = [], orig, None
        # candidate order: original, then same-year neighbours by distance
        y = orig[:4]
        neigh = sorted((x for x in all_dates if x[:4] == y and x not in used),
                       key=lambda x: (abs((date_t.fromisoformat(x) - date_t.fromisoformat(orig)).days), x))
        # "scene exists" = CDSE catalog has an S1 IW product intersecting the AOI
        # that day (polarization-independent, so A and B get the same dates).
        sids = []
        for cand in [orig] + neigh:
            tried.append(cand)
            sids = scene_ids_for(cand)
            if not sids:
                print(f'  {cand}: no scene in catalog')
                continue
            arr = fetch(date_t.fromisoformat(cand))
            if arr is not None:
                d = cand
                break
            print(f'  {cand}: catalog has scene but fetch returned None')
        if arr is None:
            no_scene_final += 1
            print(f'  {orig}: NO SCENE after {len(tried)} candidates')
            continue
        used.add(d)

        dets, counts, thr, _ = detect_with_log(arr)
        prod_counts, prod_thr = sb.detect_ships(arr)
        assert prod_counts == counts and abs(prod_thr - thr) < 1e-12, (d, prod_counts, counts)

        sid = sids[0] if sids else ''
        for k, det in enumerate(dets):
            row = {'date': d, 'scene_id': sid, 'det_idx': k}
            row.update({k2: v for k2, v in det.items() if not k2.startswith('_')})
            det_rows.append(row)

        png = os.path.join(PNG_DIR, f'anchorage_{d}.png')
        render_png(d, arr, dets, thr, sid, counts, png)

        a = by_date[d]
        tot = counts['vlcc'] + counts['suezmax'] + counts['aframax'] + counts['mr']
        sum_rows.append({
            'date': d, 'n_vlcc': counts['vlcc'], 'n_suezmax': counts['suezmax'],
            'n_aframax': counts['aframax'], 'n_mr': counts['mr'], 'n_small': counts['small'],
            'total_detected': tot,
            'anch_vlcc': a['vlcc'], 'anch_suezmax': a['suezmax'], 'anch_aframax': a['aframax'],
            'anch_mr': a['mr'], 'anch_total': a['total'],
            'match': int(tot == a['total']),
            'year': p['year'], 'slot': p['slot'],
            'replaced': int(d != orig), 'replaced_from': orig if d != orig else '',
            'n_candidates_tried': len(tried),
            'scene_id': sid, 'n_scenes_that_day': len(sids), 'threshold': round(thr, 4),
            'nan_frac': round(float(np.mean(~np.isfinite(arr))), 4),
            'valid_frac_gt001': round(float(np.mean(np.nan_to_num(arr) > 0.01)), 4),
            'polarization_filter': FETCH_POL or 'SV(prod)',
            'run_id': RUN_ID,
        })
        print(f'  {d} thr={thr:.3f} det={len(dets)} total={tot} anch={a["total"]} '
              f'{"MATCH" if tot == a["total"] else "DIFF"}{" (repl " + orig + ")" if d != orig else ""}')

    det_fields = ['date', 'scene_id', 'det_idx', 'lat', 'lon', 'length_m', 'class', 'area_px',
                  'peak_norm', 'in_land_mask_buffer', 'near_platform',
                  'dist_land_rect_m', 'dist_platform_m']
    with open(os.path.join(OUT, 'detections.csv'), 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=det_fields); wr.writeheader(); wr.writerows(det_rows)
    sum_rows.sort(key=lambda r: r['date'])
    with open(os.path.join(OUT, 'summary.csv'), 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=list(sum_rows[0].keys())); wr.writeheader(); wr.writerows(sum_rows)

    pngs = sorted(x for x in os.listdir(PNG_DIR) if x.endswith('.png'))
    zpath = os.path.join(OUT, f'anchorage_valid_{RUN_ID}{"_" + FETCH_POL if FETCH_POL else ""}.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for x in pngs:
            z.write(os.path.join(PNG_DIR, x), f'png/{x}')
        z.write(os.path.join(OUT, 'detections.csv'), 'detections.csv')
        z.write(os.path.join(OUT, 'summary.csv'), 'summary.csv')

    meta = {'run_id': RUN_ID, 'polarization_filter': FETCH_POL or 'SV(prod)', 'n_dates': len(sum_rows), 'n_png': len(pngs),
            'no_scene': no_scene_final, 'n_detections': len(det_rows),
            'n_replaced': sum(r['replaced'] for r in sum_rows),
            'n_match': sum(r['match'] for r in sum_rows),
            'zip': os.path.basename(zpath), 'zip_bytes': os.path.getsize(zpath),
            'aoi': list(sb.bbox), 'size_px': list(sb.size), 'resolution_m': sb.RESOLUTION}
    with open(os.path.join(OUT, 'run_meta.json'), 'w') as f:
        json.dump(meta, f, indent=1)
    print('META', json.dumps(meta))
    ok = meta['n_dates'] == 30 and meta['n_png'] == 30 and no_scene_final == 0
    print('CHECK', 'OK' if ok else 'FAIL')
    sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
