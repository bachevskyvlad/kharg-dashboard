"""
backup_ras_tanura.py — Task 0 blocker: export current Ras Tanura rows to CSV.

Writes two CSV files into backups/ras_tanura_pre_rebuild_{date}/:
  terminal_observations.csv
  terminal_volume_timeseries.csv

Exits non-zero if either table returns 0 rows (safety gate: never proceed
with a rebuild if the backup is empty).
"""

import csv
import os
import sys
import datetime

# ---------------------------------------------------------------------------
# credentials (env vars preferred; hardcoded fallbacks for CI)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

TERMINAL_ID = "ras_tanura"
DATE_TAG    = datetime.date.today().isoformat()
OUT_DIR     = f"backups/ras_tanura_pre_rebuild_{DATE_TAG}"
os.makedirs(OUT_DIR, exist_ok=True)


def _fetch_all(table, filters):
    rows = []
    offset = 0
    while True:
        q = sb.table(table).select("*")
        for col, val in filters.items():
            q = q.eq(col, val)
        batch = q.range(offset, offset + 999).execute().data
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


def main():
    results = {}

    print(f"[backup] Fetching terminal_observations for {TERMINAL_ID}...")
    obs = _fetch_all("terminal_observations", {"terminal_id": TERMINAL_ID})
    path_obs = os.path.join(OUT_DIR, "terminal_observations.csv")
    _write_csv(path_obs, obs)
    results["terminal_observations"] = len(obs)
    print(f"  {len(obs)} rows → {path_obs}")

    print(f"[backup] Fetching terminal_volume_timeseries for {TERMINAL_ID}...")
    vol = _fetch_all("terminal_volume_timeseries", {"terminal_id": TERMINAL_ID})
    path_vol = os.path.join(OUT_DIR, "terminal_volume_timeseries.csv")
    _write_csv(path_vol, vol)
    results["terminal_volume_timeseries"] = len(vol)
    print(f"  {len(vol)} rows → {path_vol}")

    print("\n[backup] Summary:")
    for table, n in results.items():
        status = "OK" if n > 0 else "EMPTY — ABORT"
        print(f"  {table}: {n} rows — {status}")

    if any(n == 0 for n in results.values()):
        print("\n[backup] ABORT: one or more tables returned 0 rows. Refusing to proceed.")
        sys.exit(1)

    print(f"\n[backup] Complete. Files in {OUT_DIR}/")
    return OUT_DIR


if __name__ == "__main__":
    main()
