"""ТЗ-12.3: Kharg anchorage history on the ТЗ-12.2 pipeline -> Supabase.

Pipeline (unchanged, imported): diagnostics/anchorage_test/tz12_2_test.py
  frame 50.25–50.52 / 29.08–29.33, 4.17 m/px, masks data/exclusion_zones.geojson v2,
  prod threshold / blob / class logic. PNG renderer: diagnostics/anchorage_blind/blind_pack.py
  (full PNG, downscaled to 2048 px width).

Modes:
  --recon            Phase 0a: orbit-35 dates with full frame coverage, 2017-01-01 -> today
                     -> diagnostics/anchorage_hist/dates.csv
  --year YYYY        Phase 2: one batch = one year. Resume (skip dates already in
                     anchorage_daily or in local daily.csv). After EACH date: upsert both
                     tables + append local CSVs + write PNG to diagnostics/anchorage_hist/YYYY/.
                     ТЗ-12.1a dates are not recomputed: taken from anchorage_blind/detections.csv
                     and full.zip.
  --sync             push local daily.csv / ships.csv to Supabase (idempotent upsert)
  --update           weekly: new catalog dates after the last date in the DB -> process,
                     then --snapshots
  --snapshots        upload full PNGs of the two latest dates to Storage bucket
                     anchorage_snapshots as latest.png / previous.png (+ snapshots.json)

403 ACCESS_INSUFFICIENT_PROCESSING_UNITS -> stop at once, exit 3, report date + DB count.
data/anchorage.json is never read or written here.
"""
import os, sys, csv, json, time, argparse, zipfile, io
from datetime import date as date_t, datetime

import numpy as np
import requests
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'diagnostics', 'anchorage_test'))
sys.path.insert(0, os.path.join(ROOT, 'diagnostics', 'anchorage_blind'))
import tz12_2_test as t          # ТЗ-12.2 pipeline
import blind_pack as bp          # full-PNG renderer (2048 px)

SOURCE = 'tz12_frame_v2'
RUN_ID = os.environ.get('RUN_ID') or datetime.utcnow().strftime('local-%Y%m%dT%H%M%SZ')
DATES_CSV = os.path.join(HERE, 'dates.csv')
DAILY_CSV = os.path.join(HERE, 'daily.csv')
SHIPS_CSV = os.path.join(HERE, 'ships.csv')
BLIND_DIR = os.path.join(ROOT, 'diagnostics', 'anchorage_blind')
TZ121A_RUN = 'gha-42322619253'
TZ121A_THR = 0.55                 # all 30 dates logged thr=0.5500 in gha-42322619253
DAILY_FIELDS = ['date', 'orbit', 'threshold', 'total', 'total_no_small', 'vlcc', 'suezmax',
                'aframax', 'mr', 'small', 'masked_blobs', 'run_id', 'source']
SHIP_FIELDS = ['date', 'lat', 'lon', 'length_m', 'class']
SB_URL = os.environ.get('SUPABASE_URL', 'https://yoekhkczwdeyauwarsyy.supabase.co')
SB_KEY = os.environ.get('SUPABASE_WRITE_KEY', '')
BUCKET = 'anchorage_snapshots'


def log(*a):
    print(*a, flush=True)


