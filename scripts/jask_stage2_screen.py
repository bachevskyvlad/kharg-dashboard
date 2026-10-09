"""
jask_stage2_screen.py — Screen + Cap Gate for Jask
Outputs:
  diagnostics/jask/stage2_screen.json
  diagnostics/jask/JASK_SCREEN.md
"""
import math, json, os, sys, datetime
from pathlib import Path

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

WL_THRESHOLD  = 2.5   # step_db >= this → working level; immovable
CAP_GATE      = 0.60  # 60% — immovable
BBL_PER_M3    = 6.2898
DB_FLOOR      = -5.0

sys.path.insert(0, os.path.dirname(__file__))
from tank_registry import LOCATIONS
import numpy as np
import pandas as pd
from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "jask"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Registry ──────────────────────────────────────────────────────────────────
jask_loc = next(l for l in LOCATIONS if l["terminal"] == "jask")
tanks_reg = {t["id"]: t for t in jask_loc["tanks"]}
print(f"Registry: {len(tanks_reg)} Jask tanks")

# ── Pull data ─────────────────────────────────────────────────────────────────
print("Pulling terminal_observations…")
rows = []
page = 0
while True:
    r = (sb.table("terminal_observations")
           .select("date,tank_id,db_mean")
           .like("tank_id", "jk_%")
           .order("date")
           .range(page * 1000, page * 1000 + 999)
           .execute())
    rows.extend(r.data)
    if len(r.data) < 1000:
        break
    page += 1

print(f"  {len(rows)} rows")
if not rows:
    print("NO DATA — aborting")
    sys.exit(1)

df = pd.DataFrame(rows)
df["date"]    = pd.to_datetime(df["date"])
df["db_mean"] = df["db_mean"].astype(float)

# ── Step_db per tank ──────────────────────────────────────────────────────────
step_rows = []
for tid in sorted(tanks_reg.keys()):
    grp = df[df["tank_id"] == tid].sort_values("date")
    ts  = grp["db_mean"].dropna()
    n   = len(ts)
    if n < 4:
        step_rows.append({"tank_id": tid, "n_obs": n, "step_db": None, "WL": False, "note": "sparse"})
        continue
    step = float(ts.diff().abs().dropna().median())
    step_rows.append({"tank_id": tid, "n_obs": n, "step_db": round(step, 3),
                      "WL": step >= WL_THRESHOLD, "note": ""})

step_df = pd.DataFrame(step_rows)
print("\n── step_db ──────────────────────────────────────")
for _, row in step_df.iterrows():
    wl_flag = " ← WL" if row["WL"] else ""
    step_s  = f"{row['step_db']:.3f}" if row["step_db"] is not None else "NULL"
    print(f"  {row['tank_id']:6s}  step={step_s:6s}  n={row['n_obs']:3d}{wl_flag}")

# ── Capacity via r_fit ────────────────────────────────────────────────────────
def tank_cap_mb(t):
    return math.pi * t["radius_m"]**2 * t["h_max_m"] * BBL_PER_M3 / 1e6

total_cap = sum(tank_cap_mb(t) for t in tanks_reg.values())
per_tank_cap = {tid: tank_cap_mb(t) for tid, t in tanks_reg.items()}

wl_ids  = step_df[step_df["WL"]]["tank_id"].tolist()
wl_cap  = sum(per_tank_cap.get(tid, 0) for tid in wl_ids)
wl_pct  = wl_cap / total_cap * 100

print(f"\nTotal capacity (r_fit-based): {total_cap:.3f} Mb")
print(f"WL tanks ({len(wl_ids)}): {', '.join(sorted(wl_ids))}")
print(f"WL cap: {wl_cap:.3f} Mb = {wl_pct:.1f}% of total")
print(f"\n60% gate: {'PASS → TERMINAL_SERIES' if wl_pct >= 60 else f'FAIL → TANK_LEVEL ({wl_pct:.1f}% < 60%)'}")
verdict = "TERMINAL_SERIES" if wl_pct >= 60 else "TANK_LEVEL"

