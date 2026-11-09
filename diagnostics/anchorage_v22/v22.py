"""ТЗ v2.2 — classes from anchorage_blobs (sea-brightness correction + merge). READ ONLY.

Stages (argv[1]):
  dryfit   Б1–Б3 dry run: fit k_m_per_sea / sea_ref so that daily VLCC count does not
           correlate with sea_p70 (|Spearman| < 0.15), merge blobs closer than merge_m,
           compare with anchorage_optical_ref. Writes NOTHING to the database.
  axisfit  Same sweep but using PCA major-axis length from patch_b64 (0 PU).
           Single blobs → axis length; merged groups → union bbox length.
           Detailed per-blob table for 2026-10-30. Writes NOTHING to the database.
"""
import os, sys, json, math, itertools, base64, io
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

    acc_dates = ('2026-10-18', '2026-10-24', '2026-10-30', '2026-11-05')
    def score(O, k, r):
        ev = sum(abs(counts(O[d], k, r)['vlcc'] - int(ref[d]['vlcc'])) for d in acc_dates if d in O)
        vmax = max(abs(counts(O[d], k, r)['vlcc'] - int(ref[d]['vlcc'])) for d in acc_dates if d in O)
        over = sum(max(0, counts(O[d], k, r)[c] - int(ref[d][c]) - 2) for d in ref for c in ('aframax', 'mr')
                   if d in O and ref[d][c] is not None)
        return ev, vmax, over
    P('\nSWEEP: merge_m | k | sea_ref | Spearman | VLCC abs err (4 dates) | max VLCC err | Aframax/MR excess over optical+2')
    cands = []
    for mm in (150, 200, 250):
        O = {d: merge_date(by[d], mm) for d in dates}
        nm = sum(len(by[d]) - len(O[d]) for d in dates)
        for k in np.arange(0, 1501, 50):
            for r in np.arange(0.12, 0.261, 0.01):
                sp = spear(O, k, r)
                ev, vmax, over = score(O, k, r)
                cands.append((mm, float(k), float(r), float(sp), ev, vmax, over, nm))
    ok_c = [c for c in cands if abs(c[3]) < 0.15]
    for c in sorted(ok_c, key=lambda c: (c[5], c[4], c[6], abs(c[3])))[:8]:
        P(f'  {c[0]:>3} m | k={c[1]:>5.0f} | ref={c[2]:.2f} | rho={c[3]:+.3f} | err={c[4]} | max={c[5]} | over={c[6]} | merges total={c[7]}')
    P('  best ignoring the Spearman constraint:')
    for c in sorted(cands, key=lambda c: (c[5], c[4], c[6]))[:5]:
        P(f'  {c[0]:>3} m | k={c[1]:>5.0f} | ref={c[2]:.2f} | rho={c[3]:+.3f} | err={c[4]} | max={c[5]} | over={c[6]}')
    pick = sorted(ok_c, key=lambda c: (c[5], c[4], c[6], abs(c[3])))[0] if ok_c else None
    if pick is None:
        P('\nFIT: nothing satisfies |Spearman| < 0.15'); return
    merge_m, k1, r1, s1 = pick[0], pick[1], pick[2], pick[3]
    merged = {d: merge_date(by[d], merge_m) for d in dates}
    n_merges = {d: len(by[d]) - len(merged[d]) for d in dates}
    P(f'\nFIT: merge_m={merge_m}, k_m_per_sea={k1:.0f}, sea_ref={r1:.2f}, Spearman={s1:+.3f}')
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
        af_ok = (o['aframax'] is None or a['aframax'] <= int(o['aframax']) + 2) and (o['mr'] is None or a['mr'] <= int(o['mr']) + 2)
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


