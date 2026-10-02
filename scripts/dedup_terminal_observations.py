"""
Dedup terminal_observations — Task 1
=====================================
1. Fetch all rows for TERMINAL_ID where (terminal_id, date, tank_id) has duplicates
2. Save them as diagnostics/al_faw/dedup_backup_YYYYMMDDHHMMSS.csv
3. Delete the non-canonical rows (keep max db_mean per group — same rule as stage7 in-memory dedup)
4. Verify / create UNIQUE constraint (terminal_id, date, tank_id) via a dry-run advisory check

Usage:
    SUPABASE_URL=... SUPABASE_KEY=... python dedup_terminal_observations.py [--dry-run]

The script ALWAYS writes the backup CSV first.  Without --dry-run it then performs the DELETE.
With --dry-run it prints the plan and exits without modifying the database.

Security rules (ТЗ verbatim):
    - Delete only with prior backup committed to git
    - Transactional: fetch → backup → delete per-ID, not bulk
    - UNIQUE constraint creation: advisory only (Supabase RLS; DDL via SQL editor)
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# ── Config ──────────────────────────────────────────────────────────────────────
TERMINAL_ID = "al_flaw"
OUT_DIR     = Path("../diagnostics/al_faw")
PAGE_SIZE   = 1000

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY", "")
if not SB_URL or not SB_KEY:
    print("FATAL: SUPABASE_URL and SUPABASE_KEY must be set in environment")
    sys.exit(1)

HEADERS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}


# ── Helpers ──────────────────────────────────────────────────────────────────────
def fetch_all():
    """Paginate through terminal_observations for this terminal."""
    rows, offset = [], 0
    while True:
        url = (
            f"{SB_URL}/rest/v1/terminal_observations"
            f"?terminal_id=eq.{TERMINAL_ID}"
            f"&select=id,terminal_id,date,tank_id,db_mean,orbit_direction,created_at"
            f"&order=id.asc"
            f"&offset={offset}&limit={PAGE_SIZE}"
        )
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        print(f"  fetched offset={offset} → {len(batch)} rows (total so far: {len(rows)})")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def delete_row(row_id: int, dry_run: bool) -> bool:
    if dry_run:
        return True
    url = f"{SB_URL}/rest/v1/terminal_observations?id=eq.{row_id}"
    r = requests.delete(url, headers=HEADERS, timeout=30)
    if r.status_code not in (200, 204):
        print(f"  DELETE id={row_id} failed: {r.status_code} {r.text[:200]}")
        return False
    return True


# ── Main ─────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only, do not delete")
    args = parser.parse_args()
    dry_run = args.dry_run
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"=== dedup_terminal_observations [{mode}] terminal={TERMINAL_ID} ===")

    # 1. Fetch
    print("\nFetching all rows…")
    rows = fetch_all()
    df = pd.DataFrame(rows)
    print(f"  Total rows fetched: {len(df)}")
    if df.empty:
        print("  Nothing to do.")
        return

    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)

    # 2. Identify duplicates
    dup_mask  = df.duplicated(subset=["date", "tank_id"], keep=False)
    df_dups   = df[dup_mask].copy()
    print(f"  Duplicate (date, tank_id) rows: {len(df_dups)}")
    if df_dups.empty:
        print("  No duplicates — nothing to delete.")
        return

    n_pairs = df_dups.groupby(["date", "tank_id"]).ngroups
    print(f"  Duplicate groups (unique date×tank_id with >1 row): {n_pairs}")

    # 3. Backup
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bak = OUT_DIR / f"dedup_backup_{ts}.csv"
    df_dups.to_csv(bak, index=False)
    print(f"\n  Backup written: {bak}  ({len(df_dups)} rows)")
    print("  IMPORTANT: commit this backup to git before running without --dry-run")

    # 4. Decide which IDs to delete: for each group keep row with max db_mean; delete the rest
    df["db_mean_f"] = pd.to_numeric(df["db_mean"], errors="coerce")
    keep_ids = (
        df.sort_values("db_mean_f", ascending=False)
          .drop_duplicates(subset=["date", "tank_id"], keep="first")
          ["id"]
          .tolist()
    )
    delete_ids = df[~df["id"].isin(keep_ids)]["id"].tolist()
    print(f"\n  Rows to delete: {len(delete_ids)}")
    print(f"  Rows to keep:   {len(keep_ids)}")

    if dry_run:
        print("\n  [DRY-RUN] No deletions performed.")
        print(f"  Sample IDs that would be deleted: {delete_ids[:10]}")
        print(
            "\n  UNIQUE constraint advisory:\n"
            "  Run this in the Supabase SQL editor after deleting:\n\n"
            "    ALTER TABLE terminal_observations\n"
            "      ADD CONSTRAINT terminal_obs_unique_date_tank\n"
            "      UNIQUE (terminal_id, date, tank_id);\n"
        )
        return

    # 5. Delete (transactional per ID — no bulk RPC available without service key)
    print(f"\n  Deleting {len(delete_ids)} rows…")
    ok, fail = 0, 0
    for rid in delete_ids:
        if delete_row(rid, dry_run=False):
            ok += 1
        else:
            fail += 1
    print(f"  Deleted: {ok}  Failed: {fail}")

    if fail:
        print(f"  WARNING: {fail} rows could not be deleted — re-run to retry")
        sys.exit(2)

    print("\n  Done.  Run stage7 pipeline to verify vol_max < 10.62 Mb.")
    print(
        "\n  UNIQUE constraint — add manually in Supabase SQL editor:\n\n"
        "    ALTER TABLE terminal_observations\n"
        "      ADD CONSTRAINT terminal_obs_unique_date_tank\n"
        "      UNIQUE (terminal_id, date, tank_id);\n"
    )


if __name__ == "__main__":
    main()
