"""ТЗ v2.2 — classes from anchorage_blobs (sea-brightness correction + merge). READ ONLY.

Stages (argv[1]):
  dryfit   Б1–Б3 dry run: fit k_m_per_sea / sea_ref so that daily VLCC count does not
           correlate with sea_p70 (|Spearman| < 0.15), merge blobs closer than merge_m,
           compare with anchorage_optical_ref. Writes NOTHING to the database.
"""
import os, sys, json, math, itertools
import numpy as np
import requests
from scipy.stats import spearmanr

U = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_WRITE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
HERE = os.path.dirname(os.path.abspath(__file__))
PAD = 8
RES = 4.17
REPORT = os.path.join(HERE, 'dryfit_report.txt')
out = []


def P(s=''):
    print(s, flush=True); out.append(str(s))


def get_all(tb, select, order='date'):
    rows, off = [], 0
    while True:
        r = requests.get(f'{U}/rest/v1/{tb}?select={select}&order={order}&limit=1000&offset={off}', headers=H, timeout=120)
        r.raise_for_status(); part = r.json(); rows += part
        if len(part) < 1000:
            return rows
        off += 1000


def classify(L):
    if L >= 290: return 'vlcc'
    if L >= 260: return 'suezmax'
    if L >= 220: return 'aframax'
    if L >= 100: return 'mr'
    return 'small'


def merge_date(blobs, merge_m):
    """Single-linkage clusters of blob centroids closer than merge_m. Union length = max side of
    the union bbox (same definition as length_bbox_m). Returns list of objects."""
    n = len(blobs)
    parent = list(range(n))
    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, j in itertools.combinations(range(n), 2):
        a, b = blobs[i], blobs[j]
        dm = math.hypot((a['lat'] - b['lat']) * 110574, (a['lon'] - b['lon']) * 111320 * math.cos(math.radians(a['lat'])))
        if dm < merge_m:
            parent[f(i)] = f(j)
    groups = {}
    for i in range(n):
        groups.setdefault(f(i), []).append(blobs[i])
    objs = []
    for g in groups.values():
        if len(g) == 1:
            L = g[0]['length_bbox_m']
        else:
            x0 = min(b['x0'] for b in g); y0 = min(b['y0'] for b in g)
            x1 = max(b['x0'] + b['patch_w'] - 2 * PAD - 1 for b in g)
            y1 = max(b['y0'] + b['patch_h'] - 2 * PAD - 1 for b in g)
            L = (max(x1 - x0, y1 - y0) + 1) * RES
        objs.append({'n': len(g), 'L': L, 'sea': g[0]['sea_p70'],
                     'lat': float(np.mean([b['lat'] for b in g])), 'lon': float(np.mean([b['lon'] for b in g])),
                     'parts': sorted(round(b['length_bbox_m']) for b in g)})
    return objs


def counts(objs, k, ref):
    c = {x: 0 for x in ('vlcc', 'suezmax', 'aframax', 'mr', 'small')}
    large = 0
    for o in objs:
        Lc = o['L'] - k * (o['sea'] - ref)
        c[classify(Lc)] += 1
        large += Lc >= 250
    tot = sum(c.values())
    return {**c, 'total': tot, 'total_no_small': tot - c['small'], 'large': large}


