"""
fix_db_mean_floor.py — One-time fix: clip db_mean < -5.0 in Supabase.

SAR VV backscatter physically cannot be below ~-5 dB for liquid surfaces.
Values below this are geometric artifacts (shadow zones, multipath) that
skew the global 5th-percentile used in compute_volumes(), causing systematic
volume underestimation across all terminals.

Uses a single bulk UPDATE per table (Supabase REST supports .lt() filter on update).
Safe to re-run: rows already at >= -5.0 are untouched.
"""

import os, sys
from config import sb

DB_FLOOR = -5.0

def count_bad(table):
    return sb.table(table).select("id", count="exact").lt("db_mean", DB_FLOOR).execute().count

def fix_table(table):
    print(f"\n{'='*60}")
    print(f"Table: {table}")
    before = count_bad(table)
    print(f"  Rows with db_mean < {DB_FLOOR}: {before}")
    if before == 0:
        print("  Nothing to fix.")
        return 0

    # Bulk UPDATE: SET db_mean = -5.0 WHERE db_mean < -5.0
    result = sb.table(table).update({"db_mean": DB_FLOOR}).lt("db_mean", DB_FLOOR).execute()
    print(f"  UPDATE executed. Rows returned: {len(result.data) if result.data else '?'}")

    after = count_bad(table)
    fixed = before - after
    print(f"  After fix — still bad: {after}  |  fixed: {fixed}")
    return fixed

total = 0
total += fix_table("raw_observations")
total += fix_table("terminal_observations")

print(f"\n{'='*60}")
print(f"Total fixed: {total} rows")
print("Next: re-run compute.py + export.py to regenerate volumes.")
