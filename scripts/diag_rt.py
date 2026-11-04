"""diag_rt.py — Check terminal_observations for ras_tanura."""
import os
from supabase import create_client
from collections import defaultdict

SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# 1. How many rows in terminal_observations for ras_tanura?
print("=== terminal_observations for ras_tanura ===")
rows = []
offset = 0
while True:
    batch = (sb.table("terminal_observations")
               .select("date,tank_id,db_mean")
               .eq("terminal_id", "ras_tanura")
               .range(offset, offset + 999)
               .execute().data)
    rows.extend(batch)
    if len(batch) < 1000:
        break
    offset += 1000

print(f"Total rows: {len(rows)}")

if rows:
    dates = sorted(set(r["date"] for r in rows))
    tanks = sorted(set(r["tank_id"] for r in rows))
    print(f"Unique dates: {len(dates)}  ({dates[0]} → {dates[-1]})")
    print(f"Unique tank_ids: {len(tanks)}")
    print(f"Tank IDs: {tanks}")

    date_tank_count = defaultdict(set)
    for r in rows:
        date_tank_count[r["date"]].add(r["tank_id"])
    counts = sorted(len(v) for v in date_tank_count.values())
    print(f"\nTanks per date — min:{min(counts)}  max:{max(counts)}  median:{counts[len(counts)//2]}")

    from collections import Counter
    hist = Counter(counts)
    print("Coverage histogram:")
    for n in sorted(hist):
        print(f"  {n:3d} tanks: {hist[n]:4d} dates")
else:
    print("NO DATA in terminal_observations for ras_tanura!")

# 2. What terminals DO have data?
print("\n=== All terminals in terminal_observations ===")
all_rows = (sb.table("terminal_observations")
              .select("terminal_id")
              .limit(1000)
              .execute().data)
terminals = sorted(set(r["terminal_id"] for r in all_rows))
print(f"Terminals: {terminals}")

# 3. Current state of terminal_volume_timeseries for ras_tanura
print("\n=== terminal_volume_timeseries for ras_tanura (last 10) ===")
vol = (sb.table("terminal_volume_timeseries")
         .select("date,vol_mn_bbl")
         .eq("terminal_id", "ras_tanura")
         .eq("aggregation", "daily")
         .order("date", desc=True)
         .limit(10)
         .execute().data)
if vol:
    for r in vol:
        print(f"  {r['date']}: {r['vol_mn_bbl']} Mb")
else:
    print("  NO DATA")
print(f"Total rows in terminal_volume_timeseries for ras_tanura:")
count_rows = (sb.table("terminal_volume_timeseries")
                .select("date")
                .eq("terminal_id", "ras_tanura")
                .eq("aggregation", "daily")
                .execute().data)
print(f"  {len(count_rows)} rows")
