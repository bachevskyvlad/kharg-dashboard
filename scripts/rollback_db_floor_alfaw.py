"""
rollback_db_floor_alfaw.py — Restore original db_mean values for al_flaw tanks
that were clamped by the DB_FLOOR=-5.0 UPDATE.

Source: pre-floor pertank CSV from git commit (passed as --csv <path>).
Only restores rows where current db_mean == -5.0 AND pre-floor db_mean < -5.0.

Usage:
  python rollback_db_floor_alfaw.py --csv <pre_floor_pertank.csv>          # dry-run
  python rollback_db_floor_alfaw.py --csv <pre_floor_pertank.csv> --force  # live
"""
import os, sys, json
import requests

DRY_RUN = "--force" not in sys.argv
CSV_PATH = None
for i, a in enumerate(sys.argv):
    if a == "--csv" and i+1 < len(sys.argv):
        CSV_PATH = sys.argv[i+1]

if not CSV_PATH:
    print("ERROR: --csv <path> required"); sys.exit(1)

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
TERMINAL_ID = "al_flaw"

def sb_patch(path, params, data):
    r = requests.patch(
        f"{SB_URL}/rest/v1/{path}", params=params, json=data,
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
        timeout=60)
    if not r.ok:
        print(f"  [patch error] {r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    return r

def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    # Load pre-floor CSV (no pandas dependency)
    import csv
    rows_below_floor = {}  # (tank_id, date) -> original db_mean
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if float(row["db_mean"]) < -5.0:
                rows_below_floor[(row["tank_id"], row["date"])] = float(row["db_mean"])

    print(f"=== Al-Faw DB_FLOOR rollback ===")
    print(f"  mode: {'DRY-RUN' if DRY_RUN else 'LIVE --force'}")
    print(f"  pre-floor CSV: {CSV_PATH}")
    print(f"  rows with original db_mean < -5.0: {len(rows_below_floor)}")
    print()

    if len(rows_below_floor) == 0:
        print("Nothing to restore."); return

    # Group by tank_id and original value for batch PATCH
    # Strategy: for each tank, PATCH by (tank_id, date) — date is unique per tank
    # Supabase doesn't support multi-value OR in one PATCH, so we go per tank_id + date
    # For efficiency: per tank_id, group by db_mean value and batch-patch same values
    from collections import defaultdict
    by_tank_value = defaultdict(list)  # (tank_id, db_mean_original) -> [date, ...]
    for (tid, dt), val in rows_below_floor.items():
        by_tank_value[(tid, round(val, 6))].append(dt)

    print(f"  Distinct (tank, value) groups: {len(by_tank_value)}")

    if DRY_RUN:
        print()
        print("  Top 10 groups (tank_id, original_db_mean, n_dates):")
        for (tid, val), dates in sorted(by_tank_value.items(), key=lambda x: -len(x[1]))[:10]:
            print(f"    {tid}  db_mean={val:.4f}  n={len(dates)}")
        print()
        print("DRY-RUN — pass --force to apply.")
        return

    # Live: per tank, per original db_mean value, PATCH by date list
    total_updated = 0
    for (tid, val), dates in by_tank_value.items():
        # Supabase supports ?date=in.(d1,d2,...) with PostgREST
        date_list = ",".join(dates)
        try:
            sb_patch("terminal_observations",
                params={
                    "terminal_id": f"eq.{TERMINAL_ID}",
                    "tank_id": f"eq.{tid}",
                    "orbit_direction": "eq.DESCENDING",
                    "date": f"in.({date_list})",
                    "db_mean": "eq.-5",  # only restore the ones we clamped
                },
                data={"db_mean": val}
            )
            total_updated += len(dates)
            print(f"  {tid}  db_mean={val:.4f}  restored {len(dates)} rows")
        except Exception as e:
            print(f"  ERROR {tid} val={val}: {e}")

    print()
    print(f"Restored: {total_updated} rows")
    print("Note: run h1_db_norm_refix.py --force to recompute db_norm after restore.")


if __name__ == "__main__":
    main()
