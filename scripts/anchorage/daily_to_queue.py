"""Sync anchorage_queue from anchorage_daily + anchorage_ships.

anchorage_daily  → 5-class SAR counts  (vlcc, suezmax, aframax, mr, small)
anchorage_ships  → per-ship lat/lon/length_m per date
anchorage_queue  → 3-class counts + arrived/departed flow

Mapping:
  vlcc        = vlcc
  suezaframax = suezmax + aframax
  small       = mr + small
  total       = vlcc + suezaframax + small
  arrived     = ships in current date NOT matched in previous date
  departed    = ships in previous date NOT matched in current date

Ship matching: haversine distance < MATCH_DIST_M (same as tracks.py).

Usage:
  python scripts/anchorage/daily_to_queue.py --dry-run   # show rows, no write
  python scripts/anchorage/daily_to_queue.py             # upsert to anchorage_queue
"""
import os, sys, json, math, argparse
from datetime import datetime, timezone

import requests

SB_URL = os.environ.get('SUPABASE_URL', '')
SB_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
MATCH_DIST_M = 1500   # metres — same threshold as tracks.py


def fatal(msg):
    print(f'FATAL: {msg}', flush=True)
    sys.exit(1)


def log(*a):
    print(*a, flush=True)


def headers():
    if not SB_KEY:
        fatal('SUPABASE_SERVICE_KEY is empty')
    return {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
            'Content-Type': 'application/json'}


def sb_get(table, params=''):
    r = requests.get(f'{SB_URL}/rest/v1/{table}?{params}',
                     headers=headers(), timeout=30)
    if r.status_code != 200:
        fatal(f'{table} fetch failed: {r.status_code} {r.text[:300]}')
    return r.json()


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def match_count(prev_ships, curr_ships):
    """Return (n_arrived, n_departed) based on MATCH_DIST_M threshold."""
    used_prev = set()
    matched_curr = set()
    for ci, cs in enumerate(curr_ships):
        for pi, ps in enumerate(prev_ships):
            if pi in used_prev:
                continue
            d = haversine_m(float(ps['lat']), float(ps['lon']),
                            float(cs['lat']), float(cs['lon']))
            if d <= MATCH_DIST_M:
                used_prev.add(pi)
                matched_curr.add(ci)
                break
    n_matched  = len(matched_curr)
    n_departed = len(prev_ships) - len(used_prev)
    n_arrived  = len(curr_ships) - n_matched
    return n_arrived, n_departed


def get_all_dates_daily():
    rows = sb_get('anchorage_daily', 'select=date&order=date.asc&limit=5000')
    return [r['date'] for r in rows]


def get_all_dates_queue():
    rows = sb_get('anchorage_queue', 'select=date&order=date.asc&limit=5000')
    return {r['date'] for r in rows}


def get_daily_row(d):
    rows = sb_get('anchorage_daily',
                  f'select=date,total,vlcc,suezmax,aframax,mr,small&date=eq.{d}&limit=1')
    return rows[0] if rows else None


def get_ships(d):
    rows = sb_get('anchorage_ships',
                  f'select=lat,lon,length_m&date=eq.{d}&limit=5000')
    return rows


def build_queue_row(d, daily_row, prev_ships, curr_ships):
    """Convert anchorage_daily row + ships to anchorage_queue row."""
    vlcc        = int(daily_row.get('vlcc')    or 0)
    suezaframax = int(daily_row.get('suezmax') or 0) + int(daily_row.get('aframax') or 0)
    small       = int(daily_row.get('mr')      or 0) + int(daily_row.get('small')   or 0)
    total       = vlcc + suezaframax + small

    if prev_ships is None:
        arrived  = 0
        departed = 0
    else:
        arrived, departed = match_count(prev_ships, curr_ships)

    return {
        'date':        d,
        'total':       total,
        'vlcc':        vlcc,
        'suezaframax': suezaframax,
        'small':       small,
        'arrived':     arrived,
        'departed':    departed,
    }


def upsert_queue(row):
    r = requests.post(
        f'{SB_URL}/rest/v1/anchorage_queue?on_conflict=date',
        json=[row],
        headers={**headers(), 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        timeout=60)
    if r.status_code >= 300:
        fatal(f'upsert anchorage_queue {row["date"]}: {r.status_code} {r.text[:300]}')


def main():
    if not SB_URL:
        fatal('SUPABASE_URL is empty')

    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='print what would be upserted, do not write')
    args = ap.parse_args()

    log('daily_to_queue: fetching date lists …')
    all_daily  = get_all_dates_daily()
    have_queue = get_all_dates_queue()
    new_dates  = [d for d in all_daily if d not in have_queue]
    log(f'  anchorage_daily: {len(all_daily)} dates, last={all_daily[-1] if all_daily else "—"}')
    log(f'  anchorage_queue: {len(have_queue)} dates, last={max(have_queue) if have_queue else "—"}')
    log(f'  to sync: {new_dates}')

    if not new_dates:
        log('daily_to_queue: nothing to do')
        return

    # Pre-fetch ships for the date just before the first new date (for arrived/departed)
    all_sorted = sorted(all_daily)
    rows_written = []
    prev_ships = None
    prev_date  = None

    for d in sorted(new_dates):
        idx = all_sorted.index(d)
        if idx > 0 and prev_date != all_sorted[idx - 1]:
            prev_date  = all_sorted[idx - 1]
            prev_ships = get_ships(prev_date)
            log(f'  ships for prev={prev_date}: {len(prev_ships)}')

        daily_row = get_daily_row(d)
        if daily_row is None:
            log(f'  {d}: no row in anchorage_daily — skip')
            prev_date  = d
            prev_ships = []
            continue

        curr_ships = get_ships(d)
        log(f'  ships for {d}: {len(curr_ships)}')

        row = build_queue_row(d, daily_row, prev_ships, curr_ships)
        rows_written.append(row)
        log(f'  {d}: total={row["total"]} vlcc={row["vlcc"]} suezaframax={row["suezaframax"]} '
            f'small={row["small"]} arrived={row["arrived"]} departed={row["departed"]}')

        prev_date  = d
        prev_ships = curr_ships

    if args.dry_run:
        log(f'\nDRY-RUN: would upsert {len(rows_written)} rows to anchorage_queue:')
        for r in rows_written:
            log(f'  {json.dumps(r)}')
        log('No data written.')
        return

    for row in rows_written:
        upsert_queue(row)
        log(f'  upserted {row["date"]}')

    log(f'daily_to_queue: done — upserted {len(rows_written)} rows')


if __name__ == '__main__':
    main()
