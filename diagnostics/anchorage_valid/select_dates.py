"""ТЗ-12 step 1: pick 30 validation dates from data/anchorage.json.

Per year 2017-2026: zero/min-total date, mean-like date, max-total date.
If the year has no zero, the min slot is replaced by a second mean-like date.
Ties -> earliest date. Deterministic.
"""
import json, os, statistics

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')


def load_records():
    with open(os.path.join(ROOT, 'data', 'anchorage.json')) as f:
        return sorted(json.load(f)['records'], key=lambda r: r['date'])


def select(records):
    out = []
    for year in range(2017, 2027):
        recs = [r for r in records if r['date'].startswith(str(year))]
        if not recs:
            continue
        mean = statistics.mean(r['total'] for r in recs)
        by_mean = sorted(recs, key=lambda r: (abs(r['total'] - mean), r['date']))
        mx = sorted(recs, key=lambda r: (-r['total'], r['date']))[0]
        zeros = [r for r in recs if r['total'] == 0]
        picks = []
        if zeros:
            picks.append(('zero', zeros[0]))
            mean_c = [r for r in by_mean if r['date'] != mx['date'] and r['date'] != zeros[0]['date']]
            picks.append(('mean', mean_c[0]))
        else:
            mean_c = [r for r in by_mean if r['date'] != mx['date']]
            picks.append(('mean', mean_c[0]))
            picks.append(('mean2', mean_c[1]))
        picks.append(('max', mx))
        for slot, r in picks:
            out.append({'year': year, 'slot': slot, 'date': r['date'],
                        'year_mean': round(mean, 2), **{k: r[k] for k in ('vlcc', 'suezmax', 'aframax', 'mr', 'total')}})
    return out


if __name__ == '__main__':
    for p in select(load_records()):
        print(f"{p['year']} {p['slot']:<5} {p['date']} total={p['total']:<3} (year_mean={p['year_mean']})")
