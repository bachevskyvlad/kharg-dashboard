"""
events_v3.py — Al-Faw events on three series (raw24 / live17 / norm17).

Events: geopolitical + anchor episodes from polarity verdict.
Estimator: Δvol over event window [t0, t1].
Placebo p90: distribution of |Δvol| over 20 non-event 90-day windows (step 30d).
Verdict: sign correct AND |Δ| > p90 → SIGNAL; sign correct only → WEAK; else → WRONG.

Usage:
  python events_v3.py [--force]   # --force to write CSV
"""
import os, sys
from pathlib import Path
import pandas as pd
import numpy as np

DRY = "--force" not in sys.argv

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"

# Events table — canonical list
# sign: DRAW = storage decreases (negative Δvol); BUILD = storage increases
EVENTS = [
    # ── Polarity-anchor episodes (canonical from verdict) ──
    {"id": "basra_2021q3",    "t0": "2021-07-01", "t1": "2021-09-30", "sign": "DRAW",
     "note": "Iraq post-OPEC+ ramp-up Jul-Sep 2021; Basra_pw peak exports",
     "url": "https://momr.opec.org"},
    {"id": "basra_2022q2",    "t0": "2022-05-01", "t1": "2022-08-31", "sign": "DRAW",
     "note": "Iraq production push post-Ukraine May-Aug 2022; Basra_pw peak",
     "url": "https://momr.opec.org"},
    {"id": "basra_2023h1",    "t0": "2023-01-01", "t1": "2023-02-28", "sign": "DRAW",
     "note": "Basra_pw peak Jan-Feb 2023 (pre-cut)",
     "url": "https://momr.opec.org"},
    {"id": "opec_cuts_2025h1","t0": "2025-01-01", "t1": "2025-06-30", "sign": "BUILD",
     "note": "Iraq voluntary OPEC+ makeup cuts H1 2025 → less loading",
     "url": "https://www.opec.org/opec_web/en/press_room/7438.htm"},
    # ── Geopolitical episodes ──
    {"id": "jcpoa_2015",      "t0": "2015-07-14", "t1": "2015-10-14", "sign": "BUILD",
     "note": "JCPOA signed July 2015 → anticipatory storage build (Iran/Iraq)",
     "url": "https://www.iaea.org/newscenter/focus/iran/iaea-and-iran-jcpoa"},
    {"id": "jcpoa_lift_2016", "t0": "2016-01-16", "t1": "2016-04-16", "sign": "DRAW",
     "note": "JCPOA implementation day Jan 2016 → ramp-up exports",
     "url": "https://www.iaea.org/newscenter/pressreleases/iaea-verifies-iran-has-met-commitments"},
    {"id": "reimpose_2018",   "t0": "2018-05-08", "t1": "2018-08-08", "sign": "BUILD",
     "note": "US re-imposes Iran sanctions May 2018 → curtailed exports → build",
     "url": "https://home.treasury.gov/news/press-releases/sm0409"},
    {"id": "waivers_2019",    "t0": "2019-05-02", "t1": "2019-08-02", "sign": "DRAW",
     "note": "Waiver end May 2019 → Iran volumes fall further, Iraq compensates",
     "url": "https://home.treasury.gov/news/press-releases/sm667"},
    {"id": "covid_2020",      "t0": "2020-03-11", "t1": "2020-06-11", "sign": "BUILD",
     "note": "COVID demand collapse Mar 2020 → exports fall → storage builds",
     "url": "https://www.who.int/director-general/speeches/detail/who-director-general-s-opening-remarks-at-the-media-briefing-on-covid-19---11-march-2020"},
    {"id": "war_2026",        "t0": "2026-02-01", "t1": "2026-05-01", "sign": "DRAW",
     "note": "Regional conflict 2026 → supply uncertainty → drawdown pressure",
     "url": ""},
]

SERIES = ["vol_raw24_mb", "vol_live17_mb", "vol_norm17_mb"]
SERIES_SHORT = ["raw24", "live17", "norm17"]


def nearest_date(df, target):
    """Return nearest available date within ±20 days of target."""
    t = pd.Timestamp(target)
    diff = (df["date"] - t).abs()
    idx = diff.idxmin()
    if diff[idx] > pd.Timedelta(days=20):
        return None, None
    return df.loc[idx, "date"], idx


def delta_vol(df, t0_str, t1_str, col):
    d0, i0 = nearest_date(df, t0_str)
    d1, i1 = nearest_date(df, t1_str)
    if d0 is None or d1 is None or d0 >= d1:
        return None, None, None
    v0 = df.loc[i0, col]
    v1 = df.loc[i1, col]
    if pd.isna(v0) or pd.isna(v1):
        return None, d0, d1
    return v1 - v0, d0, d1


