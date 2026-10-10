"""
series_v3.py — Al-Faw daily volumes: full-24 / live-17 / norm-17
with live-based calibration (p05/p95 from LIVE tanks only).

LOW_STUCK tanks (excluded from calibration, clipped in [0,1]):
  af_0, af_2, af_3, af_6, af_7, af_9, af_11

Three output series per date:
  vol_raw24   — all 24 tanks, live-calibration scale
  vol_live17  — live 17 tanks only, live-calibration scale
  vol_norm17  — live 17 tanks, db_norm, live-norm-calibration scale

Invariants checked:
  vol_raw24  <= cap_all  (8.5933 Mn bbl)
  vol_live17 <= cap_live17

Usage:
  python series_v3.py [--force]   # --force required to write CSV
"""
import os, sys, math
import requests
import pandas as pd
import numpy as np
from pathlib import Path

DRY = "--force" not in sys.argv

SB_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or ""
TERMINAL_ID  = "al_flaw"
ORBIT_DIR    = "DESCENDING"
PAGE_SIZE    = 1000

H_MIN        = 2.0
H_MAX        = 14.0
BARREL_PER_M3 = 6.2898

LOW_STUCK = {"af_0","af_2","af_3","af_6","af_7","af_9","af_11"}

R_FIT = {
    "af_0": 37.3, "af_1": 36.8, "af_2": 37.0, "af_3": 36.6, "af_4": 37.0,
    "af_5": 35.9, "af_6": 36.2, "af_7": 36.3, "af_8": 36.4, "af_9": 35.1,
    "af_10": 36.6,"af_11": 34.4,"af_12": 34.2,"af_13": 35.2,"af_14": 33.8,
    "af_15": 37.2,"af_16": 36.8,"af_17": 34.9,"af_18": 34.7,"af_19": 36.9,
    "af_20": 35.9,"af_21": 36.7,"af_22": 35.5,"af_23": 35.7,
}

LIVE = {k: v for k, v in R_FIT.items() if k not in LOW_STUCK}

def cap_mn_bbl(r_dict):
    return sum(math.pi * r**2 * H_MAX * BARREL_PER_M3 for r in r_dict.values()) / 1e6

CAP_ALL   = cap_mn_bbl(R_FIT)
CAP_LIVE  = cap_mn_bbl(LIVE)

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"


def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_all():
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations", {
            "terminal_id": f"eq.{TERMINAL_ID}",
            "orbit_direction": f"eq.{ORBIT_DIR}",
            "select": "date,tank_id,db_mean,db_norm",
            "order": "date.asc",
            "offset": str(offset), "limit": str(PAGE_SIZE)})
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.DataFrame(rows)


def vol_tank(db_series, r_m, db_min, db_max):
    """Volume in bbl per tank-date."""
    fill = ((db_max - db_series) / (db_max - db_min)).clip(0, 1)
    h = H_MIN + fill * (H_MAX - H_MIN)
    return math.pi * r_m**2 * h * BARREL_PER_M3


