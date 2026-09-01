"""
export.py — Write JSON data files and volume chart.

Step 3 of 3 in the pipeline (ingest → compute → export).
Reads computed aggregates from parquet/pickle written by compute.py,
writes frontend JSON files, generates chart PNG, refreshes gross_flows.
"""

import os
import json
import pickle
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import warnings

warnings.filterwarnings("ignore")

from config import sb, DAILY_PATH, WEEKLY_PATH, PLOT_PATH, EWMA_ALPHA
from locations import raw_tanks, TERMINALS
from export_data import refresh_gross_flows

TERMINAL_MAX_CAP = {
    "lavan":        7.5,
    "siri":         3.2,
    "jask":         9.8,
    "al_flaw":     13.5,
    "halul":        3.5,
    "sitra":        3.7,
    "kozmino":      1.8,
    "de_kastri":    1.2,
    "primorsk":     3.8,
    "novorossiysk": 3.2,
    "ras_laffan":  11.8,
    "ras_tanura":  28.5,
    "fujairah":    19.4,
    "mina_ahmadi":  8.4,
    "ruwais":      11.5,
    "das_island":   5.2,
    "zirku":        7.9,
}

TERMINALS_DIR = os.path.join(os.path.dirname(DAILY_PATH), "terminals")
os.makedirs(TERMINALS_DIR, exist_ok=True)
os.makedirs("public", exist_ok=True)


# ── Load computed aggregates ───────────────────────────────────────────────────
daily_total  = pd.read_parquet("data/_kharg_daily.parquet")
weekly_total = pd.read_parquet("data/_kharg_weekly.parquet")
daily_total["day"]        = pd.to_datetime(daily_total["day"])
weekly_total["week_start"] = pd.to_datetime(weekly_total["week_start"])

with open("data/_terminal_daily.pkl", "rb") as f:
    terminal_daily = pickle.load(f)

print(f"[export] Kharg: {len(daily_total)} daily rows")


# ── Kharg JSON files ───────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(DAILY_PATH), exist_ok=True)

daily_payload = {
    "dates":  daily_total["day"].dt.strftime("%Y-%m-%d").tolist(),
    "values": daily_total["vol_mn_bbl"].round(3).tolist(),
    "meta": {
        "orbit": "descending", "polarization": "VV",
        "method": "inverse_crescent_radiometry",
        "mask_type": "15m_far_side_crescent",
        "scaling": "global_5_95_percentiles",
        "smoothing": "none", "aggregation": "daily",
    }
}
with open(DAILY_PATH, "w") as f:
    json.dump(daily_payload, f, indent=2)
print(f"[export] Wrote {DAILY_PATH}")

weekly_payload = {
    "dates":  weekly_total["week_start"].dt.strftime("%Y-%m-%d").tolist(),
    "values": weekly_total["smoothed_vol"].round(3).tolist(),
    "meta": {
        "orbit": "descending", "polarization": "VV",
        "method": "inverse_crescent_radiometry",
        "mask_type": "15m_far_side_crescent",
        "scaling": "global_5_95_percentiles",
        "smoothing": f"EWMA_alpha{EWMA_ALPHA}",
    }
}
with open(WEEKLY_PATH, "w") as f:
    json.dump(weekly_payload, f, indent=2)
print(f"[export] Wrote {WEEKLY_PATH}")

refresh_gross_flows(sb)


# ── Terminal JSON files ────────────────────────────────────────────────────────
iran_series = {}

for terminal in TERMINALS:
    tid  = terminal["id"]
    name = terminal["name"]
    daily_t = terminal_daily.get(tid)
    if daily_t is None or daily_t.empty:
        continue

    daily_t["day"] = pd.to_datetime(daily_t["day"])
    payload = {
        "terminal": tid, "name": name,
        "max_capacity_mb": TERMINAL_MAX_CAP.get(tid),
        "dates":  daily_t["day"].dt.strftime("%Y-%m-%d").tolist(),
        "values": daily_t["vol_mn_bbl"].round(3).tolist(),
    }
    path = os.path.join(TERMINALS_DIR, f"{tid}_daily.json")
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"[export] Wrote {path} ({len(payload['dates'])} rows)")

    iran_series[tid] = daily_t


# ── Iran Total ─────────────────────────────────────────────────────────────────
IRAN_IDS = {"lavan", "siri", "jask"}
iran_filtered = {tid: df for tid, df in iran_series.items() if tid in IRAN_IDS}

if iran_filtered:
    print("\n[export] Building iran_total…")
    kharg_df = daily_total[["day", "vol_mn_bbl"]].rename(columns={"vol_mn_bbl": "kharg"})
    merged = kharg_df.copy()
    for tid, df_t in iran_filtered.items():
        tmp = df_t[["day", "vol_mn_bbl"]].rename(columns={"vol_mn_bbl": tid})
        merged = merged.merge(tmp, on="day", how="outer")
    merged = merged.sort_values("day")
    vol_cols = [c for c in merged.columns if c != "day"]
    merged["vol_total"] = merged[vol_cols].sum(axis=1, min_count=1)
    merged = merged.dropna(subset=["vol_total"])

    total_max = 33.2 + sum(TERMINAL_MAX_CAP.get(t, 0) for t in iran_filtered)
    iran_payload = {
        "terminal": "iran_total", "name": "Iran Total",
        "max_capacity_mb": round(total_max, 1),
        "dates":  merged["day"].dt.strftime("%Y-%m-%d").tolist(),
        "values": merged["vol_total"].round(3).tolist(),
    }
    path = os.path.join(TERMINALS_DIR, "iran_total_daily.json")
    with open(path, "w") as f:
        json.dump(iran_payload, f, separators=(",", ":"))
    print(f"[export] Wrote {path}")


# ── Volume chart PNG ───────────────────────────────────────────────────────────
plt.figure(figsize=(14, 7))
plt.plot(weekly_total["week_start"], weekly_total["vol_mn_bbl"],
         marker=".", linestyle="", color="gray", alpha=0.4, label="Raw Weekly")
plt.plot(weekly_total["week_start"], weekly_total["smoothed_vol"],
         marker="s", linestyle="-", color="indigo", linewidth=3,
         label=f"Inverse Production Model (EWMA α={EWMA_ALPHA})")
plt.title(
    f"Production Output: Kharg Island Storage ({len(raw_tanks)} Tanks)\n"
    f"Inverse Crescent Radiometry (EWMA α={EWMA_ALPHA})",
    fontsize=16,
)
plt.xlabel("Date", fontsize=13)
plt.ylabel("Total Active Volume (Mn. Barrels)", fontsize=13)
plt.grid(True, linestyle="--", alpha=0.5)
max_cap = sum(np.pi * t["radius"]**2 * t["h_max"] * 6.2898 for t in raw_tanks) / 1_000_000
plt.axhline(y=max_cap, color="r", linestyle="--", linewidth=1, label=f"Max Capacity (~{max_cap:.1f} M)")
plt.legend(fontsize=12, loc="upper right")
plt.tight_layout()
plt.savefig(PLOT_PATH)
print(f"[export] Wrote {PLOT_PATH}")

# Cleanup temp files
for f in ["data/_kharg_daily.parquet", "data/_kharg_weekly.parquet", "data/_terminal_daily.pkl"]:
    try: os.remove(f)
    except: pass

print("\n[export] Done.")
