"""
export_terminal_json.py — Export terminal volume timeseries from Supabase to static JSON.

Writes:
  data/terminals/lavan_daily.json
  data/terminals/siri_daily.json
  data/terminals/jask_daily.json
  data/terminals/iran_total_daily.json  (sum of kharg + lavan + siri + jask)

JSON format: {"dates": [...], "values": [...], "terminal": "...", "max_capacity_mb": ...}

Iran total max capacity: 33.2 (Kharg) + lavan + siri + jask estimated capacity
"""

import json
import os
import sys
import pandas as pd
from supabase import create_client

SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM",
)
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

IRAN_TERMINALS = ["lavan", "siri", "jask"]

# Earliest date to include per terminal (terminal launch dates)
TERMINAL_START = {
    "jask": "2022-01-01",
}

TERMINAL_META = {
    "lavan":  {"name": "Lavan Island",  "max_capacity_mb": 7.5},
    "siri":   {"name": "Siri Island",   "max_capacity_mb": 3.2},
    "jask":   {"name": "Jask",          "max_capacity_mb": 9.8},
    "kharg":  {"name": "Kharg Island",  "max_capacity_mb": 33.2},
}
KHARG_MAX = 33.2

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "terminals")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def fetch_terminal(terminal_id: str) -> pd.DataFrame:
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("terminal_volume_timeseries")
            .select("date,vol_mn_bbl")
            .eq("terminal_id", terminal_id)
            .eq("aggregation", "daily")
            .eq("smoothed", False)
            .order("date")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    if not rows:
        return pd.DataFrame(columns=["date", "vol_mn_bbl"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    return df


def fetch_kharg() -> pd.DataFrame:
    """Kharg data lives in volume_timeseries (not terminal_volume_timeseries)."""
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("volume_timeseries")
            .select("date,vol_mn_bbl")
            .eq("aggregation", "daily")
            .eq("smoothed", False)
            .order("date")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    if not rows:
        return pd.DataFrame(columns=["date", "vol_mn_bbl"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    return df


def to_json_payload(df: pd.DataFrame, terminal_id: str) -> dict:
    meta = TERMINAL_META.get(terminal_id, {})
    return {
        "terminal": terminal_id,
        "name": meta.get("name", terminal_id),
        "max_capacity_mb": meta.get("max_capacity_mb", None),
        "dates": df["date"].dt.strftime("%Y-%m-%d").tolist(),
        "values": [round(v, 3) for v in df["vol_mn_bbl"].tolist()],
    }


def write_json(payload: dict, filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  Wrote {path} ({len(payload['dates'])} rows)")


# ── Individual terminals ───────────────────────────────────────────────────────
for tid in IRAN_TERMINALS:
    print(f"Fetching {tid}…")
    df = fetch_terminal(tid)
    if df.empty:
        print(f"  [!] No data for {tid} — skipping")
        continue
    if tid in TERMINAL_START:
        df = df[df["date"] >= TERMINAL_START[tid]]
    write_json(to_json_payload(df, tid), f"{tid}_daily.json")

# ── Iran total ─────────────────────────────────────────────────────────────────
print("Building iran_total…")
kharg_df = fetch_kharg()
all_dfs = {"kharg": kharg_df}
for tid in IRAN_TERMINALS:
    df = fetch_terminal(tid)
    if not df.empty:
        if tid in TERMINAL_START:
            df = df[df["date"] >= TERMINAL_START[tid]]
        all_dfs[tid] = df

# Outer join on date, sum non-null values
merged = None
for tid, df in all_dfs.items():
    df2 = df.rename(columns={"vol_mn_bbl": tid})
    if merged is None:
        merged = df2
    else:
        merged = merged.merge(df2, on="date", how="outer")

merged = merged.sort_values("date")
vol_cols = [c for c in merged.columns if c != "date"]
merged["vol_total"] = merged[vol_cols].sum(axis=1, min_count=1)
merged = merged.dropna(subset=["vol_total"])

total_max = KHARG_MAX + sum(
    TERMINAL_META[t]["max_capacity_mb"] for t in IRAN_TERMINALS if t in all_dfs
)

iran_total_payload = {
    "terminal": "iran_total",
    "name": "Iran Total",
    "max_capacity_mb": round(total_max, 1),
    "dates": merged["date"].dt.strftime("%Y-%m-%d").tolist(),
    "values": [round(v, 3) for v in merged["vol_total"].tolist()],
}
write_json(iran_total_payload, "iran_total_daily.json")

print("\nDone.")
