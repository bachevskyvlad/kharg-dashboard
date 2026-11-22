"""ТЗ-12.3 export_static — rebuild data/anchorage.json from anchorage_queue.

Reads all rows from the anchorage_queue Supabase table and writes
data/anchorage.json in 3-class schema:
  {updated, records:[{date, total, vlcc, suezaframax, small, arrived, departed}]}

If anchorage_queue is empty or unreachable, the script exits non-zero (FATAL).
data/anchorage.json is NEVER computed here; it only mirrors the DB.
"""
import os, sys, json
from datetime import datetime, timezone

import requests

SB_URL = os.environ.get('SUPABASE_URL', '')
SB_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
OUT    = os.path.join(ROOT, 'data', 'anchorage.json')


def fatal(msg):
    print(f'FATAL: {msg}', flush=True)
    sys.exit(1)


def log(*a):
    print(*a, flush=True)


def sb_headers():
    if not SB_KEY:
        fatal('SUPABASE_SERVICE_KEY is empty')
    return {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
            'Content-Type': 'application/json'}


def fetch_all():
    h = sb_headers()
    rows, off = [], 0
    while True:
        r = requests.get(
            f'{SB_URL}/rest/v1/anchorage_queue',
            params={'select': 'date,total,vlcc,suezaframax,small,arrived,departed',
                    'order': 'date.asc', 'limit': 1000, 'offset': off},
            headers=h, timeout=60)
        if r.status_code != 200:
            fatal(f'anchorage_queue fetch failed: {r.status_code} {r.text[:300]}')
        batch = r.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        off += 1000
    return rows


def main():
    if not SB_URL:
        fatal('SUPABASE_URL is empty')

    log('export_static: fetching anchorage_queue …')
    rows = fetch_all()

    if not rows:
        fatal('anchorage_queue is empty — nothing to export')

    records = []
    for r in rows:
        records.append({
            'date':        r['date'],
            'total':       int(r.get('total')       or 0),
            'vlcc':        int(r.get('vlcc')        or 0),
            'suezaframax': int(r.get('suezaframax') or 0),
            'small':       int(r.get('small')       or 0),
            'arrived':     int(r.get('arrived')     or 0),
            'departed':    int(r.get('departed')    or 0),
        })

    out = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'records': records,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(out, f, separators=(',', ':'))

    log(f'export_static: wrote {len(records)} records → {OUT}')


if __name__ == '__main__':
    main()