# ── Supabase ─────────────────────────────────────────────────────────────────
class DB:
    def __init__(self):
        self.ok = bool(SB_KEY)
        self.err = None if self.ok else 'no SUPABASE_WRITE_KEY'
        self.h = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
                  'Content-Type': 'application/json'}
        if self.ok:
            r = requests.get(f'{SB_URL}/rest/v1/anchorage_daily?select=date&limit=1', headers=self.h, timeout=30)
            if r.status_code != 200:
                self.ok, self.err = False, f'anchorage_daily not readable: {r.status_code} {r.text[:200]}'
        log(f'DB: {"OK" if self.ok else "UNAVAILABLE -> local CSV only (" + self.err + ")"}')

    def dates(self, y0=None, y1=None):
        if not self.ok:
            return set()
        q = 'select=date&order=date'
        if y0:
            q += f'&date=gte.{y0}&date=lte.{y1}'
        out, off = set(), 0
        while True:
            r = requests.get(f'{SB_URL}/rest/v1/anchorage_daily?{q}&limit=1000&offset={off}',
                             headers=self.h, timeout=60)
            r.raise_for_status()
            rows = r.json(); out |= {x['date'] for x in rows}
            if len(rows) < 1000:
                return out
            off += 1000

    def count(self):
        if not self.ok:
            return None
        r = requests.get(f'{SB_URL}/rest/v1/anchorage_daily?select=date',
                         headers={**self.h, 'Prefer': 'count=exact', 'Range': '0-0'}, timeout=30)
        cr = r.headers.get('content-range', '*/?')
        return cr.split('/')[-1]

    def upsert(self, daily, ships):
        if not self.ok:
            return False
        d = daily['date']
        r = requests.post(f'{SB_URL}/rest/v1/anchorage_daily?on_conflict=date', json=[daily],
                          headers={**self.h, 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f'upsert anchorage_daily {d}: {r.status_code} {r.text[:300]}')
        r = requests.delete(f'{SB_URL}/rest/v1/anchorage_ships?date=eq.{d}', headers=self.h, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f'delete anchorage_ships {d}: {r.status_code} {r.text[:300]}')
        if ships:
            r = requests.post(f'{SB_URL}/rest/v1/anchorage_ships?on_conflict=date,lat,lon', json=ships,
                              headers={**self.h, 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=60)
            if r.status_code >= 300:
                raise RuntimeError(f'upsert anchorage_ships {d}: {r.status_code} {r.text[:300]}')
        return True

    def upload(self, name, data, ctype):
        r = requests.post(f'{SB_URL}/storage/v1/object/{BUCKET}/{name}', data=data,
                          headers={'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
                                   'Content-Type': ctype, 'x-upsert': 'true',
                                   'Cache-Control': 'max-age=300'}, timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f'storage upload {name}: {r.status_code} {r.text[:300]}')


# ── local CSV (always written; DB mirror) ────────────────────────────────────
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def append_csv(path, fields, rows):
    new = not os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


# ── ТЗ-12.1a reuse ───────────────────────────────────────────────────────────
def load_tz121a():
    out = {}
    path = os.path.join(BLIND_DIR, 'detections.csv')
    if not os.path.exists(path):
        return out
    for r in read_csv(path):
        e = out.setdefault(r['date'], {'ships': [], 'total': None})
        if r['row_type'] == 'ship':
            e['ships'].append({'date': r['date'], 'lat': float(r['lat']), 'lon': float(r['lon']),
                               'length_m': int(r['length_m']), 'class': r['class']})
        else:
            e['total'] = {k: int(r[k]) for k in ('total', 'total_no_small', 'vlcc', 'suezmax',
                                                  'aframax', 'mr', 'small')}
    return out


def tz121a_png(d):
    with zipfile.ZipFile(os.path.join(BLIND_DIR, 'full.zip')) as z:
        return z.read(f'full_{d}.png')


# ── detection helpers ────────────────────────────────────────────────────────
def masked_blobs(arr, mask, thr):
    """Blobs above the prod threshold (same area window) that fall inside the masks."""
    a = np.nan_to_num(arr, nan=0.0)
    lab, n = ndimage.label((a > thr) & mask)
    if not n:
        return 0
    areas = ndimage.sum_labels(np.ones_like(lab), lab, index=np.arange(1, n + 1))
    amin, amax = t.area_bounds()
    return int(np.sum((areas >= amin) & (areas <= amax)))


def pu_estimate(size):
    # SH PU ~ (w*h / 512^2) * (input bands / 3) * 2 (FLOAT32); VV only (dataMask not billed)
    w, h = size
    return (w * h / 262144.0) * (1 / 3) * 2


def cfg_all():
    t.DATE_FROM, t.DATE_TO = '2017-01-01', date_t.today().isoformat()
    t.set_scale(0)
    return t.sh()


# ── modes ────────────────────────────────────────────────────────────────────
def recon():
    cfg, col, bbox = cfg_all()
    try:
        full, meta = t.candidate_dates(cfg, col, bbox)
    except t.StopPU as e:
        log('STOP: 403 PU during catalog search'); log(str(e)); sys.exit(3)
    tz = load_tz121a()
    rows = [{'date': d, 'year': d[:4], 'n_scenes': len(full[d]['ids']),
             'scene_ids': ';'.join(full[d]['ids']), 'in_tz12_1a': int(d in tz)} for d in sorted(full)]
    with open(DATES_CSV, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    log(f'RECON {t.DATE_FROM}..{t.DATE_TO}: orbit-35 full-cover dates = {len(rows)}; '
        f'partial = {len(meta) - len(full)}; in ТЗ-12.1a = {sum(r["in_tz12_1a"] for r in rows)} '
        f'(of {len(tz)} in detections.csv); to compute = {len(rows) - sum(r["in_tz12_1a"] for r in rows)}')
    for y in range(2017, date_t.today().year + 1):
        yr = [r for r in rows if r['year'] == str(y)]
        log(f'  {y}: {len(yr)} dates ({sum(r["in_tz12_1a"] for r in yr)} from ТЗ-12.1a)  '
            f'first={yr[0]["date"] if yr else "-"} last={yr[-1]["date"] if yr else "-"}')
    gaps = []
    ds = [date_t.fromisoformat(r['date']) for r in rows]
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > 24:
            gaps.append(f'{a}->{b} ({(b - a).days} d)')
    log(f'gaps > 24 d between consecutive dates: {len(gaps)}')
    for g in gaps:
        log(f'  {g}')


def process_date(d, ctx, db, tz):
    """Process one date, write PNG + CSV + DB. Returns (n_ships, pu)."""
    cfg, col, bbox, gj, mask, meta = ctx
    ydir = os.path.join(HERE, d[:4]); os.makedirs(ydir, exist_ok=True)
    png = os.path.join(ydir, f'full_{d}.png')
    if d in tz and tz[d]['total']:
        c = tz[d]['total']; ships = tz[d]['ships']
        daily = {'date': d, 'orbit': t.REL_ORBIT, 'threshold': TZ121A_THR, **c,
                 'masked_blobs': None, 'run_id': TZ121A_RUN, 'source': SOURCE}
        with open(png, 'wb') as f:
            f.write(tz121a_png(d))
        pu = 0.0; how = 'from ТЗ-12.1a'
    else:
        a10, size = t.fetch(cfg, col, bbox, d)          # may raise StopPU
        if a10 is None:
            return None
        vf = float(np.mean(np.isfinite(a10)))
        if vf < t.MIN_VALID_FRAC:
            log(f'  {d}: dataMask_frac={vf:.4f} < {t.MIN_VALID_FRAC} -> skipped (hole)')
            return None
        arr = t.upsample(a10); del a10
        det, counts, thr = t.detect(arr, mask)
        mb = masked_blobs(arr, mask, thr)
        bp.render_full(png, d, meta[d], arr, gj, det, counts, thr)
        del arr
        tot = sum(counts.values())
        daily = {'date': d, 'orbit': t.REL_ORBIT, 'threshold': round(float(thr), 4), 'total': tot,
                 'total_no_small': tot - counts['small'], **counts, 'masked_blobs': mb,
                 'run_id': RUN_ID, 'source': SOURCE}
        ships = [{'date': d, 'lat': s['lat'], 'lon': s['lon'], 'length_m': s['length_m'],
                  'class': s['class']} for s in det]
        pu = 2 * pu_estimate((size[0] / 2, size[1]))     # two tiles
        how = 'computed'
    # de-duplicate pk (date, lat, lon) — two blobs can't share a centroid, but be safe
    seen, uniq = set(), []
    for s in ships:
        k = (s['lat'], s['lon'])
        if k not in seen:
            seen.add(k); uniq.append(s)
    append_csv(DAILY_CSV, DAILY_FIELDS, [daily])
    append_csv(SHIPS_CSV, SHIP_FIELDS, uniq)
    in_db = db.upsert(daily, uniq)
    log(f'  {d} {how}: thr={daily["threshold"]} total={daily["total"]} no_small={daily["total_no_small"]} '
        f'V{daily["vlcc"]} S{daily["suezmax"]} A{daily["aframax"]} MR{daily["mr"]} s{daily["small"]} '
        f'masked_blobs={daily["masked_blobs"]} db={"ok" if in_db else "pending"}')
    return len(uniq), pu


def run_dates(dates, db, label):
    cfg, col, bbox = cfg_all()
    gj, mask = t.load_masks()
    full, meta = t.candidate_dates(cfg, col, bbox)
    tz = load_tz121a()
    ctx = (cfg, col, bbox, gj, mask, meta)
    t0 = time.time(); n_ok = n_ship = 0; pu = 0.0; holes = []
    for d in dates:
        if d not in meta:
            holes.append(d); continue
        try:
            r = process_date(d, ctx, db, tz)
        except t.StopPU as e:
            log(f'STOP: 403 ACCESS_INSUFFICIENT_PROCESSING_UNITS. Остановился на дате {d}, '
                f'в базе {db.count()} дат (local daily.csv: {len(read_csv(DAILY_CSV))}).')
            log(str(e))
            summary(label, n_ok, n_ship, t0, pu, holes)
            sys.exit(3)
        if r is None:
            holes.append(d); continue
        n_ok += 1; n_ship += r[0]; pu += r[1]
    summary(label, n_ok, n_ship, t0, pu, holes)
    return n_ok


def summary(label, n_ok, n_ship, t0, pu, holes):
    log(f'BATCH {label}: dates processed={n_ok} ships={n_ship} minutes={(time.time() - t0) / 60:.1f} '
        f'PU~{pu:.0f} holes={holes}')


def year(y, db):
    if not os.path.exists(DATES_CSV):
        log('no dates.csv — run --recon first'); sys.exit(2)
    all_y = [r['date'] for r in read_csv(DATES_CSV) if r['year'] == str(y)]
    done = db.dates(f'{y}-01-01', f'{y}-12-31') | {r['date'] for r in read_csv(DAILY_CSV) if r['date'][:4] == str(y)}
    todo = [d for d in all_y if d not in done]
    log(f'YEAR {y}: catalog {len(all_y)}, already done {len(set(all_y) & done)} (resume), to do {len(todo)}')
    run_dates(todo, db, str(y))


def sync(db):
    if not db.ok:
        log(f'SYNC impossible: {db.err}'); sys.exit(2)
    ships = {}
    for s in read_csv(SHIPS_CSV):
        ships.setdefault(s['date'], {})[(s['lat'], s['lon'])] = {
            'date': s['date'], 'lat': float(s['lat']), 'lon': float(s['lon']),
            'length_m': int(s['length_m']), 'class': s['class']}
    last = {}
    for r in read_csv(DAILY_CSV):
        last[r['date']] = r                       # last row per date wins
    for d, r in sorted(last.items()):
        daily = {k: (None if r[k] in ('', 'None') else r[k]) for k in DAILY_FIELDS}
        for k in ('orbit', 'total', 'total_no_small', 'vlcc', 'suezmax', 'aframax', 'mr', 'small', 'masked_blobs'):
            daily[k] = None if daily[k] is None else int(daily[k])
        daily['threshold'] = float(daily['threshold'])
        db.upsert(daily, list(ships.get(d, {}).values()))
    log(f'SYNC: upserted {len(last)} dates; DB count now {db.count()}')


def snapshots(db):
    if not db.ok:
        log(f'SNAPSHOTS skipped: {db.err}'); return
    ds = sorted(db.dates())[-2:]
    if len(ds) < 2:
        log('SNAPSHOTS: fewer than 2 dates in DB'); return
    meta = {}
    for name, d in zip(('previous', 'latest'), ds):
        p = os.path.join(HERE, d[:4], f'full_{d}.png')
        if not os.path.exists(p):
            log(f'SNAPSHOTS: missing local PNG {p}'); return
        with open(p, 'rb') as f:
            db.upload(f'{name}.png', f.read(), 'image/png')
        meta[name] = {'date': d, 'file': f'{name}.png'}
    meta['updated'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    db.upload('snapshots.json', json.dumps(meta).encode(), 'application/json')
    log(f'SNAPSHOTS uploaded: previous={ds[0]} latest={ds[1]}')


def update(db):
    if not db.ok:
        log(f'UPDATE impossible: {db.err}'); sys.exit(2)
    cfg, col, bbox = cfg_all()
    full, meta = t.candidate_dates(cfg, col, bbox)
    have = db.dates()
    last = max(have) if have else '2017-01-01'
    new = [d for d in sorted(full) if d > last]
    log(f'UPDATE: last in DB {last}; new catalog dates {new}')
    if new:
        run_dates(new, db, 'update')
    # make sure the two latest PNGs exist locally (weekly runner has a fresh checkout)
    for d in sorted(db.dates())[-2:]:
        p = os.path.join(HERE, d[:4], f'full_{d}.png')
        if not os.path.exists(p):
            gj, mask = t.load_masks()
            ctx = (cfg, col, bbox, gj, mask, meta)
            a10, size = t.fetch(cfg, col, bbox, d)
            arr = t.upsample(a10); det, counts, thr = t.detect(arr, mask)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            bp.render_full(p, d, meta[d], arr, gj, det, counts, thr)
    snapshots(db)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--recon', action='store_true')
    g.add_argument('--year', type=int)
    g.add_argument('--sync', action='store_true')
    g.add_argument('--update', action='store_true')
    g.add_argument('--snapshots', action='store_true')
    a = ap.parse_args()
    log(f'RUN_ID={RUN_ID}')
    if a.recon:
        return recon()
    db = DB()
    try:
        if a.year:
            year(a.year, db)
        elif a.sync:
            sync(db)
        elif a.update:
            update(db)
        elif a.snapshots:
            snapshots(db)
    except t.StopPU as e:
        log(f'STOP: 403 PU. В базе {db.count()} дат.'); log(str(e)); sys.exit(3)


if __name__ == '__main__':
    main()
