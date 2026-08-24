"""
dedup_terminal_observations.py — Remove duplicate rows from terminal_observations.

A duplicate here means: two rows for the same (terminal_id, tank_id) where
the dates are consecutive calendar days AND the db_mean values are identical
(within 1e-6 tolerance). This pattern was caused by Sentinel-1C multi-satellite
overlap producing two catalog entries for the same physical orbit pass.

Strategy:
  For each (terminal_id, tank_id) pair, sort observations by date.
  When two consecutive rows have equal db_mean, keep the EARLIER date and
  delete the LATER one (it is the duplicate scene entry).

Run once manually after deploying the orbit-based dedup fix in sar_fetch.py.
"""

import sys
import os
from supabase import create_client
import pandas as pd

sb = create_client(
    "https://yoekhkczwdeyauwarsyy.supabase.co",
    "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
)

TERMINALS = ["lavan", "siri", "jask", "al_flaw",
             "halul", "sitra", "kozmino", "de_kastri", "primorsk", "novorossiysk"]

total_deleted = 0

for tid in TERMINALS:
    print(f"\n{'='*50}\n{tid}")

    # Load all observations for this terminal
    rows = []
    offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("id,date,tank_id,db_mean")
                   .eq("terminal_id", tid)
                   .order("date")
                   .range(offset, offset + 999)
                   .execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    if not rows:
        print("  no data")
        continue

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["tank_id", "date"]).reset_index(drop=True)

    ids_to_delete = []

    for tank_id, grp in df.groupby("tank_id"):
        grp = grp.sort_values("date").reset_index(drop=True)
        for i in range(1, len(grp)):
            prev = grp.iloc[i - 1]
            curr = grp.iloc[i]
            gap_days = (curr["date"] - prev["date"]).days
            same_val = abs(float(curr["db_mean"]) - float(prev["db_mean"])) < 1e-6
            if gap_days == 1 and same_val:
                # curr is the duplicate — delete it
                ids_to_delete.append(curr["id"])

    if not ids_to_delete:
        print("  no duplicates found")
        continue

    print(f"  found {len(ids_to_delete)} duplicate rows — deleting...")

    # Delete in batches of 100
    for i in range(0, len(ids_to_delete), 100):
        batch_ids = ids_to_delete[i:i + 100]
        sb.table("terminal_observations").delete().in_("id", batch_ids).execute()

    print(f"  deleted {len(ids_to_delete)} rows")
    total_deleted += len(ids_to_delete)

print(f"\nDone. Total deleted: {total_deleted} rows.")
print("Re-run export_terminal_json.py to regenerate JSON files from clean data.")