def dryfit():
    blobs = get_all('anchorage_blobs', 'date,lat,lon,x0,y0,patch_w,patch_h,sea_p70,length_bbox_m')
    ref = {r['date']: r for r in get_all('anchorage_optical_ref', '*')}
    calib = get_all('anchorage_calib', '*', order='id')
    daily = {r['date']: r for r in get_all('anchorage_daily', '*')}
    P(f'blobs={len(blobs)} dates={len({b["date"] for b in blobs})} optical_ref={sorted(ref)} calib={calib}')
    P(f'optical_ref rows: {json.dumps(list(ref.values()), default=str)}')
    merge_m = float((calib[0].get('merge_m') if calib and calib[0].get('merge_m') else 150))
    by = {}
    for b in blobs:
        by.setdefault(b['date'], []).append(b)
    dates = sorted(by)
    sea = {d: by[d][0]['sea_p70'] for d in dates}
    raw = {d: [{'n': 1, 'L': b['length_bbox_m'], 'sea': b['sea_p70'], 'lat': b['lat'], 'lon': b['lon'],
                'parts': [b['length_bbox_m']]} for b in by[d]] for d in dates}
    merged = {d: merge_date(by[d], merge_m) for d in dates}
    n_merges = {d: len(by[d]) - len(merged[d]) for d in dates}
    P(f'merge_m={merge_m}: dates with merges={sum(1 for d in dates if n_merges[d])}, objects merged away={sum(n_merges.values())}')

    def spear(objs_by_date, k, r):
        v = [counts(objs_by_date[d], k, r)['vlcc'] for d in dates]
        return spearmanr(v, [sea[d] for d in dates]).correlation

    def opt_err(objs_by_date, k, r):
        e = 0
        for d, o in ref.items():
            if d in objs_by_date:
                e += abs(counts(objs_by_date[d], k, r)['vlcc'] - int(o['vlcc']))
        return e

    k0 = float(calib[0]['k_m_per_sea']) if calib else 940.0
    r0 = float(calib[0]['sea_ref']) if calib else 0.18
    P(f'\nBEFORE (calib k={k0}, sea_ref={r0}):')
    P(f'  no merge : Spearman(VLCC, sea_p70) = {spear(raw, k0, r0):+.3f}, optical VLCC abs err = {opt_err(raw, k0, r0)}')
    P(f'  merge    : Spearman(VLCC, sea_p70) = {spear(merged, k0, r0):+.3f}, optical VLCC abs err = {opt_err(merged, k0, r0)}')
    P(f'  uncorrected k=0: Spearman = {spear(raw, 0, r0):+.3f}')

    best = None
    for k in np.arange(0, 2001, 20):
        for r in np.arange(0.10, 0.301, 0.005):
            s = spear(merged, k, r)
            if not np.isfinite(s) or abs(s) >= 0.15:
                continue
            e = opt_err(merged, k, r)
            key = (e, abs(s), abs(k - k0))
            if best is None or key < best[0]:
                best = (key, k, r, s)
    if best is None:
        P('\nFIT: no (k, sea_ref) gives |Spearman| < 0.15'); return
    (_, k1, r1, s1) = best
    P(f'\nFIT (merge on): k_m_per_sea={k1:.0f}, sea_ref={r1:.3f}, Spearman={s1:+.3f}, optical VLCC abs err={opt_err(merged, k1, r1)}')

    P('\nACCEPTANCE vs anchorage_optical_ref — date: site now (anchorage_daily) | after | optical')
    ok = True
    for d in sorted(ref):
        o = ref[d]; now = daily.get(d, {}); a = counts(merged.get(d, []), k1, r1); b = counts(raw.get(d, []), k1, r1)
        cols = ('vlcc', 'suezmax', 'aframax', 'mr', 'total')
        P(f'  {d}: now ' + ' '.join(f'{c}={now.get(c)}' for c in cols) +
          f' | after ' + ' '.join(f'{c}={a[c]}' for c in cols) + f' large={a["large"]}' +
          f' | optical ' + ' '.join(f'{c}={o.get(c)}' for c in cols) + f' | merges={n_merges.get(d, 0)}')
        if d in ('2026-10-18', '2026-10-24', '2026-10-30', '2026-11-05'):
            v_ok = abs(a['vlcc'] - int(o['vlcc'])) <= 1
            ok &= v_ok
            P(f'      VLCC ±1: {"PASS" if v_ok else "FAIL"}')
        af_ok = a['aframax'] <= int(o['aframax']) + 2 and a['mr'] <= int(o['mr']) + 2
        tot_ok = abs(a['total'] - b['total']) <= n_merges.get(d, 0)
        ok &= af_ok and tot_ok
        P(f'      Aframax/MR <= optical+2: {"PASS" if af_ok else "FAIL"}; total change {b["total"]}->{a["total"]} '
          f'<= merges {n_merges.get(d, 0)}: {"PASS" if tot_ok else "FAIL"}')
    P(f'\nACCEPTANCE OVERALL: {"PASS" if ok else "FAIL"}  (dry run — nothing written)')
    d = '2026-11-05'
    for ob in merged.get(d, []):
        if ob['n'] > 1:
            P(f'  merge example {d}: {ob["lat"]:.3f}/{ob["lon"]:.3f} parts {ob["parts"]} -> union {ob["L"]:.0f} m, '
              f'corrected {ob["L"] - k1 * (ob["sea"] - r1):.0f} m -> {classify(ob["L"] - k1 * (ob["sea"] - r1))}')
    # yearly VLCC share before/after
    vs = lambda O, k, r: sum(counts(O[d], k, r)['vlcc'] for d in dates) / max(1, sum(counts(O[d], k, r)['total_no_small'] for d in dates))
    P(f'\nVLCC share of large-4: calib before {vs(raw, k0, r0):.0%} -> after {vs(merged, k1, r1):.0%}')
    json.dump({'k_m_per_sea': round(float(k1)), 'sea_ref': round(float(r1), 3), 'merge_m': merge_m,
               'spearman': round(float(s1), 3), 'acceptance': 'PASS' if ok else 'FAIL'},
              open(os.path.join(HERE, 'dryfit_result.json'), 'w'), indent=1)


if __name__ == '__main__':
    st = sys.argv[1] if len(sys.argv) > 1 else 'dryfit'
    if st == 'dryfit':
        dryfit()
    open(REPORT, 'w').write('\n'.join(out) + '\n')
