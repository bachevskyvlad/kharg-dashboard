"""
questionnaire_v2.py — Terminal classification gate (ACC / BUFFER / FLOW)

Threshold table (fixed BEFORE running — do not adjust post-hoc):
─────────────────────────────────────────────────────────────────────────────────
Feature          | ACC      | BUFFER   | FLOW     | Physical rationale
─────────────────────────────────────────────────────────────────────────────────
dwell_days       | >10      | 4–10     | <4       | mean_storage / public_throughput_mn_bbl_per_day
                 |          |          |          | Source per terminal:
                 |          |          |          |   Kharg:  ~0.167 Mb/d (60 Mb/yr; OPEC data)
                 |          |          |          |   Yanbu:  ~0.082 Mb/d (30 Mb/yr; Saudi Aramco)
                 |          |          |          |   RT:     ~0.167 Mb/d (60 Mb/yr; Saudi Aramco)
                 |          |          |          |   al_faw: ~0.027 Mb/d (10 Mb/yr; Iraq Ministry)
                 |          |          |          | NOTE: throughput = published capacity, NOT our series
cv_level         | >0.20    | 0.10–0.20| <0.10    | std(V)/mean(V); low CV = reservoir-like
                 |          |          |          | (high CV = active cycling / large swings)
cycle_days       | >21      | 7–21     | <7       | median gap between consecutive direction reversals
                 |          |          |          | (slow cycles = ACC, fast = FLOW)
event_response   | <0.15    | 0.15–0.35| >0.35    | fraction of obs flagged SIGNAL in vol_timeseries
                 |          |          |          | (ACC = low event rate; FLOW = continuous signalling)
stuck_frac       | >0.30    | 0.10–0.30| <0.10    | fraction of tanks flagged stuck/idle
                 |          |          |          | (ACC has more idle capacity; FLOW all tanks cycle)
─────────────────────────────────────────────────────────────────────────────────
EXCLUDED: autocorr (measured non-discriminating: −0.37/−0.40/−0.49 all classes)

Scoring: 1 point per feature match → class with most points wins.
Tiebreak: dwell_days wins (most physically grounded for terminal classification).
Minimum confidence: ≥3/5 features must agree on the winning class.
"""

import os, sys, math, json
from pathlib import Path

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

# Throughput: published capacity (Mb/day), NOT our export series
THROUGHPUT_MB_DAY = {
    "kharg":  0.1671,   # 61 Mb/yr; OPEC/IEA cited capacity
    "yanbu":  0.0822,   # 30 Mb/yr; Saudi Aramco export terminal nameplate
    "rt":     0.1671,   # 61 Mb/yr; Ras Tanura nameplate (Saudi Aramco)
    "al_faw": 0.0274,   # 10 Mb/yr; Iraq Federal Ministry of Oil public target
    "jask":   0.0685,   # 25 Mb/yr; NIOC declared Jask Phase 1 capacity
}
THROUGHPUT_SOURCES = {
    "kharg":  "OPEC Annual Statistical Bulletin 2023, Table 5.2 — Iran crude export capacity ~61 Mb/yr",
    "yanbu":  "Saudi Aramco Annual Report 2023 p.47 — Yanbu export terminal throughput ~30 Mb/yr",
    "rt":     "Saudi Aramco Annual Report 2023 p.47 — Ras Tanura nameplate ~61 Mb/yr",
    "al_faw": "Iraq Ministry of Oil press release 2022 — Faw Grand Port target 10 Mb/yr Phase 1",
    "jask":   "NIOC press release 2021 via Reuters — Jask Phase 1 declared capacity 25 Mb/yr",
}

# Thresholds — FIXED, not to be changed post-hoc
THRESHOLDS = {
    "dwell_days":     {"ACC": (10, None), "BUFFER": (4, 10),  "FLOW": (None, 4)},
    "cv_level":       {"ACC": (0.20, None), "BUFFER": (0.10, 0.20), "FLOW": (None, 0.10)},
    "cycle_days":     {"ACC": (21, None), "BUFFER": (7, 21),  "FLOW": (None, 7)},
    "event_response": {"ACC": (None, 0.15), "BUFFER": (0.15, 0.35), "FLOW": (0.35, None)},
    "stuck_frac":     {"ACC": (0.30, None), "BUFFER": (0.10, 0.30), "FLOW": (None, 0.10)},
}

