"""ТЗ-12.2: three test frames of the Kharg roadstead + ship count.

Frame (strict): lon 50.288–50.462, lat 29.195–29.325 -> PNG 4054x3472 (4.17 m/px).
S1 VV from Sentinel Hub (CDSE, same evalscript as scripts/ship_backfill.py),
requested at 10 m/px, bilinearly upsampled onto the frame grid.
Dates: 3 random (random.Random(7)) from relative-orbit-35 dates 2023–2026 whose
same-day orbit-35 footprint fully covers the frame.
Masks: data/exclusion_zones.geojson rasterized onto the frame grid.
Detection: ship_backfill.py logic (norm, p70+3.5σ clip 0.55–0.92, area 3–400 px
@25 m scaled to 4.17 m, length classes), applied on the frame grid.

Polarization: dataFilter has NO polarization constraint (VV band is read).
The prod value 'SV' returns empty rasters over Kharg (all scenes are 1SDV),
see diagnostics/anchorage_valid (ТЗ-12).

On 403 ACCESS_INSUFFICIENT_PROCESSING_UNITS: stop, write nothing, exit 3.
Outputs are written only after all 3 dates succeed.
"""
import os, sys, json, csv, random, shutil, tempfile
from datetime import datetime

import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
MASK_FILE = os.path.join(ROOT, 'data', 'exclusion_zones.geojson')
RUN_ID = os.environ.get('RUN_ID') or datetime.utcnow().strftime('local-%Y%m%dT%H%M%SZ')

LON0, LON1, LAT0, LAT1 = 50.288, 50.462, 29.195, 29.325
W, H = 4054, 3472
PX_M = 4.17
REQ_RES_M = 10
REL_ORBIT = 35
DATE_FROM, DATE_TO = '2023-01-01', '2026-12-31'
SEED = 7
N_DATES = 3
MIN_VALID_FRAC = 0.999

# ship_backfill.py constants
AREA_MIN_25, AREA_MAX_25 = 3, 400
AREA_SCALE = (25.0 / PX_M) ** 2           # 25 m px -> 4.17 m px
AREA_MIN, AREA_MAX = AREA_MIN_25 * AREA_SCALE, AREA_MAX_25 * AREA_SCALE
EVALSCRIPT = """
//VERSION=3
// band 0: same norm as ship_backfill.py; band 1: dataMask (coverage check only)
function setup() {
  return { input: ["VV", "dataMask"], output: { bands: 2, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  var db = 10 * Math.log10(s.VV + 1e-10);
  var norm = Math.max(0, Math.min(1, (db + 25) / 40));
  return [norm, s.dataMask];
}
"""
CLASSES = ['vlcc', 'suezmax', 'aframax', 'mr', 'small']
COLORS = {'vlcc': (255, 45, 45), 'suezmax': (255, 153, 0), 'aframax': (255, 238, 0),
          'mr': (0, 229, 255), 'small': (255, 61, 255)}
MASK_COLORS = {'land_buffer_500m': (57, 255, 20), 'static': (0, 160, 255), 'coast': (255, 255, 255)}


class StopPU(Exception):
    pass


def log(*a):
    print(*a, flush=True)


# ── Sentinel Hub ─────────────────────────────────────────────────────────────
def sh():
    from sentinelhub import SHConfig, BBox, CRS, DataCollection
    cfg = SHConfig()
    cfg.sh_client_id = os.environ.get('CLIENT_ID', '')
    cfg.sh_client_secret = os.environ.get('CLIENT_SECRET', '')
    cfg.sh_token_url = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
    cfg.sh_base_url = 'https://sh.dataspace.copernicus.eu'
    try:
        col = DataCollection.SENTINEL1_IW.define_from('s1iw_cdse_tz122', service_url=cfg.sh_base_url)
    except ValueError:
        col = DataCollection['s1iw_cdse_tz122']
    return cfg, col, BBox([LON0, LAT0, LON1, LAT1], crs=CRS.WGS84)


def check_pu(e):
    s = str(e)
    if 'ACCESS_INSUFFICIENT_PROCESSING_UNITS' in s or ('403' in s and 'PROCESSING_UNITS' in s.upper()):
        raise StopPU(s[:500])


