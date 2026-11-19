"""
gulf_exit_pipeline.py — Aggregate corridor + irlane + jask results into
Supabase table gulf_exit_passes.

Columns: date, zone, large, vlcc, moving, stay
  zone: 'corridor' | 'irlane' | 'jask'
  jask uses: large=terminal_n, vlcc=terminal_vlcc, moving=0, stay=anchorage_n

Usage:
  python gulf_exit_pipeline.py --incremental   # last 14 days
  python gulf_exit_pipeline.py --backfill      # load from existing CSVs
"""

import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

SUPABASE_URL         = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""
TABLE = "gulf_exit_passes"

if not SUPABASE_URL:
    print("FATAL: SUPABASE_URL not set", file=sys.stderr); sys.exit(1)
if not SUPABASE_SERVICE_KEY:
    print("FATAL: SUPABASE_SERVICE_KEY not set", file=sys.stderr); sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR  = REPO_ROOT / "data"
DIAG_DIR  = REPO_ROOT / "diagnostics"

# CSV sources (produced by existing pipeline jobs)
CORRIDOR_CSV = DATA_DIR / "hormuz_corridor_ships.csv"
JASK_CSV     = DATA_DIR / "jask_ship_obs.csv"
IRLANE_CSV   = DIAG_DIR / "irlane_ships.csv"


def _sb_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def _upsert_rows(rows):
    if not rows:
        return
    batch = 500
    for i in range(0, len(rows), batch):
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{TABLE}",
            headers=_sb_headers(),
            json=rows[i:i + batch],
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"  DB error {r.status_code}: {r.text[:300]}")
        else:
            print(f"  upserted {len(rows[i:i+batch])} rows")


def _fetch_existing(zone):
    """Return set of dates already in DB for given zone."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?select=date&zone=eq.{zone}&order=date.asc",
        headers={**_sb_headers(), "Prefer": ""},
        timeout=30,
    )
    if r.status_code == 200:
        return {row["date"] for row in r.json()}
    return set()


def _load_corridor_csv(path, since=None):
    rows = []
    if not path.exists():
        print(f"  missing {path}"); return rows
    with open(path) as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if since and d < since:
                continue
            rows.append({
                "date":   d,
                "zone":   "corridor",
                "large":  int(row.get("vlcc", 0)) + int(row.get("mid", 0)),
                "vlcc":   int(row.get("vlcc", 0)),
                "moving": 0,
                "stay":   0,
            })
    return rows


def _load_irlane_csv(path, since=None):
    rows = []
    if not path.exists():
        print(f"  missing {path}"); return rows
    with open(path) as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if since and d < since:
                continue
            rows.append({
                "date":   d,
                "zone":   "irlane",
                "large":  int(row.get("large", 0)),
                "vlcc":   int(row.get("vlcc", 0)),
                "moving": int(row.get("moving", 0)),
                "stay":   int(row.get("stay", 0)),
            })
    return rows


def _load_jask_csv(path, since=None):
    rows = []
    if not path.exists():
        print(f"  missing {path}"); return rows
    with open(path) as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if since and d < since:
                continue
            rows.append({
                "date":   d,
                "zone":   "jask",
                "large":  int(row.get("terminal_n", 0)),
                "vlcc":   int(row.get("terminal_vlcc", 0)),
                "moving": 0,
                "stay":   int(row.get("anchorage_n", 0)),
            })
    return rows


def run_backfill():
    """Load all three CSVs and upsert everything from 2025-01-01."""
    since = "2025-01-01"
    rows = (
        _load_corridor_csv(CORRIDOR_CSV, since) +
        _load_irlane_csv(IRLANE_CSV, since) +
        _load_jask_csv(JASK_CSV, since)
    )
    print(f"Backfill: {len(rows)} rows total")
    _upsert_rows(rows)


def run_incremental():
    """Load from CSVs only dates not already in DB (last 14 days window)."""
    since = (date.today() - timedelta(days=14)).isoformat()

    for zone, loader, path in [
        ("corridor", _load_corridor_csv, CORRIDOR_CSV),
        ("irlane",   _load_irlane_csv,   IRLANE_CSV),
        ("jask",     _load_jask_csv,      JASK_CSV),
    ]:
        existing = _fetch_existing(zone)
        candidates = loader(path, since)
        new_rows = [r for r in candidates if r["date"] not in existing]
        print(f"{zone}: {len(new_rows)} new rows (of {len(candidates)} in window)")
        _upsert_rows(new_rows)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--incremental", action="store_true")
    g.add_argument("--backfill",    action="store_true")
    args = p.parse_args()

    if args.backfill:
        run_backfill()
    else:
        run_incremental()
