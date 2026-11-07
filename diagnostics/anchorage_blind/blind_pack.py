"""ТЗ-12.1a: blind-validation pack, 30 dates.

Pipeline = ТЗ-12.2 unchanged (imported from diagnostics/anchorage_test/tz12_2_test.py):
frame 50.25–50.52 / 29.08–29.33, 4.17 m/px (6301x6672), 10 m S1 VV request (tiled),
masks data/exclusion_zones.geojson (v2), prod threshold/blob/class logic.

Dates: 3 per year 2017–2026, relative orbit 35, catalog footprint fully covers the
frame; within each year random.Random(11) (one generator, years in order) shuffles
the year's dates, first 3 whose raster has dataMask=1 on >=99.9 % of the frame.
One fetch per date -> two PNGs downscaled to 2048 px width:
  blind_NN.png  SAR + green land contours + blue point-mask circles with id, nothing else
  full_DATE.png as ТЗ-12.2 (ship boxes, header with date/orbit/threshold/counts)
blind numbers: random.Random(21) permutation of 1..30 over chronologically sorted dates.
Outputs (diagnostics/anchorage_blind/): blind.zip, full.zip, detections.csv, key.csv.
403 ACCESS_INSUFFICIENT_PROCESSING_UNITS -> stop, write nothing.
"""
import os, sys, csv, random, zipfile, tempfile, shutil
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'anchorage_test'))
import tz12_2_test as t                                   # ТЗ-12.2 pipeline as-is

YEARS = range(2017, 2027)
PER_YEAR = 3
SEED_DATES, SEED_IDS = 11, 21
OUT_W = 2048
LAND = (57, 255, 20)
PLAT = (40, 120, 255)


def log(*a):
    print(*a, flush=True)


