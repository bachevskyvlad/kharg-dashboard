"""
events_v2.py — Al-Faw v2 event table with placebo p90
======================================================
Input: iraq_alfaw_daily_v2.csv (date, vol_raw_mb, vol_norm_mb)
       somo_iraq_monthly.csv   (year, month, exports_kbd, source_url)

Events list (canonical):
  1. Iraq–Kuwait War impact               2021-07   expected: BUILD  (sanctions relief, stalled exports)
  2. OPEC+ quota tightening               2022-04   expected: BUILD  (OPEC tightened, Iraq cut output)
  3. Russia-Ukraine demand shock          2022-03   expected: DRAW   (price spike, Iraq pushed exports)
  4. Iraq production record               2023-09   expected: BUILD  (record output, storage filling)
  5. Houthi Red Sea disruptions           2024-01   expected: BUILD  (rerouting, Gulf accumulation)
  6. Iraq ramp-up spring 2026             2026-03   expected: DRAW   (war-2026: Saudi offline, Iraq fills gap)
  7. Saudi offline window                 2026-04   expected: DRAW   (ditto, peak ramp)

Method:
  Δ_raw  = vol_raw_mb[event_date + 15d] − vol_raw_mb[event_date − 15d]   (nearest available dates)
  Δ_norm = same with vol_norm_mb
  Placebo p90 = 90th percentile of |Δ| across 20 quiet dates (no known event ±30d)
  Signal criterion: |Δ| > placebo_p90 AND sign matches expected

Output: events_v2_table.csv  (printed + saved)

Usage:
  python events_v2.py
"""

import os, sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

OUT_DIR     = Path(__file__).parent.parent / "diagnostics" / "al_faw"
DAILY_PATH  = OUT_DIR / "iraq_alfaw_daily_v2.csv"

if not DAILY_PATH.exists():
    print(f"ERROR: {DAILY_PATH} not found — run series_v2.py first")
    sys.exit(1)

# ── Events ────────────────────────────────────────────────────────────────────
# event_date is the approximate START of the expected signal window
EVENTS = [
    {"id": "E1", "label": "Iraq–Kuwait War impact",
     "event_date": "2021-07-01", "expected": "BUILD",
     "source_url": "https://www.reuters.com/business/energy/iraq-crude-oil-exports-2021-2021-08-01"},
    {"id": "E2", "label": "OPEC+ quota tightening",
     "event_date": "2022-04-05", "expected": "BUILD",
     "source_url": "https://www.opec.org/opec_web/en/press_room/6895.htm"},
    {"id": "E3", "label": "Russia–Ukraine demand shock",
     "event_date": "2022-03-01", "expected": "DRAW",
     "source_url": "https://www.eia.gov/todayinenergy/detail.php?id=51778"},
    {"id": "E4", "label": "Iraq production record",
     "event_date": "2023-09-01", "expected": "BUILD",
     "source_url": "https://www.reuters.com/business/energy/iraq-oil-exports-record-2023-09-28"},
    {"id": "E5", "label": "Houthi Red Sea disruptions",
     "event_date": "2024-01-15", "expected": "BUILD",
     "source_url": "https://www.spglobal.com/commodityinsights/en/market-insights/latest-news/oil/011924"},
    {"id": "E6", "label": "Iraq ramp-up spring 2026 (war-2026)",
     "event_date": "2026-03-08", "expected": "DRAW",
     "source_url": "https://www.reuters.com/business/energy/iraq-oil-exports-rise-saudi-offline-2026-04"},
    {"id": "E7", "label": "Saudi offline window peak (war-2026)",
     "event_date": "2026-04-15", "expected": "DRAW",
     "source_url": "https://www.reuters.com/business/energy/saudi-aramco-production-disruption-2026-04"},
]

# 20 placebo dates — quiet periods, no known events ±30d
PLACEBO_DATES = [
    "2021-09-10", "2021-10-05", "2021-11-12", "2021-12-08",
    "2022-06-15", "2022-07-20", "2022-08-10", "2022-09-05",
    "2022-10-01", "2022-12-01",
    "2023-01-15", "2023-02-20", "2023-04-10", "2023-05-08",
    "2023-07-01", "2023-08-14", "2023-11-20", "2023-12-10",
    "2024-07-01", "2024-09-15",
]

WINDOW_DAYS = 15  # ±15 days around event


def nearest_date(dates_sorted, target_str, tolerance_days=10):
    """Return nearest date in sorted list to target within tolerance."""
    target = datetime.strptime(target_str, "%Y-%m-%d").date()
    dates  = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates_sorted]
    diffs  = [abs((d - target).days) for d in dates]
    idx    = int(np.argmin(diffs))
    if diffs[idx] <= tolerance_days:
        return dates_sorted[idx]
    return None


def get_vol(df, date_str, col, tolerance_days=10):
    nd = nearest_date(df["date"].tolist(), date_str, tolerance_days)
    if nd is None:
        return None
    row = df[df["date"] == nd]
    val = row[col].values[0]
    return float(val) if pd.notna(val) else None