def candidate_dates(cfg, col, bbox):
    from sentinelhub import SentinelHubCatalog
    from shapely.geometry import shape, box
    from shapely.ops import unary_union
    cat = SentinelHubCatalog(config=cfg)
    try:
        feats = list(cat.search(collection=col, bbox=bbox, time=(DATE_FROM, DATE_TO),
                                fields={'include': ['id', 'geometry', 'properties.datetime',
                                                    'properties.sat:relative_orbit',
                                                    'properties.sat:orbit_state']},
                                limit=100))
    except Exception as e:
        check_pu(e); raise
    frame = box(LON0, LAT0, LON1, LAT1)
    by_date = {}
    for f in feats:
        p = f.get('properties', {})
        if p.get('sat:relative_orbit') != REL_ORBIT:
            continue
        d = p['datetime'][:10]
        by_date.setdefault(d, []).append(f)
    full, partial = {}, []
    for d, fs in sorted(by_date.items()):
        u = unary_union([shape(f['geometry']) for f in fs])
        if u.contains(frame):
            full[d] = {'ids': [f['id'] for f in fs],
                       'orbit_state': fs[0]['properties'].get('sat:orbit_state', '')}
        else:
            partial.append(d)
    log(f'catalog: {len(feats)} S1 IW features in AOI {DATE_FROM}..{DATE_TO}; '
        f'orbit {REL_ORBIT}: {len(by_date)} dates, full cover {len(full)}, partial {len(partial)}')
    return full


def fetch(cfg, col, bbox, d):
    from sentinelhub import SentinelHubRequest, MimeType, MosaickingOrder, bbox_to_dimensions
    size = bbox_to_dimensions(bbox, resolution=REQ_RES_M)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=col, time_interval=(d, d),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={'dataFilter': {'relativeOrbitNumber': REL_ORBIT}})],
        responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
        bbox=bbox, size=size, config=cfg)
    try:
        data = req.get_data()
    except Exception as e:
        check_pu(e)
        log(f'  {d}: API error {str(e)[:300]}')
        return None, size
    raw = np.array(data[0]).astype('float32')
    norm, dm = raw[:, :, 0], raw[:, :, 1]
    norm = np.where(dm > 0, norm, np.nan).astype('float32')   # no-data -> NaN
    return norm, size


def upsample(a10):
    # bilinear onto the exact frame grid (W x H)
    a = np.where(np.isfinite(a10), a10, np.nan).astype('float32')
    return np.array(Image.fromarray(a, mode='F').resize((W, H), Image.BILINEAR))


# ── masks ────────────────────────────────────────────────────────────────────
def load_masks():
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    with open(MASK_FILE) as f:
        gj = json.load(f)
    tr = from_bounds(LON0, LAT0, LON1, LAT1, W, H)
    shapes = [(ft['geometry'], 1) for ft in gj['features']]
    mask = rasterize(shapes, out_shape=(H, W), transform=tr, fill=0, all_touched=True,
                     dtype='uint8').astype(bool)
    return gj, mask


def ll_to_px(lon, lat):
    return (lon - LON0) / (LON1 - LON0) * W, (LAT1 - lat) / (LAT1 - LAT0) * H


def px_to_ll(x, y):
    return LON0 + x / W * (LON1 - LON0), LAT1 - y / H * (LAT1 - LAT0)


# ── detection (ship_backfill.py logic) ───────────────────────────────────────
def adaptive_threshold(arr):
    valid = arr[np.isfinite(arr) & (arr > 0.01)]
    if len(valid) == 0:
        return 0.80
    sea_level = np.percentile(valid, 70)
    sea_pixels = valid[valid <= sea_level]
    if len(sea_pixels) < 100:
        return sea_level + 0.20
    return float(np.clip(sea_level + 3.5 * sea_pixels.std(), 0.55, 0.92))


def classify(length_m):
    if length_m >= 290: return 'vlcc'
    if length_m >= 260: return 'suezmax'
    if length_m >= 220: return 'aframax'
    if length_m >= 100: return 'mr'
    return 'small'


