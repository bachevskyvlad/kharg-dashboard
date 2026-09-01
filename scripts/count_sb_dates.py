import os, sys
try:
    from supabase import create_client
    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
    rows = sb.table('raw_observations').select('date', count='exact').execute()
    print(rows.count or 0)
except Exception as e:
    print(0)
