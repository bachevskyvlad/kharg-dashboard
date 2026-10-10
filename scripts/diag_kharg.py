"""diag_kharg.py — One-time diagnostics: coverage, dB calibration, recent volumes."""
import os, statistics
from supabase import create_client
from collections import Counter, defaultdict

SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""
if not SUPABASE_URL or not SUPABASE_KEY:
    import sys; sys.exit("FATAL: SUPABASE_URL and SUPABASE_KEY env vars must be set")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Fetching raw_observations ...")
rows = []
offset = 0
while True:
    batch = (sb.table("raw_observations")
               .select("date,tank_id,db_mean")
               .range(offset, offset + 999)
               .execute().data)
    rows.extend(batch)
    if len(batch) < 1000:
        break
    offset += 1000
print(f"Total rows: {len(rows)}")

date_tanks = defaultdict(set)
for r in rows:
    date_tanks[r["date"]].add(r["tank_id"])

date_counts = {d: len(t) for d, t in date_tanks.items()}
all_counts = sorted(date_counts.values())
all_tanks = set()
for t in date_tanks.values():
    all_tanks |= t

print(f"\n=== TANK INVENTORY ===")
print(f"Total unique tank_ids: {len(all_tanks)}")
print(f"IDs: {sorted(all_tanks)}")

print(f"\n=== COVERAGE ===")
print(f"Total dates: {len(date_counts)}")
print(f"Min: {min(all_counts)}  Max: {max(all_counts)}  Median: {statistics.median(all_counts)}")

print(f"\n=== 20 WORST COVERAGE DATES ===")
for d, n in sorted(date_counts.items(), key=lambda x: x[1])[:20]:
    print(f"  {d}: {n} tanks")

print(f"\n=== COVERAGE HISTOGRAM ===")
hist = Counter(all_counts)
for n in sorted(hist.keys()):
    print(f"  {n:3d} tanks: {hist[n]:4d} dates")

print(f"\n=== dB STATS PER TANK ===")
tank_db = defaultdict(list)
for r in rows:
    tank_db[r["tank_id"]].append(r["db_mean"])

tank_stats = sorted(
    [{"id": tid, "n": len(dbs), "mean": round(sum(dbs)/len(dbs), 2),
      "min": round(min(dbs), 2), "max": round(max(dbs), 2)}
     for tid, dbs in tank_db.items()],
    key=lambda x: x["mean"]
)
print("FULLEST (lowest mean dB):")
for t in tank_stats[:10]:
    print(f"  {t['id']:10s}  mean={t['mean']:6.2f}  min={t['min']:5.2f}  max={t['max']:5.2f}  n={t['n']}")
print("EMPTIEST (highest mean dB):")
for t in reversed(tank_stats[-10:]):
    print(f"  {t['id']:10s}  mean={t['mean']:6.2f}  min={t['min']:5.2f}  max={t['max']:5.2f}  n={t['n']}")

print(f"\n=== GLOBAL dB PERCENTILES ===")
all_db = sorted(r["db_mean"] for r in rows)
n = len(all_db)
p05 = all_db[int(n * 0.05)]
p95 = all_db[int(n * 0.95)]
print(f"  5th  percentile (dB_min = fill=1 / tank 'full'):   {p05:.3f} dB")
print(f"  95th percentile (dB_max = fill=0 / tank 'empty'):  {p95:.3f} dB")

print(f"\n=== LAST 20 DAILY VOLUMES (volume_timeseries) ===")
vol_rows = (sb.table("volume_timeseries")
              .select("date,vol_mn_bbl")
              .eq("aggregation", "daily")
              .eq("smoothed", False)
              .order("date", desc=True)
              .limit(20)
              .execute().data)
for r in vol_rows:
    print(f"  {r['date']}: {r['vol_mn_bbl']} Mb")