def detect(arr, mask):
    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[mask] = 0
    thr = adaptive_threshold(arr_m)
    labeled, num = ndimage.label(arr_m > thr)
    objs = ndimage.find_objects(labeled)
    areas = ndimage.sum_labels(np.ones_like(labeled), labeled, index=np.arange(1, num + 1)) if num else []
    ships = []
    for i, sl in enumerate(objs, start=1):
        area = float(areas[i - 1])
        if area < AREA_MIN or area > AREA_MAX:
            continue
        comp = labeled[sl] == i
        ys, xs = np.where(comp)
        ys = ys + sl[0].start; xs = xs + sl[1].start
        length_px = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        length_m = int(round(length_px * PX_M))
        lon, lat = px_to_ll(xs.mean() + 0.5, ys.mean() + 0.5)
        ships.append({'lat': round(lat, 6), 'lon': round(lon, 6), 'length_m': length_m,
                      'class': classify(length_m), 'area_px': int(area),
                      '_box': (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))})
    counts = {c: sum(s['class'] == c for s in ships) for c in CLASSES}
    return ships, counts, thr


# ── PNG ──────────────────────────────────────────────────────────────────────
def font(sz):
    try:
        import matplotlib.font_manager as fm
        return ImageFont.truetype(fm.findfont('DejaVu Sans'), sz)
    except Exception:
        return ImageFont.load_default()