# ── Stuck detection ───────────────────────────────────────────────────────────
print("\n── Stuck detection ──────────────────────────────")
stuck_rows = []
for tid in sorted(tanks_reg.keys()):
    grp = df[df["tank_id"] == tid].sort_values("date")
    ts  = grp["db_mean"].dropna()
    n   = len(ts)
    if n < 6:
        stuck_rows.append({"tank_id": tid, "flag": "sparse", "floor_frac": None, "iqr": None, "range": None})
        continue
    floor_frac = float((ts <= DB_FLOOR + 0.5).mean())
    iqr   = float(ts.quantile(0.75) - ts.quantile(0.25))
    rng   = float(ts.max()          - ts.min())
    step  = float(ts.diff().abs().dropna().median())

    if rng < 0.5:
        flag = "FLAT_STUCK"
    elif floor_frac > 0.80:
        flag = "FLOOR_STUCK"
    elif iqr > 0 and iqr / (rng + 0.001) < 0.12 and step < 0.5:
        flag = "UNIMODAL_NARROW"
    else:
        flag = "ok"

    # New construction silent from day 1 → not_yet_commissioned
    if flag in ("FLAT_STUCK", "FLOOR_STUCK"):
        first_date = grp["date"].min()
        if first_date >= pd.Timestamp("2024-06-01"):
            flag = "not_yet_commissioned"

    stuck_rows.append({
        "tank_id":    tid,
        "flag":       flag,
        "floor_frac": round(floor_frac, 3),
        "iqr":        round(iqr, 3),
        "range":      round(rng, 3),
        "step_db":    round(step, 3),
    })
    print(f"  {tid:6s}  {flag:22s}  floor={floor_frac:.2f}  iqr={iqr:.2f}  range={rng:.2f}  step={step:.3f}")

stuck_df = pd.DataFrame(stuck_rows)

# ── JSON output ───────────────────────────────────────────────────────────────
out = {
    "run_utc":       datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
    "n_obs":         len(rows),
    "wl_threshold":  WL_THRESHOLD,
    "cap_gate":      CAP_GATE,
    "total_cap_mb":  round(total_cap, 3),
    "wl_cap_mb":     round(wl_cap, 3),
    "wl_pct":        round(wl_pct, 1),
    "wl_ids":        sorted(wl_ids),
    "verdict":       verdict,
    "step_db":       step_df.to_dict(orient="records"),
    "stuck":         stuck_df.to_dict(orient="records"),
}
(OUT_DIR / "stage2_screen.json").write_text(json.dumps(out, indent=2, default=str))
print(f"\nJSON → {OUT_DIR}/stage2_screen.json")

# ── Markdown ──────────────────────────────────────────────────────────────────
md = [
    "# Jask Stage 2 — Screen & Cap Gate",
    f"",
    f"Run: {out['run_utc']} UTC | obs: {len(rows)} | tanks: {len(tanks_reg)}",
    "",
    "## Step_db per tank (WL threshold = 2.5)",
    "",
    "| Tank | n_obs | step_db | WL | r_fit (m) | cap (Mb) |",
    "|---|---|---|---|---|---|",
]
for _, row in step_df.iterrows():
    tid  = row["tank_id"]
    t    = tanks_reg.get(tid, {})
    cap  = round(per_tank_cap.get(tid, 0), 3)
    r    = t.get("radius_m", "?")
    step_s = f"{row['step_db']:.3f}" if row["step_db"] is not None else "—"
    wl_s   = "**YES**" if row["WL"] else "no"
    md.append(f"| {tid} | {row['n_obs']} | {step_s} | {wl_s} | {r} | {cap} |")

wl_cap_str = f"{wl_cap:.3f} Mb = **{wl_pct:.1f}%** of {total_cap:.3f} Mb total"
md += [
    "",
    f"**WL tanks ({len(wl_ids)}):** {', '.join(sorted(wl_ids))}",
    f"**WL capacity:** {wl_cap_str}",
    "",
    "## Capacity (r_fit-based)",
    "",
    "| Tank | r_fit (m) | h_max (m) | cap (Mb) |",
    "|---|---|---|---|",
]
for tid, t in sorted(tanks_reg.items()):
    md.append(f"| {tid} | {t['radius_m']} | {t['h_max_m']} | {per_tank_cap[tid]:.3f} |")
md += [
    "",
    f"**Total: {total_cap:.3f} Mb** (vs legacy 35 m flat estimate ~{math.pi*35**2*13.5*BBL_PER_M3/1e6*20:.1f} Mb)",
    "",
    "## 60% Gate",
    "",
    f"WL cap / total cap = {wl_pct:.1f}% — gate threshold = 60%",
    "",
    f"### **Verdict: {verdict}**",
    "",
    "## Stuck Detection",
    "",
    "| Tank | flag | floor_frac | IQR | range | step_db |",
    "|---|---|---|---|---|---|",
]
for _, row in stuck_df.iterrows():
    ff  = f"{row['floor_frac']:.3f}" if row["floor_frac"] is not None else "—"
    iqr = f"{row['iqr']:.3f}"        if row["iqr"]        is not None else "—"
    rng = f"{row['range']:.3f}"      if row["range"]       is not None else "—"
    stp = f"{row['step_db']:.3f}"    if row.get("step_db") is not None else "—"
    md.append(f"| {row['tank_id']} | {row['flag']} | {ff} | {iqr} | {rng} | {stp} |")

(OUT_DIR / "JASK_SCREEN.md").write_text("\n".join(md))
print(f"MD  → {OUT_DIR}/JASK_SCREEN.md")