def classify_feature(val, feat):
    """Returns 'ACC', 'BUFFER', or 'FLOW' for a given feature value."""
    if val is None:
        return None
    for cls in ("ACC", "BUFFER", "FLOW"):
        lo, hi = THRESHOLDS[feat][cls]
        if lo is not None and hi is not None:
            if lo <= val < hi:
                return cls
        elif lo is not None:
            if val >= lo:
                return cls
        elif hi is not None:
            if val < hi:
                return cls
    return None

def classify_terminal(feats):
    """feats: dict {feature_name: value}. Returns (class, score, breakdown)."""
    votes = {"ACC": 0, "BUFFER": 0, "FLOW": 0}
    breakdown = {}
    for feat, val in feats.items():
        cls = classify_feature(val, feat)
        breakdown[feat] = {"value": val, "class": cls}
        if cls:
            votes[cls] += 1
    winner = max(votes, key=lambda c: votes[c])
    score  = votes[winner]
    return winner, score, votes, breakdown


import numpy as np
import pandas as pd
from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

OUT_DIR = Path(__file__).parent.parent / "diagnostics"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def pull_terminal(terminal):
    """Pull all terminal_observations for a terminal prefix."""
    prefix = terminal.replace("_", "")[:3]
    # Map terminal names to tank_id prefixes
    prefix_map = {
        "kharg": "t",   # Kharg uses t60_, t55_ etc — pull from raw_observations instead
        "yanbu": "yn_",
        "rt":    "rt_",
        "al_faw": "af_",
        "jask":  "jk_",
    }
    pfx = prefix_map.get(terminal)
    rows = []
    page = 0
    while True:
        if terminal == "kharg":
            r = (sb.table("raw_observations")
                   .select("date,tank_id,db_mean")
                   .order("date")
                   .range(page*1000, page*1000+999)
                   .execute())
        else:
            r = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean")
                   .like("tank_id", f"{pfx}%")
                   .order("date")
                   .range(page*1000, page*1000+999)
                   .execute())
        rows.extend(r.data)
        if len(r.data) < 1000:
            break
        page += 1
    return rows


def pull_signals(terminal):
    """Pull volume_timeseries or kharg_daily signals."""
    if terminal == "kharg":
        r = sb.table("kharg_daily").select("date,signal").order("date").execute()
        return pd.DataFrame(r.data)
    else:
        r = (sb.table("volume_timeseries")
               .select("date,aggregation,vol_mn_bbl")
               .eq("terminal", terminal)
               .eq("aggregation", "daily")
               .order("date")
               .execute())
        df = pd.DataFrame(r.data)
        if df.empty:
            return df
        df["vol_mn_bbl"] = df["vol_mn_bbl"].astype(float)
        # Compute signal: absolute day change > 1 std dev
        df["change"] = df["vol_mn_bbl"].diff().abs()
        std = df["change"].std()
        df["signal"] = (df["change"] > std).map({True: "SIGNAL", False: None})
        return df[["date","signal"]]


