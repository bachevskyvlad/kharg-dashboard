"""
check_terminals.py — diagnostic: row counts, date ranges, db_mean stats per terminal.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from supabase import create_client
from tank_registry import LOCATIONS

URL = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
sb  = create_client(URL, KEY)

terminals = [l for l in LOCATIONS if l["id"] != "kharg"]

HDR = f"{'Terminal':<20} {'tanks':>5} {'obs':>7} {'dates':>5} {'min_date':<12} {'max_date':<12} {'db_min':>7} {'db_max':>7} {'db_mean':>7}"
print(HDR)
print("-" * len(HDR))

issues = []

for t in terminals:
    tid     = t["id"]
    n_tanks = len(t["tanks"])

    # fetch all rows (paginate up to 10k)
    rows = []
    page_size = 1000
    offset = 0
    while True:
        r = (sb.table("terminal_observations")
               .select("date,tank_id,db_mean")
               .eq("terminal_id", tid)
               .range(offset, offset + page_size - 1)
               .execute())
        batch = r.data
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if not rows:
        print(f"{t['name']:<20} {n_tanks:>5} {'NO DATA':>7}")
        issues.append(f"  ⚠ {t['name']}: no observations at all")
        continue

    dates  = sorted(set(row["date"] for row in rows))
    dbs    = [row["db_mean"] for row in rows if row["db_mean"] is not None]
    db_min = min(dbs)
    db_max = max(dbs)
    db_avg = sum(dbs) / len(dbs)

    print(f"{t['name']:<20} {n_tanks:>5} {len(rows):>7} {len(dates):>5} {dates[0]:<12} {dates[-1]:<12} {db_min:>7.1f} {db_max:>7.1f} {db_avg:>7.1f}")

    # flag suspicious values
    if db_min < -5:
        issues.append(f"  ⚠ {t['name']}: db_min={db_min:.1f} (very low, check outliers)")
    if db_max > 25:
        issues.append(f"  ⚠ {t['name']}: db_max={db_max:.1f} (very high, likely noise)")
    if len(dates) < 5:
        issues.append(f"  ⚠ {t['name']}: only {len(dates)} dates — backfill may be incomplete")

    # check expected tank coverage on latest date
    latest_date   = dates[-1]
    latest_tanks  = set(row["tank_id"] for row in rows if row["date"] == latest_date)
    expected_ids  = set(tank["id"] for tank in t["tanks"])
    missing       = expected_ids - latest_tanks
    if missing:
        issues.append(f"  ⚠ {t['name']} on {latest_date}: missing {len(missing)} tanks: {sorted(missing)[:5]}...")

print()
if issues:
    print("ISSUES FOUND:")
    for i in issues:
        print(i)
else:
    print("All terminals look healthy.")
