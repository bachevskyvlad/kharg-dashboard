"""
jask_polarity.py — Polarity check for Jask terminal
Outputs:
  diagnostics/jask/JASK_POLARITY.md
  diagnostics/jask/jask_polarity.json

ANCHORS (pre-stated before any calculation):
  1. war_2026      — Iran-Israel escalation, ~Apr 2026 (confirmed armed strikes on Iranian soil).
                     Expected direction: db_mean UP (oil moved out, tanks drawn down, or operations
                     disrupted — less filling → higher dB). Expected sign: POSITIVE Δdb_mean.
  2. jask_resume   — NIOC Jask pipeline resumption, summer 2025 (first export from new terminal,
                     announced June 2025 by NIOC). Expected direction: db_mean DOWN (initial fill
                     ahead of first export, tanks loaded → lower dB). Expected sign: NEGATIVE Δdb_mean.
  3. iran_sanctions_2024h2 — US/EU sanctions tightening, H2 2024 (secondary sanctions enforcement,
                     tanker fleet restrictions). Expected direction: db_mean DOWN (exports restricted,
                     oil accumulates in tanks → lower dB). Expected sign: NEGATIVE Δdb_mean.

Verdict rule: ≥2/3 anchors match expected sign → POLARITY_CONFIRMED; else POLARITY_WEAK.
(Anchor rule is immovable — defined before calculation.)
Export anchors (UANI/PortWatch correlations) are NOT computed here — quarantine.
"""

import json, os, sys, math, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "jask"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Anchors (immovable, pre-stated) ──────────────────────────────────────────
ANCHORS = [
    {
        "id":          "war_2026",
        "label":       "Iran-Israel escalation",
        "date":        "2026-04-01",      # approximate onset; use 30-day window
        "window_days": 30,
        "expected_sign": +1,              # +1 = db UP (drawdown) expected
        "rationale":   "Aerial strikes on Iranian infrastructure Apr 2026; operations disrupted, export pressure; tanks draw down → dB rises",
    },
    {
        "id":          "jask_resume",
        "label":       "NIOC Jask pipeline resumption",
        "date":        "2025-06-15",      # NIOC announcement June 2025
        "window_days": 45,
        "expected_sign": -1,              # -1 = db DOWN (fill) expected
        "rationale":   "New pipeline brought online; first fill ahead of inaugural export; tanks fill → dB falls",
    },
    {
        "id":          "iran_sanctions_2024h2",
        "label":       "Iran sanctions tightening H2 2024",
        "date":        "2024-07-01",
        "window_days": 60,
        "expected_sign": -1,              # -1 = db DOWN (accumulation) expected
        "rationale":   "Secondary sanctions / tanker fleet restrictions reduce exports; oil accumulates → dB falls",
    },
]

# ── Pull data ─────────────────────────────────────────────────────────────────
print("Pulling terminal_observations for jask…")
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

# terminal-level daily mean across all tanks
daily = df.groupby("date")["db_mean"].mean().reset_index().rename(columns={"db_mean": "db_terminal"})
daily = daily.sort_values("date")

print(f"\nDate range: {daily['date'].min().date()} → {daily['date'].max().date()}")
print(f"n_dates: {len(daily)}")
print(f"Terminal mean db: {daily['db_terminal'].mean():.2f}  std: {daily['db_terminal'].std():.2f}")