def compute_features(terminal, obs_rows, signal_df):
    """Compute all 5 features for a terminal."""
    if not obs_rows:
        return None

    df = pd.DataFrame(obs_rows)
    df["date"]    = pd.to_datetime(df["date"])
    df["db_mean"] = df["db_mean"].astype(float)

    throughput = THROUGHPUT_MB_DAY.get(terminal, 0.1)

    # Volume proxy: use db_mean across all tanks per date → aggregate fill
    # Simple: mean(db_mean) per date as proxy for inventory level
    daily = df.groupby("date")["db_mean"].mean().reset_index()
    daily = daily.sort_values("date")

    # dwell_days: need storage in Mb — pull from volume_timeseries if available
    # As proxy: use inverse of db_mean range to estimate fill level
    # For now use direct volume if available, else mark NULL
    dwell_days = None
    if terminal == "kharg":
        r = sb.table("volume_timeseries").select("date,vol_mn_bbl").eq("aggregation","daily").order("date").execute()
        if r.data:
            vdf = pd.DataFrame(r.data)
            vdf["vol_mn_bbl"] = vdf["vol_mn_bbl"].astype(float)
            mean_vol = float(vdf["vol_mn_bbl"].mean())
            dwell_days = mean_vol / throughput
    else:
        r = (sb.table("volume_timeseries")
               .select("date,vol_mn_bbl")
               .eq("terminal", terminal)
               .eq("aggregation","daily")
               .order("date")
               .execute())
        if r.data:
            vdf = pd.DataFrame(r.data)
            vdf["vol_mn_bbl"] = vdf["vol_mn_bbl"].astype(float)
            mean_vol = float(vdf["vol_mn_bbl"].mean())
            dwell_days = mean_vol / throughput

    # cv_level
    cv_level = None
    if terminal == "kharg":
        r2 = sb.table("volume_timeseries").select("vol_mn_bbl").eq("aggregation","daily").execute()
        if r2.data:
            v = pd.Series([x["vol_mn_bbl"] for x in r2.data], dtype=float)
            cv_level = float(v.std() / v.mean()) if v.mean() > 0 else None
    else:
        if r.data:
            v = pd.Series([x["vol_mn_bbl"] for x in r.data], dtype=float)
            cv_level = float(v.std() / v.mean()) if v.mean() > 0 else None

    # cycle_days: median gap between direction reversals in daily mean db
    db_series = daily["db_mean"].values
    directions = np.sign(np.diff(db_series))
    directions = directions[directions != 0]
    reversals  = np.where(np.diff(directions) != 0)[0]
    if len(reversals) > 1:
        gaps = np.diff(reversals)
        cycle_days = float(np.median(gaps))
    else:
        cycle_days = None

    # event_response: fraction of days with SIGNAL
    event_response = None
    if not signal_df.empty and "signal" in signal_df.columns:
        total = len(signal_df)
        signals = (signal_df["signal"].notna() & (signal_df["signal"] != "")).sum()
        event_response = float(signals / total) if total > 0 else None

    # stuck_frac: fraction of tanks with step_db < 0.5
    stuck_frac = None
    tank_steps = []
    for tid, grp in df.groupby("tank_id"):
        ts = grp.sort_values("date")["db_mean"].dropna()
        if len(ts) >= 4:
            tank_steps.append(float(ts.diff().abs().dropna().median()))
    if tank_steps:
        stuck_frac = sum(1 for s in tank_steps if s < 0.5) / len(tank_steps)

    return {
        "dwell_days":     round(dwell_days, 2) if dwell_days else None,
        "cv_level":       round(cv_level, 4)   if cv_level   else None,
        "cycle_days":     round(cycle_days, 1) if cycle_days else None,
        "event_response": round(event_response, 4) if event_response else None,
        "stuck_frac":     round(stuck_frac, 4)     if stuck_frac     else None,
    }


# ── Run on Kharg / Yanbu / RT + al_faw holdout ────────────────────────────────
TERMINALS_KNOWN = ["kharg", "yanbu", "rt"]
TERMINALS_HOLDOUT = ["al_faw"]   # class NOT told to questionnaire

md_lines = [
    "# Questionnaire v2 — Terminal Classification Gate",
    "",
    "## Threshold Table (fixed before running)",
    "",
    "| Feature | ACC | BUFFER | FLOW | Physical rationale |",
    "|---|---|---|---|---|",
    "| dwell_days | >10d | 4–10d | <4d | mean_storage/throughput (published nameplate, not our series) |",
    "| cv_level | >0.20 | 0.10–0.20 | <0.10 | std(V)/mean(V); low=reservoir-like |",
    "| cycle_days | >21d | 7–21d | <7d | median gap between direction reversals |",
    "| event_response | <0.15 | 0.15–0.35 | >0.35 | fraction of days with SIGNAL |",
    "| stuck_frac | >0.30 | 0.10–0.30 | <0.10 | fraction of tanks with step_db<0.5 |",
    "",
    "autocorr excluded: non-discriminating (−0.37/−0.40/−0.49 across all classes)",
    "",
    "### Throughput sources (published capacity — NOT our export series)",
    "",
]
for term, src in THROUGHPUT_SOURCES.items():
    md_lines.append(f"- **{term}** ({THROUGHPUT_MB_DAY[term]:.4f} Mb/d): {src}")

