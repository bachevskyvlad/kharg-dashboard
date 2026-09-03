"""
plot_terminals.py — fetch terminal_volume_timeseries from Supabase and
save a monthly dynamics chart as terminal_volumes.png
"""
import os, sys
from supabase import create_client
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

URL = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
sb  = create_client(URL, KEY)

rows = []
offset = 0
while True:
    r = (sb.table("terminal_volume_timeseries")
           .select("terminal_id,date,vol_mn_bbl")
           .range(offset, offset + 999)
           .execute())
    rows.extend(r.data)
    if len(r.data) < 1000:
        break
    offset += 1000

print(f"Fetched {len(rows)} rows")
df = pd.DataFrame(rows)
if df.empty:
    print("No data in terminal_volume_timeseries")
    sys.exit(0)

df["date"] = pd.to_datetime(df["date"])
df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
monthly = df.groupby(["terminal_id", "month"])["vol_mn_bbl"].mean().reset_index()

# Only terminals with >= 10 months
counts = monthly.groupby("terminal_id")["month"].count()
good = sorted(counts[counts >= 10].index.tolist())
print(f"Terminals with >=10 months: {good}")

if not good:
    print("Not enough data for any terminal")
    sys.exit(0)

monthly = monthly[monthly["terminal_id"].isin(good)]

COLORS = [
    "#2196F3","#FF5722","#4CAF50","#9C27B0","#FF9800",
    "#00BCD4","#E91E63","#8BC34A","#795548","#607D8B",
]

fig, ax = plt.subplots(figsize=(14, 6))
fig.patch.set_facecolor("#0d1117")
ax.set_facecolor("#161b22")

for i, tid in enumerate(good):
    sub = monthly[monthly.terminal_id == tid].sort_values("month")
    color = COLORS[i % len(COLORS)]
    ax.plot(sub["month"], sub["vol_mn_bbl"], color=color, linewidth=1.8,
            label=tid.replace("_", " ").title(), marker="o", markersize=2.5)

ax.set_title("Gulf Terminals — Monthly Storage Dynamics (Mn bbl)", color="white",
             fontsize=13, pad=14, fontweight="bold")
ax.set_ylabel("Volume (Mn bbl)", color="#aaa", fontsize=10)
ax.tick_params(colors="#aaa", labelsize=9)
for spine in ax.spines.values():
    spine.set_edgecolor("#30363d")
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
ax.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=[4, 7, 10]))
ax.grid(which="major", color="#30363d", linewidth=0.6, linestyle="--")
ax.grid(which="minor", color="#21262d", linewidth=0.3)
ax.legend(framealpha=0.15, facecolor="#161b22", edgecolor="#30363d",
          labelcolor="white", fontsize=8.5, ncol=2, loc="upper left")

plt.tight_layout()
out = "terminal_volumes.png"
plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved {out}")
