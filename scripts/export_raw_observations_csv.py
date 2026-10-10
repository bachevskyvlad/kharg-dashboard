import csv
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

all_rows = []
page_size = 1000
offset = 0
while True:
    rows = (sb.table("raw_observations")
              .select("*")
              .order("date")
              .order("tank_id")
              .range(offset, offset + page_size - 1)
              .execute().data)
    all_rows.extend(rows)
    print(f"  fetched {len(all_rows)} rows...", flush=True)
    if len(rows) < page_size:
        break
    offset += page_size

print(f"Total: {len(all_rows)} rows")

if not all_rows:
    print("No data found.")
    raise SystemExit(1)

keys = list(all_rows[0].keys())
out_path = "public/kharg_raw_observations.csv"
with open(out_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(all_rows)

print(f"Saved → {out_path}  ({len(all_rows)} rows, {len(keys)} columns)")
print(f"Columns: {', '.join(keys)}")
