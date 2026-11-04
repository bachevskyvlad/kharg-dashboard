"""
backup_saudi.py — Stage 0: export current rows to CSV before any rebuild.

Writes two CSV files into backups/saudi_pre_rebuild_{date}/{terminal_id}/:
  terminal_observations.csv
  terminal_volume_timeseries.csv

Exits non-zero if either table returns 0 rows (safety gate).

Usage:
  python scripts/backup_saudi.py --terminal ras_tanura
  python scripts/backup_saudi.py --terminal juwaymah
  python scripts/backup_saudi.py --terminal yanbu
  python scripts/backup_saudi.py --terminal ras_tanura,juwaymah,yanbu
"""

import argparse
import csv
import datetime
import os
import sys

SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""

SAUDI_TERMINALS = {"ras_tanura", "juwaymah", "yanbu"}

DATE_TAG = datetime.date.today().isoformat()


def _fetch_all(sb, table, terminal_id):
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table(table)
            .select("*")
            .eq("terminal_id", terminal_id)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def backup_terminal(sb, terminal_id, out_root):
    out_dir = os.path.join(out_root, terminal_id)
    os.makedirs(out_dir, exist_ok=True)

    results = {}

    print(f"[backup:{terminal_id}] Fetching terminal_observations ...")
    obs = _fetch_all(sb, "terminal_observations", terminal_id)
    path_obs = os.path.join(out_dir, "terminal_observations.csv")
    _write_csv(path_obs, obs)
    results["terminal_observations"] = len(obs)
    print(f"  {len(obs)} rows → {path_obs}")

    print(f"[backup:{terminal_id}] Fetching terminal_volume_timeseries ...")
    vol = _fetch_all(sb, "terminal_volume_timeseries", terminal_id)
    path_vol = os.path.join(out_dir, "terminal_volume_timeseries.csv")
    _write_csv(path_vol, vol)
    results["terminal_volume_timeseries"] = len(vol)
    print(f"  {len(vol)} rows → {path_vol}")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--terminal", required=True,
        help="Comma-separated terminal IDs (ras_tanura, juwaymah, yanbu) or 'all'",
    )
    args = parser.parse_args()

    if args.terminal == "all":
        terminal_ids = sorted(SAUDI_TERMINALS)
    else:
        terminal_ids = [t.strip() for t in args.terminal.split(",")]

    for tid in terminal_ids:
        if tid not in SAUDI_TERMINALS:
            print(f"ABORT: '{tid}' is not a Saudi terminal. "
                  f"Allowed: {sorted(SAUDI_TERMINALS)}")
            sys.exit(1)

    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    out_root = f"backups/saudi_pre_rebuild_{DATE_TAG}"
    os.makedirs(out_root, exist_ok=True)

    all_results = {}
    for tid in terminal_ids:
        results = backup_terminal(sb, tid, out_root)
        all_results[tid] = results

    print("\n[backup] Summary:")
    any_empty = False
    for tid, results in all_results.items():
        for table, n in results.items():
            status = "OK" if n > 0 else "EMPTY — ABORT"
            print(f"  {tid}/{table}: {n} rows — {status}")
            if n == 0:
                any_empty = True

    if any_empty:
        print("\n[backup] ABORT: one or more tables returned 0 rows.")
        sys.exit(1)

    print(f"\n[backup] Complete. Files in {out_root}/")


if __name__ == "__main__":
    main()