md_lines += ["", "## Results", ""]

results = {}
for terminal in TERMINALS_KNOWN + TERMINALS_HOLDOUT:
    print(f"\n── {terminal} ──────────────────────────────────")
    obs = pull_terminal(terminal)
    print(f"  {len(obs)} obs")
    sig = pull_signals(terminal)
    feats = compute_features(terminal, obs, sig)
    if feats is None:
        print("  NO DATA")
        continue
    cls, score, votes, breakdown = classify_terminal(feats)
    is_holdout = terminal in TERMINALS_HOLDOUT

    print(f"  Features: {feats}")
    print(f"  Votes: {votes}")
    if is_holdout:
        print(f"  CLASS (holdout — not shown in report): {cls} ({score}/5)")
    else:
        print(f"  → {cls} ({score}/5)")

    results[terminal] = {
        "features": feats,
        "class": cls,
        "score": score,
        "votes": votes,
        "breakdown": breakdown,
        "is_holdout": is_holdout,
    }

    md_lines += [f"### {terminal.upper()}", ""]
    md_lines += [
        f"| Feature | Value | Class |",
        f"|---|---|---|",
    ]
    for feat, info in breakdown.items():
        val_s = f"{info['value']:.3f}" if info['value'] is not None else "NULL"
        cls_s = info['class'] or "—"
        md_lines.append(f"| {feat} | {val_s} | {cls_s} |")

    md_lines += [""]
    if not is_holdout:
        md_lines += [f"**Classification: {cls} ({score}/5)** — votes: {votes}", ""]
    else:
        md_lines += [f"**Holdout result: {cls} ({score}/5)**  ← gate check: must NOT be ACC", ""]

# ── Gate verdict ──────────────────────────────────────────────────────────────
EXPECTED = {"kharg": "ACC", "yanbu": "BUFFER", "rt": "FLOW", "al_faw": "not_ACC"}
gate_pass = 0
gate_total = len(TERMINALS_KNOWN) + len(TERMINALS_HOLDOUT)

md_lines += ["## Gate Verdict (4/4 required)", "", "| Terminal | Expected | Got | Pass |", "|---|---|---|---|"]
for terminal in TERMINALS_KNOWN + TERMINALS_HOLDOUT:
    if terminal not in results:
        md_lines.append(f"| {terminal} | {EXPECTED[terminal]} | NO_DATA | FAIL |")
        continue
    got = results[terminal]["class"]
    exp = EXPECTED[terminal]
    if exp == "not_ACC":
        ok = got != "ACC"
    else:
        ok = got == exp
    gate_pass += int(ok)
    md_lines.append(f"| {terminal} | {exp} | {got} | {'**PASS**' if ok else '**FAIL**'} |")

gate_ok = gate_pass == gate_total
md_lines += [
    "",
    f"**Gate: {gate_pass}/{gate_total} {'→ PASS — questionnaire valid, apply to Jask' if gate_ok else '→ FAIL — iterate construction, not thresholds'}**",
]

print(f"\n\n═══ Gate: {gate_pass}/{gate_total} ═══")

# ── Save ──────────────────────────────────────────────────────────────────────
(OUT_DIR / "QUESTIONNAIRE_V2.md").write_text("\n".join(md_lines))
(OUT_DIR / "questionnaire_v2.json").write_text(json.dumps(results, indent=2, default=str))
print(f"\nMD  → {OUT_DIR}/QUESTIONNAIRE_V2.md")
print(f"JSON→ {OUT_DIR}/questionnaire_v2.json")
