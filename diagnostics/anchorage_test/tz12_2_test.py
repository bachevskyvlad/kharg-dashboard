"""ТЗ-12.2: three test frames of the Kharg roadstead + ship count.

Frame (rev2): lon 50.250–50.520, lat 29.080–29.330 -> PNG 6301x6672 (4.17 m/px),
fallback 4379x4637 (6 m/px) if any PNG > 25 MB. Dates fixed to rev1 selection.
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

# ТЗ-12.2 rev2 frame (rev1 was lon 50.288–50.462, lat 29.195–29.325, 4054x3472)
LON0, LON1, LAT0, LAT1 = 50.250, 50.520, 29.080, 29.330
# (m/px, W, H): 4.17 m first; 6 m allowed if any PNG > 25 MB
SCALES = [(4.17, 6301, 6672), (6.0, 4379, 4637)]
MAX_PNG_BYTES = 25 * 1024 * 1024
PX_M, W, H = SCALES[0]
# rev2: keep the 3 dates of rev1 (seed=7 output on the rev1 frame)
FIXED_DATES = ['2025-09-10', '2024-05-12', '2026-01-02']
SH_MAX_PX = 2500
REQ_RES_M = 10
REL_ORBIT = 35
DATE_FROM, DATE_TO = '2023-01-01', '2026-12-31'
SEED = 7
N_DATES = 3
MIN_VALID_FRAC = 0.999

# ship_backfill.py constants
AREA_MIN_25, AREA_MAX_25 = 3, 400


DETECTOR_VERSION = 'v2.1'          # v2: prod 4-conn + bbox length; v2.1: 8-conn + merge + axis length
MERGE_GAP_M = 30.0                 # blobs closer than this are one object


def area_bounds():
    sc = (25.0 / PX_M) ** 2                  # 25 m px -> frame px
    return AREA_MIN_25 * sc, AREA_MAX_25 * sc
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
    full, partial, meta = {}, [], {}
    for d, fs in sorted(by_date.items()):
        u = unary_union([shape(f['geometry']) for f in fs])
        m = {'ids': [f['id'] for f in fs],
             'orbit_state': fs[0]['properties'].get('sat:orbit_state', ''),
             'footprint_covers_frame': bool(u.contains(frame))}
        meta[d] = m
        if m['footprint_covers_frame']:
            full[d] = m
        else:
            partial.append(d)
    log(f'catalog: {len(feats)} S1 IW features in AOI {DATE_FROM}..{DATE_TO}; '
        f'orbit {REL_ORBIT}: {len(by_date)} dates, full cover {len(full)}, partial {len(partial)}')
    return full, meta


def fetch(cfg, col, bbox, d):
    """10 m grid over the frame; tiled so each SH request is <= 2500 px a side."""
    import math
    from sentinelhub import SentinelHubRequest, MimeType, MosaickingOrder, bbox_to_dimensions, BBox, CRS
    gw, gh = bbox_to_dimensions(bbox, resolution=REQ_RES_M)
    nx, ny = math.ceil(gw / SH_MAX_PX), math.ceil(gh / SH_MAX_PX)
    xs = [round(i * gw / nx) for i in range(nx + 1)]
    ys = [round(j * gh / ny) for j in range(ny + 1)]
    out = np.full((gh, gw), np.nan, dtype='float32')
    for j in range(ny):
        for i in range(nx):
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            tb = BBox([LON0 + x0 / gw * (LON1 - LON0), LAT1 - y1 / gh * (LAT1 - LAT0),
                       LON0 + x1 / gw * (LON1 - LON0), LAT1 - y0 / gh * (LAT1 - LAT0)], crs=CRS.WGS84)
            req = SentinelHubRequest(
                evalscript=EVALSCRIPT,
                input_data=[SentinelHubRequest.input_data(
                    data_collection=col, time_interval=(d, d),
                    mosaicking_order=MosaickingOrder.MOST_RECENT,
                    other_args={'dataFilter': {'relativeOrbitNumber': REL_ORBIT}})],
                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                bbox=tb, size=(x1 - x0, y1 - y0), config=cfg)
            try:
                data = req.get_data()
            except Exception as e:
                check_pu(e)
                log(f'  {d}: API error {str(e)[:300]}')
                return None, (gw, gh)
            raw = np.array(data[0]).astype('float32')
            norm, dm = raw[:, :, 0], raw[:, :, 1]
            out[y0:y1, x0:x1] = np.where(dm > 0, norm, np.nan)   # no-data -> NaN
    return out, (gw, gh)


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
    raw = arr_m > thr
    # only parts >= one 25 m pixel take part (single speckle pixels must not chain
    # the sea into one giant object when merging)
    lab8, n8 = ndimage.label(raw, structure=np.ones((3, 3)))
    if n8:
        a8 = ndimage.sum_labels(raw, lab8, index=np.arange(1, n8 + 1))
        keep = np.concatenate([[False], a8 >= (25.0 / PX_M) ** 2])
        binm = keep[lab8]
    else:
        binm = raw
    # v2.1 (fix of split ships): 8-connectivity, and pixels of blobs separated by a
    # gap <= MERGE_GAP_M are grouped into one object (a diagonal hull or a hull with
    # a dim midsection is one ship, not 2–3).
    r = max(1, int(np.ceil(MERGE_GAP_M / PX_M / 2)))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    disk = (xx * xx + yy * yy) <= r * r
    grp, num = ndimage.label(ndimage.binary_dilation(binm, structure=disk), structure=np.ones((3, 3)))
    labeled = np.where(binm, grp, 0)
    objs = ndimage.find_objects(labeled, max_label=num)
    areas = ndimage.sum_labels(binm, labeled, index=np.arange(1, num + 1)) if num else []
    AREA_MIN, AREA_MAX = area_bounds()
    ships = []
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        area = float(areas[i - 1])
        if area < AREA_MIN or area > AREA_MAX:
            continue
        comp = labeled[sl] == i
        ys, xs = np.where(comp)
        ys = ys + sl[0].start; xs = xs + sl[1].start
        # length along the object's major axis (not the bbox side): diagonal hulls
        # are no longer shortened by up to 1.41x
        if len(xs) >= 3:
            c = np.stack([xs - xs.mean(), ys - ys.mean()])
            w, v = np.linalg.eigh(np.cov(c))
            proj = v[:, np.argmax(w)] @ c
            length_px = proj.max() - proj.min() + 1
        else:
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
def set_scale(k):
    global PX_M, W, H
    PX_M, W, H = SCALES[k]


def main():
    log(f'RUN_ID={RUN_ID} frame lon {LON0}-{LON1} lat {LAT0}-{LAT1}; request {REQ_RES_M} m (tiled <= {SH_MAX_PX} px)')
    cfg, col, bbox = sh()
    try:
        full, meta = candidate_dates(cfg, col, bbox)
        shuffled = random.Random(SEED).sample(sorted(full), len(full))
        log(f'random.Random({SEED}).sample(full-cover dates, this frame) first 6: {shuffled[:6]}')
        log(f'dates used (fixed = rev1 seed-7 selection): {FIXED_DATES}')
        for d in FIXED_DATES:
            m = meta.get(d)
            log(f'  {d}: orbit-35 scenes={m["ids"] if m else None} '
                f'footprint_covers_frame={m["footprint_covers_frame"] if m else None}')
        if any(d not in meta for d in FIXED_DATES):
            log('FAIL: a fixed date has no orbit-35 scene in catalog. Nothing written.'); sys.exit(2)

        raw = {}
        for d in FIXED_DATES:
            a10, size = fetch(cfg, col, bbox, d)
            if a10 is None:
                log(f'FAIL: fetch {d}. Nothing written.'); sys.exit(2)
            raw[d] = (a10, size, float(np.mean(np.isfinite(a10))))
    except StopPU as e:
        log('STOP: 403 ACCESS_INSUFFICIENT_PROCESSING_UNITS from Sentinel Hub. Nothing written.')
        log(str(e)); sys.exit(3)

    for k in range(len(SCALES)):
        set_scale(k)
        amin, amax = area_bounds()
        log(f'scale {PX_M} m/px -> {W}x{H}; blob area {amin:.1f}-{amax:.1f} px')
        gj, mask = load_masks()
        log(f'masks: {len(gj["features"])} features, masked px {mask.sum()} ({mask.mean():.3%} of frame)')
        tmp = tempfile.mkdtemp()
        results = []
        for d in FIXED_DATES:
            a10, size, vf = raw[d]
            arr = upsample(a10)
            ships, counts, thr = detect(arr, mask)
            png = os.path.join(tmp, f'anchorage_test_{d}.png')
            render(png, d, meta[d], arr, gj, ships, counts, thr)
            sz = os.path.getsize(png)
            log(f'  {d}: png {sz/1e6:.1f} MB')
            results.append((d, thr, counts, ships, meta[d], size, vf, sz))
            del arr
        too_big = [r[0] for r in results if r[7] > MAX_PNG_BYTES]
        if too_big and k + 1 < len(SCALES):
            log(f'PNG > 25 MB at {PX_M} m/px for {too_big} -> fallback to {SCALES[k + 1][0]} m/px for all dates')
            shutil.rmtree(tmp); continue
        break

    os.makedirs(HERE, exist_ok=True)
    for old in os.listdir(HERE):            # overwrite rev1 outputs
        if old.startswith('anchorage_test_') and old.endswith('.png'):
            os.remove(os.path.join(HERE, old))
    rows = []
    for d, thr, counts, ships, m, size, vf, sz in sorted(results):
        tot = sum(counts[c] for c in ('vlcc', 'suezmax', 'aframax', 'mr'))
        log(f'{d} orbit={REL_ORBIT} {m["orbit_state"]} scenes={m["ids"]} req10m={size[0]}x{size[1]} '
            f'frame={W}x{H}@{PX_M}m dataMask_frac={vf:.4f} thr={thr:.4f} vlcc={counts["vlcc"]} '
            f'suezmax={counts["suezmax"]} aframax={counts["aframax"]} mr={counts["mr"]} '
            f'small={counts["small"]} total_no_small={tot} png_MB={sz/1e6:.1f}')
        for sh_ in ships:
            rows.append({'date': d, 'lat': sh_['lat'], 'lon': sh_['lon'], 'length_m': sh_['length_m'],
                         'class': sh_['class']})
        shutil.move(os.path.join(tmp, f'anchorage_test_{d}.png'), os.path.join(HERE, f'anchorage_test_{d}.png'))
    with open(os.path.join(HERE, 'ships.csv'), 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=['date', 'lat', 'lon', 'length_m', 'class'])
        wr.writeheader(); wr.writerows(rows)
    log(f'ships.csv rows={len(rows)}  scale={PX_M} m/px {W}x{H}  RUN_ID={RUN_ID}  DONE')


if __name__ == '__main__':
    main()