def render(path, d, meta, arr, gj, ships, counts, thr):
    v = arr[np.isfinite(arr)]
    lo, hi = np.percentile(v, 2), np.percentile(v, 98)
    g = np.clip((np.nan_to_num(arr, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)
    im = Image.fromarray((g * 255).astype('uint8'), 'L').convert('RGB')
    dr = ImageDraw.Draw(im)
    f_lab, f_ship, f_title = font(30), font(24), font(40)

    for ft in gj['features']:
        p = ft['properties']
        kind = p['kind'] if p['kind'] == 'land_buffer_500m' else p.get('mask_kind', 'static')
        col = MASK_COLORS.get(kind, (57, 255, 20))
        ring = [ll_to_px(x, y) for x, y in ft['geometry']['coordinates'][0]]
        dr.line(ring + [ring[0]], fill=col, width=4)
        lab = p.get('id') or p.get('name')
        inside = [(x, y) for x, y in ring if 0 <= x < W and 120 <= y < H]
        if not inside:          # mask entirely outside the frame -> no label
            continue
        cx = min(max(np.mean([x for x, _ in inside]), 10), W - 300)
        cy = min(max(np.mean([y for _, y in inside]), 130), H - 40)
        dr.text((cx, cy), lab, fill=col, font=f_lab, stroke_width=2, stroke_fill=(0, 0, 0))

    pad = 8
    for s in ships:
        x0, y0, x1, y1 = s['_box']; c = COLORS[s['class']]
        dr.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=c, width=4)
        dr.text((x1 + pad + 6, y0 - pad - 4), f"{s['class']} {s['length_m']}m", fill=c,
                font=f_ship, stroke_width=2, stroke_fill=(0, 0, 0))

    tot = sum(counts[k] for k in ('vlcc', 'suezmax', 'aframax', 'mr'))
    title = (f"Kharg roadstead  {d}  orbit {REL_ORBIT} {meta['orbit_state']}  thr={thr:.3f}  "
             f"V:{counts['vlcc']} S:{counts['suezmax']} A:{counts['aframax']} MR:{counts['mr']} "
             f"small:{counts['small']}  total(no small)={tot}")
    sub = f"{', '.join(meta['ids'])}   10 m -> {PX_M} m/px   masks: exclusion_zones.geojson   run={RUN_ID}"
    dr.rectangle([0, 0, W, 120], fill=(0, 0, 0))
    dr.text((16, 10), title, fill=(255, 255, 255), font=f_title)
    dr.text((16, 66), sub, fill=(200, 200, 200), font=f_lab)
    lx = W - 1000
    for k, c in COLORS.items():
        dr.rectangle([lx, 74, lx + 30, 100], outline=c, width=4)
        dr.text((lx + 38, 70), k, fill=c, font=f_lab); lx += 190
    assert im.size == (W, H)
    # fixed palette: 224 greys + exact overlay colours, no dithering (size only)
    ov = list(COLORS.values()) + list(MASK_COLORS.values()) + [(0, 0, 0), (200, 200, 200)]
    pal = [c for rgb in ov for c in rgb]
    n = 256 - len(ov)
    for i in range(n):
        v = round(i * 255 / (n - 1)); pal += [v, v, v]
    pimg = Image.new('P', (1, 1)); pimg.putpalette(pal)
    im.quantize(palette=pimg, dither=Image.Dither.NONE).save(path, optimize=True)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    log(f'RUN_ID={RUN_ID} frame lon {LON0}-{LON1} lat {LAT0}-{LAT1} -> {W}x{H} @ {PX_M} m/px; '
        f'request {REQ_RES_M} m; area {AREA_MIN:.1f}-{AREA_MAX:.1f} px')
    gj, mask = load_masks()
    log(f'masks: {len(gj["features"])} features, masked px {mask.sum()} ({mask.mean():.3%} of frame)')
    cfg, col, bbox = sh()
    try:
        full = candidate_dates(cfg, col, bbox)
        order = sorted(full)
        rng = random.Random(SEED)
        shuffled = rng.sample(order, len(order))
        log(f'random.Random({SEED}).sample(full-cover dates) first 6: {shuffled[:6]}')

        tmp = tempfile.mkdtemp()
        results, skipped = [], []
        for d in shuffled:
            if len(results) == N_DATES:
                break
            a10, size = fetch(cfg, col, bbox, d)
            # coverage = share of frame pixels with dataMask=1 (calm dark sea counts as covered)
            vf = 0.0 if a10 is None else float(np.mean(np.isfinite(a10)))
            if a10 is None or vf < MIN_VALID_FRAC:
                skipped.append((d, round(vf, 4))); log(f'  skip {d}: dataMask_frac={vf:.4f}')
                continue
            arr = upsample(a10)
            ships, counts, thr = detect(arr, mask)
            png = os.path.join(tmp, f'anchorage_test_{d}.png')
            render(png, d, full[d], arr, gj, ships, counts, thr)
            results.append((d, thr, counts, ships, full[d], size, vf))
    except StopPU as e:
        log('STOP: 403 ACCESS_INSUFFICIENT_PROCESSING_UNITS from Sentinel Hub. Nothing written.')
        log(str(e)); sys.exit(3)

    log(f'SELECTED (seed={SEED}): {[r[0] for r in results]}  skipped={skipped}')
    if len(results) < N_DATES:
        log('FAIL: fewer than 3 usable dates. Nothing written.'); sys.exit(2)

    os.makedirs(HERE, exist_ok=True)
    rows = []
    for d, thr, counts, ships, meta, size, vf in sorted(results):
        tot = sum(counts[k] for k in ('vlcc', 'suezmax', 'aframax', 'mr'))
        log(f'{d} orbit={REL_ORBIT} {meta["orbit_state"]} scenes={meta["ids"]} req={size[0]}x{size[1]} '
            f'dataMask_frac={vf:.4f} thr={thr:.4f} vlcc={counts["vlcc"]} suezmax={counts["suezmax"]} '
            f'aframax={counts["aframax"]} mr={counts["mr"]} small={counts["small"]} total_no_small={tot}')
        for s in ships:
            rows.append({'date': d, 'lat': s['lat'], 'lon': s['lon'], 'length_m': s['length_m'],
                         'class': s['class']})
        shutil.move(os.path.join(tmp, f'anchorage_test_{d}.png'), os.path.join(HERE, f'anchorage_test_{d}.png'))
    with open(os.path.join(HERE, 'ships.csv'), 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=['date', 'lat', 'lon', 'length_m', 'class'])
        wr.writeheader(); wr.writerows(rows)
    log(f'ships.csv rows={len(rows)}  RUN_ID={RUN_ID}  DONE')


if __name__ == '__main__':
    main()