def axis_length_m(patch_b64, threshold_norm):
    """PCA major-axis length (metres) from uint8 PNG patch."""
    from PIL import Image
    arr = np.array(Image.open(io.BytesIO(base64.b64decode(patch_b64))), dtype=np.float32) / 255.0
    core = arr[PAD:-PAD, PAD:-PAD]
    ship = core > float(threshold_norm)
    ys, xs = np.where(ship)
    if len(xs) < 2:
        return float(max(core.shape[0], core.shape[1])) * RES
    cx, cy = xs.mean(), ys.mean()
    dx, dy = xs.astype(float) - cx, ys.astype(float) - cy
    cov = np.array([[np.dot(dx, dx), np.dot(dx, dy)],
                    [np.dot(dx, dy), np.dot(dy, dy)]]) / len(xs)
    _, vecs = np.linalg.eigh(cov)
    pc1 = vecs[:, -1]
    proj = dx * pc1[0] + dy * pc1[1]
    return (float(proj.max()) - float(proj.min()) + 1) * RES


def merge_date_axis(day_blobs, merge_m):
    """Like merge_date but singles use length_axis_m; merged groups use union bbox."""
    n = len(day_blobs)
    parent = list(range(n))
    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, j in itertools.combinations(range(n), 2):
        a, b = day_blobs[i], day_blobs[j]
        dm = math.hypot((a['lat'] - b['lat']) * 110574,
                        (a['lon'] - b['lon']) * 111320 * math.cos(math.radians(a['lat'])))
        if dm < merge_m:
            parent[f(i)] = f(j)
    groups = {}
    for i in range(n):
        groups.setdefault(f(i), []).append(day_blobs[i])
    objs = []
    for g in groups.values():
        if len(g) == 1:
            L = g[0]['length_axis_m']
        else:
            x0 = min(b['x0'] for b in g); y0 = min(b['y0'] for b in g)
            x1 = max(b['x0'] + b['patch_w'] - 2 * PAD - 1 for b in g)
            y1 = max(b['y0'] + b['patch_h'] - 2 * PAD - 1 for b in g)
            L = (max(x1 - x0, y1 - y0) + 1) * RES
        objs.append({'n': len(g), 'L': L, 'sea': g[0]['sea_p70'],
                     'lat': float(np.mean([b['lat'] for b in g])),
                     'lon': float(np.mean([b['lon'] for b in g])),
                     'parts_axis': sorted(round(b['length_axis_m']) for b in g),
                     'parts_bbox': sorted(round(b['length_bbox_m']) for b in g)})
    return objs