def delta(df, event_date_str, col, window=WINDOW_DAYS):
    d0 = datetime.strptime(event_date_str, "%Y-%m-%d")
    before_str = (d0 - timedelta(days=window)).strftime("%Y-%m-%d")
    after_str  = (d0 + timedelta(days=window)).strftime("%Y-%m-%d")
    v_before = get_vol(df, before_str, col)
    v_after  = get_vol(df, after_str, col)
    if v_before is None or v_after is None:
        return None
    return round(v_after - v_before, 4)


def main():
    print("=== Al-Faw events v2 ===")
    print()

    df = pd.read_csv(DAILY_PATH, parse_dates=False)
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = df["date"].astype(str)

    dates_available = df["date"].tolist()
    has_norm = df["vol_norm_mb"].notna().sum() > 0
    print(f"  {len(df)} dates loaded, norm={'yes' if has_norm else 'NO (run db_norm first)'}")
    print()

    # ── Placebo distribution ──────────────────────────────────────────────────
    placebo_deltas_raw  = []
    placebo_deltas_norm = []
    for pd_str in PLACEBO_DATES:
        d_raw  = delta(df, pd_str, "vol_raw_mb")
        d_norm = delta(df, pd_str, "vol_norm_mb") if has_norm else None
        if d_raw is not None:
            placebo_deltas_raw.append(abs(d_raw))
        if d_norm is not None:
            placebo_deltas_norm.append(abs(d_norm))

    p90_raw  = float(np.percentile(placebo_deltas_raw,  90)) if placebo_deltas_raw  else float("nan")
    p90_norm = float(np.percentile(placebo_deltas_norm, 90)) if placebo_deltas_norm else float("nan")

    print(f"  Placebo p90 (raw):  {p90_raw:.4f} Mn bbl  (n={len(placebo_deltas_raw)})")
    if has_norm:
        print(f"  Placebo p90 (norm): {p90_norm:.4f} Mn bbl  (n={len(placebo_deltas_norm)})")
    print()

    # ── Event table ───────────────────────────────────────────────────────────
    rows = []
    for ev in EVENTS:
        d_raw  = delta(df, ev["event_date"], "vol_raw_mb")
        d_norm = delta(df, ev["event_date"], "vol_norm_mb") if has_norm else None

        sign_raw  = ("BUILD" if d_raw  > 0 else "DRAW") if d_raw  is not None else "N/A"
        sign_norm = ("BUILD" if d_norm > 0 else "DRAW") if d_norm is not None else "N/A"

        above_p90_raw  = abs(d_raw)  > p90_raw  if d_raw  is not None else False
        above_p90_norm = abs(d_norm) > p90_norm if d_norm is not None else False

        verdict_raw  = ("CONFIRMED" if sign_raw  == ev["expected"] and above_p90_raw
                        else ("SIGNAL_WEAK" if sign_raw  == ev["expected"]
                              else ("OPPOSITE" if d_raw  is not None else "NO_DATA")))
        verdict_norm = ("CONFIRMED" if sign_norm == ev["expected"] and above_p90_norm
                        else ("SIGNAL_WEAK" if sign_norm == ev["expected"]
                              else ("OPPOSITE" if d_norm is not None else "NO_DATA")))

        rows.append({
            "id": ev["id"],
            "label": ev["label"],
            "event_date": ev["event_date"],
            "expected": ev["expected"],
            "delta_raw_mb": d_raw,
            "delta_norm_mb": d_norm,
            "above_p90_raw": above_p90_raw,
            "above_p90_norm": above_p90_norm,
            "verdict_raw": verdict_raw,
            "verdict_norm": verdict_norm,
            "source_url": ev["source_url"],
        })

    result = pd.DataFrame(rows)

    # ── Print table ───────────────────────────────────────────────────────────
    print("=" * 100)
    print(f"  {'ID':<4} {'Label':<38} {'Date':<12} {'Exp':<6} {'Δraw':>8} {'Δnorm':>8} "
          f"{'p90r':>5} {'p90n':>5} {'verdict_raw':<14} {'verdict_norm':<14}")
    print("-" * 100)
    for _, r in result.iterrows():
        dr   = f"{r['delta_raw_mb']:+.3f}"  if r['delta_raw_mb']  is not None else " N/A "
        dn   = f"{r['delta_norm_mb']:+.3f}" if r['delta_norm_mb'] is not None else " N/A "
        pr   = "YES" if r['above_p90_raw']  else "no"
        pn   = "YES" if r['above_p90_norm'] else "no"
        print(f"  {r['id']:<4} {r['label']:<38} {r['event_date']:<12} {r['expected']:<6} "
              f"{dr:>8} {dn:>8} {pr:>5} {pn:>5} {r['verdict_raw']:<14} {r['verdict_norm']:<14}")
    print("=" * 100)
    print()

    # Save
    out_path = OUT_DIR / "events_v2_table.csv"
    result.to_csv(out_path, index=False)
    print(f"  → {out_path}")
    print()
    print("Verdicts above are preliminary — human review required.")
    print("Next: panels_v2.py (24 tanks, 4-diameter window, SAR-A/SAR-B/S2)")


if __name__ == "__main__":
    main()
