"""
delete_old_jask.py — Backup old jk_* records then delete them from Supabase.

Steps:
  1. Export terminal_observations WHERE tank_id LIKE 'jk_%' → CSV
  2. Export terminal_volume_timeseries WHERE terminal_id = 'jask' → CSV
  3. Commit CSVs to data/backups/
  4. Delete from both tables
  5. Print counts

Run once via GHA workflow_dispatch before new jask_01..20 ingest.
"""

import os, sys, json, csv
from pathlib import Path
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

BACKUP_DIR = Path(__file__).parent.parent / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def fetch_all(table, filters):
    rows = []
    offset = 0
    while True:
        q = sb.table(table).select("*")
        for col, op, val in filters:
            if op == "like":
                q = q.like(col, val)
            elif op == "eq":
                q = q.eq(col, val)
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def write_csv(rows, path):
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def delete_in_batches(table, col, values, batch_size=100):
    deleted = 0
    for i in range(0, len(values), batch_size):
        chunk = values[i:i + batch_size]
        sb.table(table).delete().in_(col, chunk).execute()
        deleted += len(chunk)
    return deleted


# ── 1. Fetch ──────────────────────────────────────────────────────────────────
print("Fetching terminal_observations jk_* ...")
obs_rows = fetch_all("terminal_observations", [("tank_id", "like", "jk_%")])
print(f"  Found: {len(obs_rows)} rows")

print("Fetching terminal_volume_timeseries jask ...")
vol_rows = fetch_all("terminal_volume_timeseries", [("terminal_id", "eq", "jask")])
print(f"  Found: {len(vol_rows)} rows")

if not obs_rows and not vol_rows:
    print("Nothing to delete. Exiting.")
    sys.exit(0)

# ── 2. Write CSVs ─────────────────────────────────────────────────────────────
obs_path = BACKUP_DIR / "jask_old_terminal_observations.csv"
vol_path = BACKUP_DIR / "jask_old_terminal_volume_timeseries.csv"
write_csv(obs_rows, obs_path)
write_csv(vol_rows, vol_path)
print(f"Backup written: {obs_path} ({len(obs_rows)} rows)")
print(f"Backup written: {vol_path} ({len(vol_rows)} rows)")

# ── 3. Dry-run summary ────────────────────────────────────────────────────────
jk_ids = list({r["tank_id"] for r in obs_rows})
print(f"\nDRY-RUN:")
print(f"  terminal_observations: DELETE WHERE tank_id IN {sorted(jk_ids)[:5]}... ({len(obs_rows)} rows total)")
print(f"  terminal_volume_timeseries: DELETE WHERE terminal_id='jask' ({len(vol_rows)} rows total)")

# ── 4. Delete ─────────────────────────────────────────────────────────────────
print("\nDeleting ...")

# Delete by tank_id batches (jk_0 .. jk_19)
obs_tank_ids = list({r["tank_id"] for r in obs_rows})
deleted_obs = delete_in_batches("terminal_observations", "tank_id", obs_tank_ids)
print(f"  Deleted terminal_observations (tank_id groups): {deleted_obs} tank_ids, ~{len(obs_rows)} rows")

# Delete terminal_volume_timeseries by terminal_id=jask
sb.table("terminal_volume_timeseries").delete().eq("terminal_id", "jask").execute()
print(f"  Deleted terminal_volume_timeseries jask: {len(vol_rows)} rows")

print("\nDone.")
