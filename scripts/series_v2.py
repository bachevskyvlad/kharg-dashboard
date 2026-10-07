"""
series_v2.py — Al-Faw v2 terminal daily series (raw + norm)
=============================================================
Computes daily volume (Mn bbl) from terminal_observations for al_flaw:
  - vol_raw:  using db_mean directly
  - vol_norm: using db_norm (requires stage_h1_db_norm.py --force first)

Volume formula (canonical):
  dB_min = df['db_mean'].quantile(0.05)  [global, over all tanks+dates]
  dB_max = df['db_mean'].quantile(0.95)
  fill_ratio = clip((dB_max - db) / (dB_max - dB_min), 0, 1)
  h_current  = H_MIN + fill_ratio * (H_MAX - H_MIN)  [H_MIN=2m, H_MAX=14m per registry v2]
  vol_bbl    = π × r² × h × BARREL_PER_M3

Invariants checked:
  - vol_raw[date] <= cap_v2 for all dates  (cap = sum π×r_fit²×H_MAX×6.2898 = 8.5933 Mn bbl)
  - No interpolation across missing dates
  - Daily granularity only (no smoothing here)

Outputs:
  diagnostics/al_faw/iraq_alfaw_daily_v2.csv    — date, vol_raw_mb, vol_norm_mb
  diagnostics/al_faw/iraq_alfaw_pertank_v2.csv  — tank, date, db_mean, db_norm, vol_raw_bbl, vol_norm_bbl
  (printed to stdout) min/max/count header + vol-vs-cap table

Usage:
  python series_v2.py
"""

import os, sys, math
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

TERMINAL_ID  = "al_flaw"
ORBIT_DIR    = "DESCENDING"
PAGE_SIZE    = 1000
BARREL_PER_M3 = 6.2898
H_MIN        = 2.0    # m
H_MAX        = 14.0   # m (registry v2, all al_faw tanks)
CAP_V2_MN_BBL = 8.5933  # Mn bbl (gate B verified)

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

R_FIT = {
    "af_0": 37.3, "af_1": 36.8, "af_2": 37.0, "af_3": 36.6, "af_4": 37.0,
    "af_5": 35.9, "af_6": 36.2, "af_7": 36.3, "af_8": 36.4, "af_9": 35.1,
    "af_10": 36.6,"af_11": 34.4,"af_12": 34.2,"af_13": 35.2,"af_14": 33.8,
    "af_15": 37.2,"af_16": 36.8,"af_17": 34.9,"af_18": 34.7,"af_19": 36.9,
    "af_20": 35.9,"af_21": 36.7,"af_22": 35.5,"af_23": 35.7,
}


def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_all():
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations",
                       {"terminal_id": f"eq.{TERMINAL_ID}",
                        "orbit_direction": f"eq.{ORBIT_DIR}",
                        "select": "date,tank_id,db_mean,db_norm",
                        "order": "date.asc",
                        "offset": str(offset), "limit": str(PAGE_SIZE)})
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.DataFrame(rows)


def compute_vol(db_series, r_series, db_min, db_max):
    """Vectorised volume in Mn bbl given db and radius arrays."""
    db_clipped = db_series.clip(db_min, db_max)
    fill_ratio = (db_max - db_clipped) / (db_max - db_min)
    fill_ratio  = fill_ratio.clip(0, 1)
    h_current  = H_MIN + fill_ratio * (H_MAX - H_MIN)
    vol_bbl    = math.pi * r_series**2 * h_current * BARREL_PER_M3
    return vol_bbl  # per tank in bbl