# ── Evaluate each anchor ──────────────────────────────────────────────────────
results = []
print("\n── Anchor evaluation ────────────────────────────────────────────────────")
for anc in ANCHORS:
    anchor_dt = pd.Timestamp(anc["date"])
    w         = anc["window_days"]

    pre  = daily[daily["date"] < anchor_dt]
    post = daily[(daily["date"] >= anchor_dt) & (daily["date"] < anchor_dt + pd.Timedelta(days=w))]

    if len(pre) < 3 or len(post) < 2:
        print(f"  {anc['id']:30s}  SKIP (pre={len(pre)} post={len(post)} obs)")
        results.append({**anc, "pre_mean": None, "post_mean": None,
                        "delta_db": None, "actual_sign": None,
                        "match": None, "n_pre": len(pre), "n_post": len(post)})
        continue

    pre_mean  = float(pre["db_terminal"].iloc[-5:].mean())   # last 5 obs before
    post_mean = float(post["db_terminal"].mean())
    delta_db  = post_mean - pre_mean
    actual_sign = +1 if delta_db > 0 else -1
    match       = (actual_sign == anc["expected_sign"])

    symbol = "✓" if match else "✗"
    print(f"  {anc['id']:30s}  pre={pre_mean:.2f}  post={post_mean:.2f}  "
          f"Δ={delta_db:+.2f}  expected={'UP' if anc['expected_sign']>0 else 'DOWN'}  {symbol}")

    results.append({
        **anc,
        "pre_mean":    round(pre_mean,  3),
        "post_mean":   round(post_mean, 3),
        "delta_db":    round(delta_db,  3),
        "actual_sign": actual_sign,
        "match":       match,
        "n_pre":       len(pre),
        "n_post":      len(post),
    })

# ── Verdict ───────────────────────────────────────────────────────────────────
evaluated = [r for r in results if r["match"] is not None]
n_match   = sum(1 for r in evaluated if r["match"])
n_total   = len(evaluated)

if n_total == 0:
    verdict = "INSUFFICIENT_DATA"
elif n_match >= 2:
    verdict = "POLARITY_CONFIRMED"
else:
    verdict = "POLARITY_WEAK"

print(f"\nMatched: {n_match}/{n_total} evaluated anchors  →  {verdict}")

# ── JSON output ───────────────────────────────────────────────────────────────
out = {
    "run_utc":    datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
    "n_obs":      len(rows),
    "n_dates":    len(daily),
    "verdict":    verdict,
    "n_match":    n_match,
    "n_total":    n_total,
    "anchors":    results,
}
(OUT_DIR / "jask_polarity.json").write_text(json.dumps(out, indent=2, default=str))

# ── Markdown ──────────────────────────────────────────────────────────────────
md = [
    "# Jask Polarity Check",
    "",
    f"Run: {out['run_utc']} UTC | obs: {len(rows)} | dates: {len(daily)}",
    "",
    "## Pre-stated anchors (before any calculation)",
    "",
    "| # | Anchor | Date | Window | Expected |  Rationale |",
    "|---|---|---|---|---|---|",
]
for i, anc in enumerate(ANCHORS, 1):
    exp = "db UP (+)" if anc["expected_sign"] > 0 else "db DOWN (−)"
    md.append(f"| {i} | {anc['label']} | {anc['date']} | {anc['window_days']}d | {exp} | {anc['rationale']} |")

md += [
    "",
    "## Results",
    "",
    "| Anchor | pre_db | post_db | Δdb | Expected | Match |",
    "|---|---|---|---|---|---|",
]
for r in results:
    if r["match"] is None:
        md.append(f"| {r['label']} | — | — | — | {'UP' if r['expected_sign']>0 else 'DOWN'} | SKIP (n={r['n_pre']}/{r['n_post']}) |")
    else:
        exp = "UP" if r["expected_sign"] > 0 else "DOWN"
        chk = "✓" if r["match"] else "✗"
        md.append(f"| {r['label']} | {r['pre_mean']:.2f} | {r['post_mean']:.2f} | {r['delta_db']:+.3f} | {exp} | {chk} |")

md += [
    "",
    f"## Verdict: **{verdict}** ({n_match}/{n_total} anchors match)",
    "",
    "> NOTE: Export anchors (UANI/PortWatch Jask correlations) are in quarantine — not computed here.",
]
(OUT_DIR / "JASK_POLARITY.md").write_text("\n".join(md))
print(f"\nJSON → {OUT_DIR}/jask_polarity.json")
print(f"MD   → {OUT_DIR}/JASK_POLARITY.md")