def placebo_p90(df, col, event_windows):
    """90th percentile |Δvol| over 20 peaceful 90-day windows."""
    # Build list of dates not within any event window
    event_ranges = [(pd.Timestamp(e["t0"]) - pd.Timedelta(days=30),
                     pd.Timestamp(e["t1"]) + pd.Timedelta(days=30))
                    for e in event_windows]

    def in_event(d):
        return any(lo <= d <= hi for lo, hi in event_ranges)

    peaceful = df[~df["date"].apply(in_event)].copy().reset_index(drop=True)
    deltas = []
    step = 30
    for i in range(0, len(peaceful) - 1, step):
        row0 = peaceful.iloc[i]
        # Find row ~90 days later
        target = row0["date"] + pd.Timedelta(days=90)
        diff = (peaceful["date"] - target).abs()
        j = diff.idxmin()
        if diff[j] > pd.Timedelta(days=15): continue
        v0 = row0[col]
        v1 = peaceful.loc[j, col]
        if pd.isna(v0) or pd.isna(v1): continue
        deltas.append(abs(v1 - v0))
        if len(deltas) >= 20: break

    if not deltas:
        return None
    return float(np.percentile(deltas, 90))


def main():
    # Load series_v3 CSV
    csv_path = OUT_DIR / "iraq_alfaw_daily_v3.csv"
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found. Run series_v3.py --force first.")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    print("=== Al-Faw events v3 — three series ===")
    print(f"  Loaded {len(df)} dates from {csv_path.name}")
    print(f"  Date range: {df.date.min().date()} – {df.date.max().date()}")
    print()

    # Placebo p90 per series
    placebo = {}
    for col in SERIES:
        if col not in df.columns:
            placebo[col] = None
            continue
        p = placebo_p90(df, col, EVENTS)
        placebo[col] = p
        sname = SERIES_SHORT[SERIES.index(col)]
        print(f"  Placebo p90 [{sname}]: {p:.3f} Mn bbl" if p else f"  Placebo p90 [{sname}]: N/A")

    print()
    print("=" * 105)
    print(f"  {'event':<20} {'exp':>5}  {'Δraw24':>8} {'vrd':>6}  {'Δlive17':>8} {'vrd':>6}  {'Δnorm17':>8} {'vrd':>6}  note")
    print("=" * 105)

    results = []
    for ev in EVENTS:
        row = {"id": ev["id"], "sign": ev["sign"], "note": ev["note"], "url": ev["url"]}
        cells = []
        for col, sname in zip(SERIES, SERIES_SHORT):
            if col not in df.columns:
                row[f"delta_{sname}"] = None
                row[f"verdict_{sname}"] = "NO_DATA"
                cells.append(f"{'N/A':>8} {'N/A':>6}")
                continue
            delta, d0, d1 = delta_vol(df, ev["t0"], ev["t1"], col)
            if delta is None:
                row[f"delta_{sname}"] = None
                row[f"verdict_{sname}"] = "NO_DATA"
                cells.append(f"{'N/A':>8} {'N/A':>6}")
                continue
            p90 = placebo[col]
            sign_ok = (ev["sign"] == "DRAW" and delta < 0) or (ev["sign"] == "BUILD" and delta > 0)
            mag_ok  = p90 is not None and abs(delta) > p90
            verdict = "SIGNAL" if (sign_ok and mag_ok) else ("WEAK" if sign_ok else "WRONG")
            row[f"delta_{sname}"] = round(delta, 4)
            row[f"verdict_{sname}"] = verdict
            cells.append(f"{delta:>+8.3f} {verdict:>6}")

        print(f"  {ev['id']:<20} {ev['sign']:>5}  {'  '.join(cells)}  {ev['note'][:50]}")
        results.append(row)

    print()
    # Summary table
    for sname in SERIES_SHORT:
        vcol = f"verdict_{sname}"
        verts = [r[vcol] for r in results if r.get(vcol) not in (None,"NO_DATA")]
        if not verts: continue
        s = sum(1 for v in verts if v=="SIGNAL")
        w = sum(1 for v in verts if v=="WEAK")
        x = sum(1 for v in verts if v=="WRONG")
        n = sum(1 for v in verts if v=="NO_DATA") + (len(results)-len(verts))
        print(f"  {sname:>10}: SIGNAL={s}  WEAK={w}  WRONG={x}  NO_DATA={n}/{len(results)}")

    # Write CSV
    out = OUT_DIR / "iraq_alfaw_events_v3.csv"
    if not DRY:
        pd.DataFrame(results).to_csv(out, index=False)
        print(f"\n  → {out}")
    else:
        print(f"\nDRY-RUN — pass --force to write {out.name}")


if __name__ == "__main__":
    main()
