"""
apply_db_floor_alfaw.py — Apply DB_FLOOR = -5.0 to al_flaw terminal_observations.
Retroactive UPDATE: sets db_mean = -5.0 where db_mean < -5.0 for terminal_id='al_flaw'.

Uses Supabase REST PATCH (partial update by filter).

Usage:
  python apply_db_floor_alfaw.py          # dry-run: count rows affected
  python apply_db_floor_alfaw.py --force  # live UPDATE
"""
import os, sys
import requests

DRY_RUN = "--force" not in sys.argv

SB_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or ""

TERMINAL_ID = "al_flaw"
ORBIT_DIR   = "DESCENDING"
DB_FLOOR    = -5.0
PAGE_SIZE   = 1000


def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def sb_patch(path, params, data):
    r = requests.patch(
        f"{SB_URL}/rest/v1/{path}", params=params, json=data,
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json",
                 "Prefer": "return=representation"},
        timeout=60)
    if not r.ok:
        print(f"  [patch error] {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return r.json()


def count_below_floor():
    """Count rows with db_mean < -5.0."""
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations", {
            "terminal_id": f"eq.{TERMINAL_ID}",
            "orbit_direction": f"eq.{ORBIT_DIR}",
            "db_mean": f"lt.{DB_FLOOR}",
            "select": "date,tank_id,db_mean",
            "order": "date.asc",
            "offset": str(offset), "limit": str(PAGE_SIZE)
        })
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    print(f"=== Al-Faw DB_FLOOR={DB_FLOOR} retroactive UPDATE ===")
    print(f"  mode: {'DRY-RUN' if DRY_RUN else 'LIVE --force'}")
    print()

    print("[1] Counting rows with db_mean < -5.0 …")
    rows = count_below_floor()
    print(f"  Found: {len(rows)} rows across {len(set(r['tank_id'] for r in rows))} tanks")

    if len(rows) == 0:
        print("  Nothing to do.")
        return

    # Per-tank breakdown
    from collections import Counter
    per_tank = Counter(r["tank_id"] for r in rows)
    print("  Per-tank count (top 10):")
    for tid, n in sorted(per_tank.items(), key=lambda x: -x[1])[:10]:
        print(f"    {tid}: {n}")
    print()

    if DRY_RUN:
        print("DRY-RUN — pass --force to apply UPDATE.")
        return

    # Supabase REST doesn't support bulk conditional PATCH across all rows in one shot
    # without PATCH /table?filter — but it does support it with Prefer: return=minimal
    print("[2] Applying UPDATE db_mean = -5.0 where db_mean < -5.0 …")

    # Use PATCH with filter (applies to all matching rows at once)
    result = sb_patch(
        "terminal_observations",
        params={
            "terminal_id": f"eq.{TERMINAL_ID}",
            "orbit_direction": f"eq.{ORBIT_DIR}",
            "db_mean": f"lt.{DB_FLOOR}",
        },
        data={"db_mean": DB_FLOOR}
    )

    updated = len(result)
    print(f"  Updated: {updated} rows (db_mean set to {DB_FLOOR})")
    print()

    # Verify: re-count
    print("[3] Verification: re-count rows with db_mean < -5.0 …")
    remaining = count_below_floor()
    print(f"  Remaining below floor: {len(remaining)}")
    if len(remaining) == 0:
        print("  [OK] All rows at or above floor.")
    else:
        print(f"  [WARN] {len(remaining)} rows still below floor — may need another pass")
    print()
    print(f"Done. {updated} rows updated to db_mean={DB_FLOOR}.")
    print("Note: db_norm is NOT recomputed here — run h1_db_norm_refix.py --force after.")


if __name__ == "__main__":
    main()
