"""
migrate_add_db_norm.py — Add db_norm column to terminal_observations via Supabase management API.
Run once before stage_h1_db_norm.py --force.
"""
import os, sys, requests

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

# Use the REST API with service role key to run DDL via rpc or direct SQL
# Supabase exposes /rest/v1/rpc/... — but DDL needs the pg connection or management API.
# We'll use the Supabase Management API v1 (requires personal access token), OR
# try the sql endpoint if available.

# Alternative: use the pg REST endpoint via PostgREST — but PostgREST doesn't support DDL.
# Best approach: use supabase management API to run a migration.

PROJECT_REF = SB_URL.replace("https://", "").split(".")[0]  # e.g. yoekhkczwdeyauwarsyy
MGMT_KEY = os.environ.get("SUPABASE_ACCESS_TOKEN")  # personal access token

if not MGMT_KEY:
    print("SUPABASE_ACCESS_TOKEN not set — trying service role key for RPC exec")
    # Some Supabase projects expose pg_execute or exec_sql via custom RPC
    # Try the standard approach: upsert a dummy row to see if column exists
    r = requests.get(
        f"{SB_URL}/rest/v1/terminal_observations",
        params={"select": "db_norm", "limit": "1"},
        headers={"apikey": SB_SERVICE_KEY, "Authorization": f"Bearer {SB_SERVICE_KEY}"},
        timeout=10
    )
    if r.status_code == 200:
        print("db_norm column already exists.")
        sys.exit(0)
    elif "db_norm" in r.text or "PGRST204" in r.text:
        print("db_norm column does NOT exist. Need to add it via Supabase dashboard SQL editor:")
        print()
        print("  ALTER TABLE terminal_observations ADD COLUMN IF NOT EXISTS db_norm double precision;")
        print()
        print("Run that in: https://supabase.com/dashboard/project/yoekhkczwdeyauwarsyy/sql/new")
        sys.exit(1)
    else:
        print(f"Unexpected response: {r.status_code} {r.text[:200]}")
        sys.exit(1)

# Use management API
sql = "ALTER TABLE terminal_observations ADD COLUMN IF NOT EXISTS db_norm double precision;"
r = requests.post(
    f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
    json={"query": sql},
    headers={"Authorization": f"Bearer {MGMT_KEY}", "Content-Type": "application/json"},
    timeout=30
)
if r.ok:
    print("Migration applied: db_norm column added.")
    sys.exit(0)
else:
    print(f"Migration failed: {r.status_code} {r.text[:500]}")
    sys.exit(1)