def axisfit():
    """Б1–Б3 dry run using PCA major-axis length from patch_b64. Writes nothing."""
    P('=== AXISFIT: PCA major-axis from patch_b64 (0 PU) ===')
    P('Fetching blobs...')
    blobs_raw = get_all('anchorage_blobs',
                        'date,lat,lon,x0,y0,patch_w,patch_h,sea_p70,length_bbox_m,threshold,patch_b64')
    ref = {r['date']: r for r in get_all('anchorage_optical_ref', '*')}
    calib = get_all('anchorage_calib', '*', order='id')
    daily = {r['date']: r for r in get_all('anchorage_daily', '*')}
    P(f'blobs={len(blobs_raw)} dates={len({b["date"] for b in blobs_raw})} optical_ref={sorted(ref)}')
    P(f'optical_ref rows: {json.dumps(list(ref.values()), default=str)}')

    P('Computing axis lengths...')
    fails = 0
    for b in blobs_raw:
        thr = float(b.get('threshold') or 0.55)
        if b.get('patch_b64'):
            try:
                b['length_axis_m'] = axis_length_m(b['patch_b64'], thr)
            except Exception as e:
                b['length_axis_m'] = b['length_bbox_m']; fails += 1
        else:
            b['length_axis_m'] = b['length_bbox_m']; fails += 1
    P(f'axis lengths done: {len(blobs_raw)} blobs, {fails} fallbacks to bbox')

    ratios = [b['length_axis_m'] / b['length_bbox_m'] for b in blobs_raw if b['length_bbox_m'] > 0]
    P(f'axis/bbox ratio: min={min(ratios):.2f} p25={float(np.percentile(ratios,25)):.2f} '
      f'median={float(np.median(ratios)):.2f} p75={float(np.percentile(ratios,75)):.2f} max={max(ratios):.2f}')

    by = {}
    for b in blobs_raw:
        by.setdefault(b['date'], []).append(b)
    dates = sorted(by)
    sea = {d: by[d][0]['sea_p70'] for d in dates}

    raw_axis = {d: [{'n': 1, 'L': b['length_axis_m'], 'sea': b['sea_p70'],
                     'lat': b['lat'], 'lon': b['lon'],
                     'parts_axis': [b['length_axis_m']]} for b in by[d]] for d in dates}

    acc_dates = ('2026-10-18', '2026-10-24', '2026-10-30', '2026-11-05')

    def spear_a(O, k, r):
        v = [counts(O[d], k, r)['vlcc'] for d in dates]
        return spearmanr(v, [sea[d] for d in dates]).correlation

    def score_a(O, k, r):
        ev = sum(abs(counts(O[d], k, r)['vlcc'] - int(ref[d]['vlcc'])) for d in acc_dates if d in O)
        vmax = max(abs(counts(O[d], k, r)['vlcc'] - int(ref[d]['vlcc'])) for d in acc_dates if d in O)
        over = sum(max(0, counts(O[d], k, r)[c] - int(ref[d][c]) - 2) for d in ref for c in ('aframax', 'mr')
                   if d in O and ref[d][c] is not None)
        return ev, vmax, over

    P('\nSWEEP axis: merge_m | k | sea_ref | Spearman | VLCC err (4 dates) | max VLCC err | Af/MR excess')
    cands = []
    for mm in (150, 200, 250):
        O = {d: merge_date_axis(by[d], mm) for d in dates}
        nm = sum(len(by[d]) - len(O[d]) for d in dates)
        for kv in np.arange(0, 1501, 50):
            for rv in np.arange(0.12, 0.261, 0.01):
                sp = spear_a(O, kv, rv)
                ev, vmax, over = score_a(O, kv, rv)
                cands.append((mm, float(kv), float(rv), float(sp), ev, vmax, over, nm))
    ok_c = [c for c in cands if abs(c[3]) < 0.15]
    for c in sorted(ok_c, key=lambda c: (c[5], c[4], c[6], abs(c[3])))[:8]:
        P(f'  {c[0]:>3} m | k={c[1]:>5.0f} | ref={c[2]:.2f} | rho={c[3]:+.3f} '
          f'| err={c[4]} | max={c[5]} | over={c[6]} | merges={c[7]}')
    P('  best ignoring Spearman:')
    for c in sorted(cands, key=lambda c: (c[5], c[4], c[6]))[:5]:
        P(f'  {c[0]:>3} m | k={c[1]:>5.0f} | ref={c[2]:.2f} | rho={c[3]:+.3f} '
          f'| err={c[4]} | max={c[5]} | over={c[6]}')

    # Also show k=0 (pure axis, no sea correction) top results
    P('  best with k=0 (no sea correction):')
    k0_c = [c for c in cands if c[1] == 0 and abs(c[3]) < 0.15]
    for c in sorted(k0_c, key=lambda c: (c[5], c[4], c[6], abs(c[3])))[:5]:
        P(f'  {c[0]:>3} m | k={c[1]:>5.0f} | ref={c[2]:.2f} | rho={c[3]:+.3f} '
          f'| err={c[4]} | max={c[5]} | over={c[6]} | merges={c[7]}')

    pick = sorted(ok_c, key=lambda c: (c[5], c[4], c[6], abs(c[3])))[0] if ok_c else None
    overall_ok = False
    if pick is None:
        P('\nFIT axis: nothing satisfies |Spearman| < 0.15')
    else:
        mm2, k1, r1, s1 = pick[0], pick[1], pick[2], pick[3]
        merged_axis = {d: merge_date_axis(by[d], mm2) for d in dates}
        n_merges = {d: len(by[d]) - len(merged_axis[d]) for d in dates}
        raw_axis2 = {d: [{'n': 1, 'L': b['length_axis_m'], 'sea': b['sea_p70'],
                          'lat': b['lat'], 'lon': b['lon']} for b in by[d]] for d in dates}
        P(f'\nFIT axis: merge_m={mm2}, k_m_per_sea={k1:.0f}, sea_ref={r1:.2f}, Spearman={s1:+.3f}')
        P('\nACCEPTANCE axis vs anchorage_optical_ref — date: site now | after | optical')
        overall_ok = True
        for d in sorted(ref):
            o = ref[d]; now = daily.get(d, {})
            a = counts(merged_axis.get(d, []), k1, r1)
            b2 = counts(raw_axis2.get(d, []), k1, r1)
            cols = ('vlcc', 'suezmax', 'aframax', 'mr', 'total')
            P(f'  {d}: now ' + ' '.join(f'{c}={now.get(c)}' for c in cols) +
              f' | after ' + ' '.join(f'{c}={a[c]}' for c in cols) + f' large={a["large"]}' +
              f' | optical ' + ' '.join(f'{c}={o.get(c)}' for c in cols) +
              f' | merges={n_merges.get(d, 0)}')
            if d in acc_dates:
                v_ok = abs(a['vlcc'] - int(o['vlcc'])) <= 1
                overall_ok &= v_ok
                P(f'      VLCC ±1: {"PASS" if v_ok else "FAIL"}')
            af_ok = (o['aframax'] is None or a['aframax'] <= int(o['aframax']) + 2) and \
                    (o['mr'] is None or a['mr'] <= int(o['mr']) + 2)
            tot_ok = abs(a['total'] - b2['total']) <= n_merges.get(d, 0)
            overall_ok &= af_ok and tot_ok
            P(f'      Aframax/MR <= optical+2: {"PASS" if af_ok else "FAIL"}; '
              f'total {b2["total"]}->{a["total"]} <= merges {n_merges.get(d, 0)}: {"PASS" if tot_ok else "FAIL"}')
        P(f'\nACCEPTANCE OVERALL axis: {"PASS" if overall_ok else "FAIL"}  (dry run — nothing written)')

    # Per-blob detail 2026-10-30
    d30 = '2026-10-30'
    P(f'\n=== PER-BLOB {d30} (optical: V8 S5 A1 MR3 total17) ===')
    P('  bbox_m | axis_m | delta | thr  | sea  | class_bbox -> class_axis |   lat    /   lon')
    n_up = n_dn = n_same = 0
    for b in sorted(by.get(d30, []), key=lambda x: -x['length_bbox_m']):
        cb = classify(b['length_bbox_m']); ca = classify(b['length_axis_m'])
        delta = b['length_axis_m'] - b['length_bbox_m']
        arrow = '↑' if delta > 1 else ('↓' if delta < -1 else '=')
        if arrow == '↑': n_up += 1
        elif arrow == '↓': n_dn += 1
        else: n_same += 1
        P(f'  bbox={b["length_bbox_m"]:>6.0f} axis={b["length_axis_m"]:>6.0f} '
          f'd={delta:>+6.0f}{arrow} thr={b.get("threshold") or 0:.3f} sea={b["sea_p70"]:.3f} '
          f'{cb:>8} -> {ca:<8}  {b["lat"]:.4f}/{b["lon"]:.4f}')
    P(f'  deltas: axis>bbox={n_up} axis<bbox={n_dn} same={n_same}')
    P(f'\nClass counts {d30} (no sea correction, k=0):')
    bc = {}; ac = {}
    for b in by.get(d30, []):
        bc[classify(b['length_bbox_m'])] = bc.get(classify(b['length_bbox_m']), 0) + 1
        ac[classify(b['length_axis_m'])] = ac.get(classify(b['length_axis_m']), 0) + 1
    for cls in ('vlcc', 'suezmax', 'aframax', 'mr', 'small'):
        P(f'  {cls:>8}: bbox={bc.get(cls,0):>2} -> axis={ac.get(cls,0):>2}  '
          f'{"↑" if ac.get(cls,0)>bc.get(cls,0) else ("↓" if ac.get(cls,0)<bc.get(cls,0) else "=")}')
    P(f'  optical: V8 S5 A1 MR3  total17')

    json.dump({'axis_acceptance': 'PASS' if overall_ok else 'FAIL'},
              open(os.path.join(HERE, 'axisfit_result.json'), 'w'), indent=1)


if __name__ == '__main__':
    st = sys.argv[1] if len(sys.argv) > 1 else 'dryfit'
    report = os.path.join(HERE, f'{st}_report.txt')
    if st == 'dryfit':
        dryfit()
    elif st == 'axisfit':
        axisfit()
    open(report, 'w').write('\n'.join(out) + '\n')