def main():
    print("=== Al-Faw series v3 — live-calibrated ===")
    print(f"  LOW_STUCK:  {sorted(LOW_STUCK)}")
    print(f"  LIVE tanks: {sorted(LIVE.keys())}  (n={len(LIVE)})")
    print(f"  cap_all:    {CAP_ALL:.4f} Mn bbl")
    print(f"  cap_live17: {CAP_LIVE:.4f} Mn bbl")
    print()

    print("[1] Fetching observations …")
    df = fetch_all()
    df["date"]    = pd.to_datetime(df["date"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df["db_norm"] = pd.to_numeric(df["db_norm"], errors="coerce")
    df["r_fit"]   = df["tank_id"].map(R_FIT)
    df = df[df["r_fit"].notna()].copy()
    df["stuck"]   = df["tank_id"].isin(LOW_STUCK)
    print(f"  {len(df)} rows | {df.tank_id.nunique()} tanks | {df.date.nunique()} dates")
    print(f"  date range: {df.date.min().date()} – {df.date.max().date()}")

    # ── Calibration: p05/p95 from LIVE tanks only ───────────────────────────
    live_db = df[~df["stuck"]]["db_mean"].dropna()
    db_min_raw = float(live_db.quantile(0.05))
    db_max_raw = float(live_db.quantile(0.95))

    live_norm = df[~df["stuck"]]["db_norm"].dropna()
    has_norm = len(live_norm) > 0
    db_min_norm = float(live_norm.quantile(0.05)) if has_norm else None
    db_max_norm = float(live_norm.quantile(0.95)) if has_norm else None

    # Compare with old calibration (all 24 tanks)
    all_db = df["db_mean"].dropna()
    old_p05 = float(all_db.quantile(0.05))
    old_p95 = float(all_db.quantile(0.95))

    print()
    print("  Calibration — RAW:")
    print(f"    OLD (all-24):  p05={old_p05:.3f}  p95={old_p95:.3f}  range={old_p95-old_p05:.3f} dB")
    print(f"    NEW (live-17): p05={db_min_raw:.3f}  p95={db_max_raw:.3f}  range={db_max_raw-db_min_raw:.3f} dB")
    if has_norm:
        all_norm = df["db_norm"].dropna()
        print(f"  Calibration — NORM:")
        print(f"    NEW (live-17): p05={db_min_norm:.3f}  p95={db_max_norm:.3f}  range={db_max_norm-db_min_norm:.3f} dB")

    # ── Per-tank volumes ─────────────────────────────────────────────────────
    df["vol_raw_bbl"] = vol_tank(df["db_mean"], df["r_fit"], db_min_raw, db_max_raw)
    if has_norm:
        mask = df["db_norm"].notna()
        df.loc[mask, "vol_norm_bbl"] = vol_tank(df.loc[mask,"db_norm"], df.loc[mask,"r_fit"],
                                                  db_min_norm, db_max_norm)

    # ── Aggregate per date ───────────────────────────────────────────────────
    # vol_raw24: all 24 tanks
    raw24 = df.groupby("date")["vol_raw_bbl"].sum().rename("vol_raw24_bbl")

    # vol_live17: only live tanks
    live_df = df[~df["stuck"]]
    live17  = live_df.groupby("date")["vol_raw_bbl"].sum().rename("vol_live17_bbl")

    # vol_norm17: live tanks with db_norm
    if has_norm:
        norm17 = live_df[live_df["db_norm"].notna()].groupby("date")["vol_norm_bbl"].sum()
        # Only include dates where ALL 17 live tanks have norm (else NaN)
        n_per_date = live_df[live_df["db_norm"].notna()].groupby("date")["tank_id"].count()
        # Dates where < 17 live tanks have norm → set NaN
        norm17 = norm17.where(n_per_date >= len(LIVE) * 0.8).rename("vol_norm17_bbl")

    daily = pd.concat([raw24, live17] + ([norm17] if has_norm else []), axis=1).reset_index()
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["vol_raw24_mb"]  = daily["vol_raw24_bbl"] / 1e6
    daily["vol_live17_mb"] = daily["vol_live17_bbl"] / 1e6
    if has_norm:
        daily["vol_norm17_mb"] = daily["vol_norm17_bbl"] / 1e6

    # ── Invariants ───────────────────────────────────────────────────────────
    print()
    print("[2] Invariants …")
    over24 = daily[daily["vol_raw24_mb"] > CAP_ALL]
    if len(over24):
        print(f"  [INVARIANT FAIL] vol_raw24 > cap_all={CAP_ALL:.4f} on {len(over24)} dates")
        sys.exit(1)
    print(f"  [INVARIANT OK] all vol_raw24 <= cap_all={CAP_ALL:.4f}")
    over17 = daily[daily["vol_live17_mb"] > CAP_LIVE]
    if len(over17):
        print(f"  [INVARIANT FAIL] vol_live17 > cap_live={CAP_LIVE:.4f} on {len(over17)} dates")
        sys.exit(1)
    print(f"  [INVARIANT OK] all vol_live17 <= cap_live17={CAP_LIVE:.4f}")

    # ── Series header ─────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  SERIES v3 HEADER")
    print("=" * 60)
    print(f"  dates:       count={len(daily)}  min={daily.date.min().date()}  max={daily.date.max().date()}")
    print(f"  vol_raw24:   min={daily.vol_raw24_mb.min():.4f}  max={daily.vol_raw24_mb.max():.4f}  mean={daily.vol_raw24_mb.mean():.4f}  Mn bbl")
    print(f"  vol_live17:  min={daily.vol_live17_mb.min():.4f}  max={daily.vol_live17_mb.max():.4f}  mean={daily.vol_live17_mb.mean():.4f}  Mn bbl")
    if has_norm:
        nm = daily["vol_norm17_mb"].dropna()
        print(f"  vol_norm17:  min={nm.min():.4f}  max={nm.max():.4f}  mean={nm.mean():.4f}  Mn bbl  ({len(nm)}/{len(daily)} dates)")
    print(f"  cap_all:     {CAP_ALL:.4f} Mn bbl")
    print(f"  cap_live17:  {CAP_LIVE:.4f} Mn bbl")

    # ── Write pertank with stuck_flag ─────────────────────────────────────────
    pertank = df[["date","tank_id","db_mean","db_norm","vol_raw_bbl"]].copy()
    pertank["stuck_flag"] = df["stuck"].astype(int)
    if has_norm:
        pertank["vol_norm_bbl"] = df.get("vol_norm_bbl")
    pertank["date"] = pertank["date"].dt.strftime("%Y-%m-%d")

    # ── Output CSV ────────────────────────────────────────────────────────────
    cols = ["date","vol_raw24_mb","vol_live17_mb"] + (["vol_norm17_mb"] if has_norm else [])
    out_daily   = OUT_DIR / "iraq_alfaw_daily_v3.csv"
    out_pertank = OUT_DIR / "iraq_alfaw_pertank_v3.csv"

    if DRY:
        print()
        print("DRY-RUN — pass --force to write CSVs.")
        return daily, pertank, db_min_raw, db_max_raw

    daily[cols].to_csv(out_daily, index=False, float_format="%.4f")
    pertank.to_csv(out_pertank, index=False, float_format="%.4f")
    print()
    print(f"  → {out_daily}  ({len(daily)} rows)")
    print(f"  → {out_pertank}  ({len(pertank)} rows)")
    print()
    print("Done.")
    return daily, pertank, db_min_raw, db_max_raw


if __name__ == "__main__":
    main()