def scaled_base(arr):
    v = arr[np.isfinite(arr)]
    lo, hi = np.percentile(v, 2), np.percentile(v, 98)
    g = np.clip((np.nan_to_num(arr, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)
    im = Image.fromarray((g * 255).astype('uint8'), 'L')
    oh = round(t.H * OUT_W / t.W)
    return im.resize((OUT_W, oh), Image.LANCZOS).convert('RGB'), OUT_W / t.W


def draw_masks(dr, gj, s, f):
    for ft in gj['features']:
        p = ft['properties']
        ring = [t.ll_to_px(x, y) for x, y in ft['geometry']['coordinates'][0]]
        ring = [(x * s, y * s) for x, y in ring]
        inside = [(x, y) for x, y in ring if 0 <= x < OUT_W and 0 <= y < t.H * s]
        if p['kind'] == 'land_buffer_500m':
            dr.line(ring + [ring[0]], fill=LAND, width=2)
        else:
            dr.line(ring + [ring[0]], fill=PLAT, width=2)
            if inside:
                cx = np.mean([x for x, _ in ring]); cy = np.mean([y for _, y in ring])
                dr.text((cx + 8, cy - 8), p['id'], fill=PLAT, font=f,
                        stroke_width=2, stroke_fill=(0, 0, 0))


def save_png(im, path):
    ov = list(t.COLORS.values()) + [LAND, PLAT, (0, 0, 0), (255, 255, 255), (200, 200, 200)]
    pal = [c for rgb in ov for c in rgb]
    n = 256 - len(ov)
    for i in range(n):
        v = round(i * 255 / (n - 1)); pal += [v, v, v]
    pimg = Image.new('P', (1, 1)); pimg.putpalette(pal)
    im.quantize(palette=pimg, dither=Image.Dither.NONE).save(path, optimize=True)


def render_blind(path, arr, gj):
    im, s = scaled_base(arr)
    draw_masks(ImageDraw.Draw(im), gj, s, t.font(14))
    save_png(im, path)


def render_full(path, d, meta, arr, gj, ships, counts, thr):
    im, s = scaled_base(arr)
    dr = ImageDraw.Draw(im)
    draw_masks(dr, gj, s, t.font(14))
    f_ship = t.font(13)
    for sh in ships:
        x0, y0, x1, y1 = [v * s for v in sh['_box']]
        c = t.COLORS[sh['class']]
        dr.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=c, width=2)
        dr.text((x1 + 7, y0 - 8), f"{sh['class']} {sh['length_m']}m", fill=c, font=f_ship,
                stroke_width=2, stroke_fill=(0, 0, 0))
    tot = sum(counts[k] for k in ('vlcc', 'suezmax', 'aframax', 'mr'))
    dr.rectangle([0, 0, OUT_W, 52], fill=(0, 0, 0))
    dr.text((8, 4), f"Kharg roadstead  {d}  orbit {t.REL_ORBIT} {meta['orbit_state']}  thr={thr:.3f}  "
                    f"V:{counts['vlcc']} S:{counts['suezmax']} A:{counts['aframax']} MR:{counts['mr']} "
                    f"small:{counts['small']}  total(no small)={tot}", fill=(255, 255, 255), font=t.font(20))
    dr.text((8, 30), f"{', '.join(meta['ids'])}  10 m -> {t.PX_M} m/px detect, shown {OUT_W}px wide  "
                     f"masks v2  run={t.RUN_ID}", fill=(200, 200, 200), font=t.font(13))
    lx = OUT_W - 520
    for k, c in t.COLORS.items():
        dr.rectangle([lx, 34, lx + 14, 46], outline=c, width=2)
        dr.text((lx + 18, 31), k, fill=c, font=t.font(13)); lx += 100
    save_png(im, path)


def zadd(z, src, name):
    # fixed timestamp: zip mtimes must not leak processing (= chronological) order
    zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    with open(src, 'rb') as f:
        z.writestr(zi, f.read(), compress_type=zipfile.ZIP_STORED)


def main():
    t.DATE_FROM, t.DATE_TO = '2017-01-01', '2026-12-31'
    t.set_scale(0)
    log(f'RUN_ID={t.RUN_ID} frame lon {t.LON0}-{t.LON1} lat {t.LAT0}-{t.LAT1} -> {t.W}x{t.H} @ {t.PX_M} m/px')
    gj, mask = t.load_masks()
    log(f'masks: {len(gj["features"])} features, masked {mask.mean():.3%} of frame')
    cfg, col, bbox = t.sh()
    tmp = tempfile.mkdtemp()
    results = []
    try:
        full, meta = t.candidate_dates(cfg, col, bbox)
        rng = random.Random(SEED_DATES)
        for y in YEARS:
            yd = sorted(d for d in full if d.startswith(str(y)))
            order = rng.sample(yd, len(yd))
            got, skipped = [], []
            for d in order:
                if len(got) == PER_YEAR:
                    break
                a10, size = t.fetch(cfg, col, bbox, d)
                vf = 0.0 if a10 is None else float(np.mean(np.isfinite(a10)))
                if a10 is None or vf < t.MIN_VALID_FRAC:
                    skipped.append((d, round(vf, 4))); continue
                arr = t.upsample(a10)
                ships, counts, thr = t.detect(arr, mask)
                bp = os.path.join(tmp, f'b_{d}.png'); fp = os.path.join(tmp, f'full_{d}.png')
                render_blind(bp, arr, gj)
                render_full(fp, d, meta[d], arr, gj, ships, counts, thr)
                results.append((d, thr, counts, ships, meta[d], vf))
                got.append(d)
                del arr, a10
            log(f'{y}: orbit-35 full-cover dates={len(yd)} picked={got} skipped={skipped}')
            if len(got) < PER_YEAR:
                log(f'FAIL: {y} has < {PER_YEAR} usable dates. Nothing written.'); sys.exit(2)
    except t.StopPU as e:
        log('STOP: 403 ACCESS_INSUFFICIENT_PROCESSING_UNITS from Sentinel Hub. Nothing written.')
        log(str(e)); sys.exit(3)

    results.sort(key=lambda r: r[0])
    perm = random.Random(SEED_IDS).sample(range(1, len(results) + 1), len(results))
    pid = {r[0]: f'blind_{n:02d}' for r, n in zip(results, perm)}

    log('DATES (chronological):')
    for d, thr, counts, ships, m, vf in results:
        tot = sum(counts.values()); tns = tot - counts['small']
        log(f'  {d} {pid[d]} scenes={m["ids"]} dataMask={vf:.4f} thr={thr:.4f} '
            f'vlcc={counts["vlcc"]} suezmax={counts["suezmax"]} aframax={counts["aframax"]} '
            f'mr={counts["mr"]} small={counts["small"]} total={tot} total_no_small={tns}')

    os.makedirs(HERE, exist_ok=True)
    with zipfile.ZipFile(os.path.join(HERE, 'blind.zip'), 'w', zipfile.ZIP_STORED) as z:
        for d in sorted(pid, key=pid.get):
            zadd(z, os.path.join(tmp, f'b_{d}.png'), f'{pid[d]}.png')
    with zipfile.ZipFile(os.path.join(HERE, 'full.zip'), 'w', zipfile.ZIP_STORED) as z:
        for d, *_ in results:
            zadd(z, os.path.join(tmp, f'full_{d}.png'), f'full_{d}.png')

    fields = ['date', 'picture_id', 'row_type', 'lat', 'lon', 'length_m', 'class',
              'total', 'total_no_small', 'vlcc', 'suezmax', 'aframax', 'mr', 'small']
    n_ship = 0
    with open(os.path.join(HERE, 'detections.csv'), 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=fields); wr.writeheader()
        for d, thr, counts, ships, m, vf in results:
            for sh in ships:
                wr.writerow({'date': d, 'picture_id': pid[d], 'row_type': 'ship', 'lat': sh['lat'],
                             'lon': sh['lon'], 'length_m': sh['length_m'], 'class': sh['class']})
                n_ship += 1
            tot = sum(counts.values())
            wr.writerow({'date': d, 'picture_id': pid[d], 'row_type': 'total', 'total': tot,
                         'total_no_small': tot - counts['small'], **counts})
    with open(os.path.join(HERE, 'key.csv'), 'w', newline='') as f:
        wr = csv.writer(f); wr.writerow(['picture_id', 'date'])
        for d in sorted(pid, key=pid.get):
            wr.writerow([pid[d], d])
    shutil.rmtree(tmp)
    log(f'blind.zip {os.path.getsize(os.path.join(HERE, "blind.zip"))/1e6:.1f} MB, '
        f'full.zip {os.path.getsize(os.path.join(HERE, "full.zip"))/1e6:.1f} MB, '
        f'detections ship rows={n_ship} total rows={len(results)}  RUN_ID={t.RUN_ID}  DONE')


if __name__ == '__main__':
    main()
