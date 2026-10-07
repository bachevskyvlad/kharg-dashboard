"""
stuck_passport.py — LOW-STUCK tank diagnostic passport.

Per stuck tank:
  - db_mean median, std, p05, p95
  - fraction of rows < -5.0 dB
  - first/last date, and date when "stuck" began (if not from start)
  - year-by-year fraction_below_5 to detect onset year

Contradiction note: Esri imagery shows roofs DOWN (empty);
SAR reads low-dB = "full" → open question, documented.

Usage: python stuck_passport.py
"""
import os, sys
from pathlib import Path
import pandas as pd, numpy as np

# Load from pre-floor git backup (original db_mean values)
import subprocess, io
raw_pt = subprocess.check_output(
    ["git","show","367321d:diagnostics/al_faw/iraq_alfaw_pertank_v2.csv"])
pt = pd.read_csv(io.BytesIO(raw_pt))
pt["date"] = pd.to_datetime(pt["date"])
pt = pt.sort_values(["tank_id","date"]).reset_index(drop=True)

LOW_STUCK = ["af_0","af_2","af_3","af_6","af_7","af_9","af_11"]
FLOOR = -5.0
ONSET_THRESH = 0.50  # fraction_below_floor >= 50% → onset year

print("=" * 70)
print("  LOW-STUCK TANK PASSPORT")
print("=" * 70)
print()

for t in sorted(LOW_STUCK):
    d = pt[pt.tank_id==t].copy()
    db = d["db_mean"]
    print(f"  ── {t} ──")
    print(f"     N={len(d)}  first={d.date.min().date()}  last={d.date.max().date()}")
    print(f"     db_mean: min={db.min():.2f}  p05={db.quantile(.05):.2f}  p25={db.quantile(.25):.2f}"
          f"  med={db.median():.2f}  p75={db.quantile(.75):.2f}  p95={db.quantile(.95):.2f}  max={db.max():.2f}")
    print(f"     std={db.std():.3f}  frac_below_{abs(int(FLOOR))}={((db<FLOOR).mean()):.1%}")

    # Year-by-year onset detection
    d["year"] = d.date.dt.year
    yearly = d.groupby("year").apply(lambda g: (g["db_mean"] < FLOOR).mean(), include_groups=False)
    print(f"     frac<{FLOOR} by year:", end="")
    for yr, f in yearly.items():
        marker = "*" if f >= ONSET_THRESH else ""
        print(f"  {yr}:{f:.0%}{marker}", end="")
    print()

    # Onset year: first year where frac_below >= threshold for 2+ consecutive years
    onset = None
    years = sorted(yearly.index)
    for i, yr in enumerate(years):
        if yearly[yr] >= ONSET_THRESH:
            # Check if previous year was also stuck or this is year 1
            if i == 0 or yearly.get(years[i-1], 0) < ONSET_THRESH:
                onset = yr
            break
    if onset is None:
        print(f"     Onset: always stuck from first observation ({d.date.min().date()})")
    else:
        first_obs = d.date.min().year
        if onset == first_obs:
            print(f"     Onset: always stuck from first observation ({d.date.min().date()})")
        else:
            print(f"     Onset: stuck from ~{onset} (frac_below crossed {ONSET_THRESH:.0%})")
    print()

print("=" * 70)
print("  OPEN QUESTION — Esri(opущены крыши) ↔ SAR(тихие = полные)")
print("=" * 70)
print()
print("  Observation: 7 LOW-STUCK tanks show systematically low SAR dB")
print("  (−10 to −5 dB), which the volume formula interprets as full.")
print("  ESRI World Imagery (z=19) shows roofs in lowered position (empty).")
print("  This is a contradiction. Hypotheses (no winner selected):")
print()
print("  H1 [Specular/mirror roof]: New metallic floating roofs act as")
print("      flat specular reflectors → near-zero SAR return regardless of fill.")
print("      Would explain: constant low dB unrelated to fill level.")
print()
print("  H2 [Decommissioned/maintenance]: Tanks taken out of service;")
print("      roof rests on floor (drained); low dB from flat floor geometry.")
print("      Would explain: no cycling, onset year.")
print()
print("  H3 [Site flooding / background contamination]: Shatt-al-Arab")
print("      seasonal inundation of surrounding area → low background dB")
print("      bleeds into tank measurement window.")
print("      Would explain: correlated with river level / wet season.")
print()
print("  Resolution path: SAR time series + Esri date-matched imagery +")
print("  river gauge data for Shatt-al-Arab at Basra.")
print()
print("  STATUS: OPEN — does not block series or events analysis.")
print("  stuck_flag=1 in pertank_v3.csv; excluded from calibration.")