def main():
    print("=== Al-Faw series v2 (raw + norm) ===")
    print()

    print("[1] Fetching terminal_observations …")
    df = fetch_all()
    df = df.drop_duplicates(subset=["date", "tank_id"])
    for col in ["db_mean", "db_norm"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["r_fit"] = df["tank_id"].map(R_FIT)
    df = df[df["r_fit"].notna()].copy()

    dates = sorted(df["date"].unique())
    tanks = sorted(df["tank_id"].unique())

    print(f"  {len(df)} rows | {len(tanks)} tanks | {len(dates)} dates")
    print(f"  date range: {dates[0]} – {dates[-1]}")
    print(f"  db_norm coverage: {df['db_norm'].notna().sum()}/{len(df)} "
          f"({df['db_norm'].notna().mean()*100:.1f}%)")
    print()

    # ── Global percentiles for calibration ───────────────────────────────────
    db_min_raw  = float(df["db_mean"].quantile(0.05))
    db_max_raw  = float(df["db_mean"].quantile(0.95))
    has_norm    = df["db_norm"].notna().sum() > 0
    if has_norm:
        db_min_norm = float(df["db_norm"].quantile(0.05))
        db_max_norm = float(df["db_norm"].quantile(0.95))
    else:
        db_min_norm = db_max_norm = None

    print(f"  Calibration — raw:  dB_min={db_min_raw:.3f}  dB_max={db_max_raw:.3f}")
    if has_norm:
        print(f"  Calibration — norm: dB_min={db_min_norm:.3f}  dB_max={db_max_norm:.3f}")
    else:
        print("  WARN: db_norm column empty — run stage_h1_db_norm.py --force first")
    print()

    # ── Per-tank volumes ──────────────────────────────────────────────────────
    df["vol_raw_bbl"]  = compute_vol(df["db_mean"], df["r_fit"], db_min_raw, db_max_raw)
    if has_norm:
        df["vol_norm_bbl"] = compute_vol(df["db_norm"], df["r_fit"], db_min_norm, db_max_norm)
    else:
        df["vol_norm_bbl"] = float("nan")

    # ── Daily aggregate ───────────────────────────────────────────────────────
    daily = (df.groupby("date")
               .agg(vol_raw_bbl=("vol_raw_bbl", "sum"),
                    vol_norm_bbl=("vol_norm_bbl", "sum"),
                    n_tanks=("tank_id", "count"))
               .reset_index())
    daily["vol_raw_mb"]  = daily["vol_raw_bbl"]  / 1e6
    daily["vol_norm_mb"] = daily["vol_norm_bbl"] / 1e6

    # ── Header report ─────────────────────────────────────────────────────────
    print("=" * 60)
    print("  SERIES v2 HEADER")
    print("=" * 60)
    print(f"  dates:   count={len(daily)}  min={daily['date'].min()}  max={daily['date'].max()}")
    print(f"  vol_raw: min={daily['vol_raw_mb'].min():.4f}  max={daily['vol_raw_mb'].max():.4f}  "
          f"mean={daily['vol_raw_mb'].mean():.4f}  Mn bbl")
    if has_norm:
        print(f"  vol_norm: min={daily['vol_norm_mb'].min():.4f}  max={daily['vol_norm_mb'].max():.4f}  "
              f"mean={daily['vol_norm_mb'].mean():.4f}  Mn bbl")
    print(f"  cap_v2:  {CAP_V2_MN_BBL} Mn bbl")
    print()

    # ── Invariant: vol <= cap ─────────────────────────────────────────────────
    over_cap_raw  = daily[daily["vol_raw_mb"]  > CAP_V2_MN_BBL]
    over_cap_norm = daily[daily["vol_norm_mb"].notna() & (daily["vol_norm_mb"] > CAP_V2_MN_BBL)] if has_norm else pd.DataFrame()

    if len(over_cap_raw) > 0:
        print(f"[INVARIANT FAIL] vol_raw > cap_v2 on {len(over_cap_raw)} dates:")
        print(over_cap_raw[["date","vol_raw_mb"]].to_string(index=False))
        sys.exit(1)
    print(f"  [INVARIANT OK] all vol_raw <= cap_v2={CAP_V2_MN_BBL}")

    if has_norm and len(over_cap_norm) > 0:
        print(f"[INVARIANT FAIL] vol_norm > cap_v2 on {len(over_cap_norm)} dates:")
        print(over_cap_norm[["date","vol_norm_mb"]].to_string(index=False))
        sys.exit(1)
    if has_norm:
        print(f"  [INVARIANT OK] all vol_norm <= cap_v2={CAP_V2_MN_BBL}")
    print()

    # ── Vol vs cap table: min/max/percentiles ─────────────────────────────────
    print("  Vol-vs-cap stats (raw):")
    for pct in [5, 25, 50, 75, 95]:
        v = daily["vol_raw_mb"].quantile(pct/100)
        print(f"    p{pct:02d}: {v:.4f} Mn bbl  ({v/CAP_V2_MN_BBL*100:.1f}% of cap)")
    print()

    # ── Save outputs ──────────────────────────────────────────────────────────
    daily_out = daily[["date", "vol_raw_mb", "vol_norm_mb"]].copy()
    daily_path = OUT_DIR / "iraq_alfaw_daily_v2.csv"
    daily_out.to_csv(daily_path, index=False, float_format="%.4f")
    print(f"  → {daily_path}  ({len(daily_out)} rows)")

    pertank_cols = ["tank_id", "date", "db_mean", "db_norm", "vol_raw_bbl", "vol_norm_bbl"]
    pertank_out  = df[pertank_cols].sort_values(["tank_id","date"])
    pertank_path = OUT_DIR / "iraq_alfaw_pertank_v2.csv"
    pertank_out.to_csv(pertank_path, index=False, float_format="%.4f")
    print(f"  → {pertank_path}  ({len(pertank_out)} rows)")
    print()
    print("Done. Next: events_v2.py")


if __name__ == "__main__":
    main()
