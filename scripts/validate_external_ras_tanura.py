"""
validate_external_ras_tanura.py — External validation of Ras Tanura SAR series.

Tasks:
  1. Ingest JODI-Oil bulk CSV (Saudi Arabia, Crude Oil)
  2. Correlate monthly RT storage vs JODI exports / closing stocks
  3. Event study: 5 events (2026 conflict) + placebo control + Kharg negative control
  4. Write diagnostics/ras_tanura/VALIDATION_EXTERNAL.md

Usage:
  python scripts/validate_external_ras_tanura.py

JODI download (one-time, cached):
  The script auto-downloads from jodidata.org on first run.
  If the network is blocked, download manually and place at:
    data/external/jodi/jodi_oil_latest.csv
  Source: https://www.jodidata.org/_resources/files/downloads/oil-data/jodi_oil_csv_beta.zip
  Unzip and rename the CSV to jodi_oil_latest.csv.
"""

import os
import sys
import json
import zipfile
import datetime
import warnings
import requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy import stats

warnings.filterwarnings("ignore")

# ── Constants ─────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

JODI_DIR     = os.path.join(os.path.dirname(__file__), "..", "data", "external", "jodi")
JODI_CACHE   = os.path.join(JODI_DIR, "jodi_oil_latest.csv")
JODI_URLS = [
    "https://www.jodidata.org/_resources/files/downloads/oil-data/jodi_oil_world_primary_csv.zip",
    "https://www.jodidata.org/_resources/files/downloads/oil-data/world_primary_csv.zip",
    "https://www.jodidata.org/_resources/files/downloads/oil-data/jodi_oil_csv_beta.zip",
    "https://www.jodidb.org/_resources/files/downloads/oil-data/jodi_oil_csv_beta.zip",
]
JODI_URL     = JODI_URLS[0]
JODI_SAUDI_OUT = os.path.join(os.path.dirname(__file__), "..", "data", "external", "jodi_saudi_crude.csv")

OUT_DIR      = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "ras_tanura")
REPORT_PATH  = os.path.join(OUT_DIR, "VALIDATION_EXTERNAL.md")
PLOT_DIR     = os.path.join(OUT_DIR, "plots")

WINDOW_START = "2017-01"   # RT backfill start
CORR_WINDOW  = "2024-01"   # recent window for focused correlation

# Events table — (id, date, label, expected_direction, news_note)
# Volumes from public reporting; exact sources noted per event.
EVENTS = [
    ("E1", "2026-03-02", "Drone attack, RT ops suspended",
     "build_or_plateau",
     "Houthi drone strike; Saudi Aramco halted loading operations (Bloomberg 2026-03-02)"),
    ("E2", "2026-04-15", "Mid-shutdown plateau (~4 months)",
     "high_level",
     "Estimated midpoint of ~4-month suspension period; no loading reported"),
    ("E3", "2026-06-27", "Operations resume; ~4 VLCC loading (~8 Mb)",
     "draw",
     "Aramco resumes exports; Reuters/Kpler cited ~4 VLCCs loaded ≈8 Mb (2026-06-26/27)"),
    ("E4", "2026-07-08", "Export rate ~90% pre-war level",
     "draw_or_low",
     "Trade sources: Saudi exports recovering to ~5.5 mbpd (2026-07 first decade)"),
    ("E5", "2026-09-21", "East-West pipeline attack; STS Sohar ~60 Mb",
     "high_volatility",
     "Pipeline disruption; ~14 Mb/day loadings via STS Sohar reported (2026-09-20/21)"),
]

NEWS_VOLUMES = {
    "E3": 8.0,   # Mb — VLCC loading volume at resumption
    "E5": 60.0,  # Mb — STS Sohar transfer volume
}

# ── Supabase fetch ─────────────────────────────────────────────────────────────

def _sb_fetch(table, select, filters=None, order=None):
    """Minimal Supabase REST fetch — no client library needed."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
    if filters:
        for k, v in filters.items():
            url += f"&{k}=eq.{v}"
    if order:
        url += f"&order={order}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    rows = []
    offset = 0
    while True:
        r = requests.get(url + f"&offset={offset}&limit=1000", headers=headers, timeout=30)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def load_rt_monthly():
    """Load RT daily volume from Supabase, aggregate to monthly."""
    print("[validate] Loading RT terminal_volume_timeseries...")
    rows = _sb_fetch(
        "terminal_volume_timeseries",
        "date,vol_mn_bbl",
        {"terminal_id": "ras_tanura", "aggregation": "daily", "smoothed": "false"},
        "date.asc",
    )
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["vol_mn_bbl"] = df["vol_mn_bbl"].astype(float)
    df = df[df["date"] >= WINDOW_START].copy()
    # Monthly: mean level + end-of-month value for delta
    df["month"] = df["date"].dt.to_period("M")
    monthly_mean = df.groupby("month")["vol_mn_bbl"].mean().rename("rt_level_mb")
    monthly_eom  = df.groupby("month")["vol_mn_bbl"].last().rename("rt_eom_mb")
    m = pd.concat([monthly_mean, monthly_eom], axis=1)
    m["rt_delta_mb"] = m["rt_eom_mb"].diff()
    m.index = m.index.to_timestamp()
    print(f"  RT monthly: {len(m)} rows, {m.index[0].date()} → {m.index[-1].date()}")
    return m, df


def load_kharg_monthly():
    """Load Kharg daily volume from Supabase, aggregate to monthly."""
    print("[validate] Loading Kharg volume_timeseries...")
    rows = _sb_fetch(
        "volume_timeseries",
        "date,vol_mn_bbl",
        {"aggregation": "daily", "smoothed": "false"},
        "date.asc",
    )
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["vol_mn_bbl"] = df["vol_mn_bbl"].astype(float)
    df = df[df["date"] >= WINDOW_START].copy()
    df["month"] = df["date"].dt.to_period("M")
    monthly_mean = df.groupby("month")["vol_mn_bbl"].mean().rename("kharg_level_mb")
    monthly_eom  = df.groupby("month")["vol_mn_bbl"].last().rename("kharg_eom_mb")
    m = pd.concat([monthly_mean, monthly_eom], axis=1)
    m["kharg_delta_mb"] = m["kharg_eom_mb"].diff()
    m.index = m.index.to_timestamp()
    print(f"  Kharg monthly: {len(m)} rows")
    return m, df


# ── JODI ingest ───────────────────────────────────────────────────────────────

def download_jodi():
    os.makedirs(JODI_DIR, exist_ok=True)
    if os.path.exists(JODI_CACHE):
        age_days = (datetime.datetime.now().timestamp() - os.path.getmtime(JODI_CACHE)) / 86400
        print(f"[validate] JODI cache found ({age_days:.0f} days old) — using cached file")
        return True
    print(f"[validate] Downloading JODI bulk CSV ...")
    last_err = None
    try:
        r = None
        for url in JODI_URLS:
            print(f"  Trying {url} ...")
            try:
                r = requests.get(url, timeout=60, stream=True)
                r.raise_for_status()
                print(f"  OK: {url}")
                break
            except Exception as e:
                print(f"  failed ({type(e).__name__}): {e}")
                last_err = e
                r = None
        if r is None:
            raise last_err
        zip_path = os.path.join(JODI_DIR, "jodi_oil.zip")
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        with zipfile.ZipFile(zip_path) as z:
            csvs = [n for n in z.namelist() if n.endswith(".csv")]
            if not csvs:
                raise ValueError(f"No CSV in zip: {z.namelist()}")
            with z.open(csvs[0]) as src, open(JODI_CACHE, "wb") as dst:
                dst.write(src.read())
        os.remove(zip_path)
        print(f"  Downloaded and extracted → {JODI_CACHE}")
        return True
    except Exception as e:
        print(f"  [!] JODI download failed: {e}")
        print(f"  Manual steps:")
        print(f"    1. Open https://www.jodidata.org/oil/database/data-downloads.aspx")
        print(f"    2. Download 'JODI Oil World - Primary' CSV/zip")
        print(f"    3. Unzip and place the CSV at: {JODI_CACHE}")
        return False


def load_jodi_saudi():
    """Parse JODI bulk CSV → Saudi Arabia crude oil monthly series."""
    if not os.path.exists(JODI_CACHE):
        raise FileNotFoundError(f"JODI cache not found: {JODI_CACHE}\nRun with internet access or place file manually.")

    print("[validate] Parsing JODI bulk CSV...")
    df = pd.read_csv(JODI_CACHE, low_memory=False)
    print(f"  Columns: {list(df.columns)}")
    print(f"  Shape: {df.shape}")

    # Normalise column names (JODI schema varies slightly between releases)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Find country / product / flow columns
    col_map = {}
    for candidate in ["country", "ref_area", "country_name"]:
        if candidate in df.columns:
            col_map["country"] = candidate; break
    for candidate in ["product", "energy_product", "commodity"]:
        if candidate in df.columns:
            col_map["product"] = candidate; break
    for candidate in ["flow", "transaction", "flow_breakdown"]:
        if candidate in df.columns:
            col_map["flow"] = candidate; break
    for candidate in ["value", "obs_value", "quantity"]:
        if candidate in df.columns:
            col_map["value"] = candidate; break
    for candidate in ["time_period", "period", "date", "month", "ref_period"]:
        if candidate in df.columns:
            col_map["period"] = candidate; break
    for candidate in ["unit", "unit_measure", "unit_name"]:
        if candidate in df.columns:
            col_map["unit"] = candidate; break

    print(f"  Column mapping: {col_map}")

    # Filter Saudi Arabia, Crude Oil
    # JODI uses ISO2 country codes (SA) or full names — try both
    country_vals = df[col_map["country"]].str.upper()
    sa_mask = (country_vals == "SA") | country_vals.str.contains("SAUDI", na=False)
    # JODI product codes: CRUDEOIL=crude oil, TOTCRUDE=total crude+condensate
    # Use TOTCRUDE for exports/stocks (aggregate), CRUDEOIL as fallback
    prod_vals = df[col_map["product"]].str.upper()
    crude_mask = prod_vals.isin(["TOTCRUDE", "CRUDEOIL"])
    df_sa = df[sa_mask & crude_mask].copy()
    print(f"  Saudi crude rows: {len(df_sa)}")
    if len(df_sa) == 0:
        print("  [!] No Saudi crude rows found — dumping unique country/product values:")
        print(df[col_map["country"]].unique()[:30])
        print(df[col_map["product"]].unique()[:30])
        raise ValueError("No Saudi Arabia / crude oil data in JODI file")

    # Unit conversion: JODI kb → Mb (divide by 1000); kbd → Mbpd (divide by 1000)
    unit_col = col_map.get("unit")
    if unit_col:
        units = df_sa[unit_col].str.upper().unique()
        print(f"  Units found: {units}")
        # JODI standard: kb = thousand barrels, kbd = thousand barrels/day
        # Some releases use "KBD" for stock flows too — check flow names
    else:
        print("  [!] No unit column found — assuming kb/kbd")

    flow_col    = col_map["flow"]
    period_col  = col_map["period"]
    value_col   = col_map["value"]

    flows_avail = df_sa[flow_col].unique()
    print(f"  Flow values: {flows_avail}")

    # JODI flow codes (short form): TOTEXPSB=exports, CLOSTLV=closing stocks level,
    # INDPROD=production, REFINOBS=refinery intake. Long-form names as fallback.
    FLOW_MAP = {
        "exports_mbpd":           ["TOTEXPSB", "EXPORTS", "EXPORT"],
        "closing_stocks_mb":      ["CLOSTLV", "CLOSTK", "CSTOCK", "CLOSINGSTK", "CLOSING STOCKS", "CLOSING_STOCKS"],
        "production_mbpd":        ["INDPROD", "PRODUCTION", "PRODUC"],
        "refinery_intake_mbpd":   ["REFINOBS", "REFINERY INTAKE", "REFINERYINTAKE", "REF_INTAKE"],
    }

    records = {}
    flow_vals_upper = df_sa[flow_col].str.upper()
    for out_col, keywords in FLOW_MAP.items():
        matched = df_sa[flow_vals_upper.isin(keywords)]
        if len(matched) == 0:
            print(f"  [!] Flow not found for {out_col}: tried {keywords}")
            continue
        # Parse period → month
        s = matched[[period_col, value_col]].copy()
        s[period_col] = pd.to_datetime(s[period_col].astype(str).str[:7], format="%Y-%m", errors="coerce")
        s = s.dropna(subset=[period_col])
        s[value_col] = pd.to_numeric(s[value_col], errors="coerce")
        s = s.dropna(subset=[value_col])
        s = s[(s[value_col] > 0)]  # JODI uses 0 for missing in some releases
        monthly = s.groupby(period_col)[value_col].mean()
        # Convert kb → Mb (stocks) or kbd → Mbpd (flows)
        monthly = monthly / 1000.0
        records[out_col] = monthly
        print(f"  {out_col}: {len(monthly)} months, last={monthly.index[-1].strftime('%Y-%m') if len(monthly) else 'N/A'}")

    jodi = pd.DataFrame(records)
    jodi.index.name = "month"
    jodi = jodi[jodi.index >= WINDOW_START].copy()
    jodi["exports_delta_mbpd"] = jodi.get("exports_mbpd", pd.Series(dtype=float)).diff()
    jodi["closing_stocks_delta_mb"] = jodi.get("closing_stocks_mb", pd.Series(dtype=float)).diff()

    # Save processed file
    jodi.to_csv(JODI_SAUDI_OUT)
    print(f"  Saved → {JODI_SAUDI_OUT}")

    # Sanity check: 2026 exports should be below 2025 norm
    if "exports_mbpd" in jodi.columns:
        exp_2025 = jodi.loc["2025", "exports_mbpd"].mean() if "2025" in jodi.index.year.astype(str).values else np.nan
        exp_2026 = jodi.loc["2026", "exports_mbpd"].mean() if "2026" in jodi.index.year.astype(str).values else np.nan
        print(f"  SANITY exports_mbpd: 2025 mean={exp_2025:.2f}, 2026 mean={exp_2026:.2f}")
        if not np.isnan(exp_2026) and not np.isnan(exp_2025):
            if exp_2026 >= exp_2025:
                print("  [!] SANITY WARN: 2026 exports >= 2025 — check flow filter (war should show lower exports)")
            else:
                print("  [OK] SANITY: 2026 exports < 2025 — consistent with conflict disruption")
    return jodi


# ── Correlation ───────────────────────────────────────────────────────────────

def compute_correlations(rt_m, jodi):
    """Pearson + Spearman with cross-lags, multiple windows."""
    results = []

    def _pair(x, y, label, window_label, lag=0):
        if lag != 0:
            x = x.shift(lag)
        combined = pd.concat([x, y], axis=1).dropna()
        if len(combined) < 6:
            return None
        a, b = combined.iloc[:, 0], combined.iloc[:, 1]
        r_p, p_p = stats.pearsonr(a, b)
        r_s, p_s = stats.spearmanr(a, b)
        results.append({
            "pair": label,
            "window": window_label,
            "lag_months": lag,
            "n": len(combined),
            "pearson_r": round(r_p, 3),
            "pearson_p": round(p_p, 4),
            "spearman_r": round(r_s, 3),
            "spearman_p": round(p_s, 4),
        })

    merged = rt_m.join(jodi, how="inner")

    windows = {
        "full": merged,
        f"{CORR_WINDOW}+": merged[merged.index >= CORR_WINDOW],
        "2026": merged[merged.index >= "2026-01"],
    }

    pairs = [
        ("rt_delta_mb", "exports_mbpd",         "Δstorage_RT vs JODI_exports"),
        ("rt_delta_mb", "closing_stocks_delta_mb","Δstorage_RT vs ΔJODI_stocks"),
        ("rt_level_mb", "closing_stocks_mb",     "RT_level vs JODI_stocks_level"),
    ]

    for wlabel, wdf in windows.items():
        for xcol, ycol, plabel in pairs:
            if xcol not in wdf.columns or ycol not in wdf.columns:
                continue
            for lag in range(-2, 3):
                _pair(wdf[xcol], wdf[ycol], plabel, wlabel, lag)

    return pd.DataFrame(results)


# ── Event study ───────────────────────────────────────────────────────────────

PASS_INTERVAL_DAYS = 6   # Sentinel-1 revisit

def _window_delta(daily_df, center_date, n_passes=3):
    """Return Δvol_mb in a ±n_passes window around center_date."""
    days = n_passes * PASS_INTERVAL_DAYS
    d = pd.to_datetime(center_date)
    sub = daily_df[(daily_df["date"] >= d - pd.Timedelta(days=days)) &
                   (daily_df["date"] <= d + pd.Timedelta(days=days))].copy()
    if len(sub) < 2:
        return np.nan, sub
    delta = sub["vol_mn_bbl"].iloc[-1] - sub["vol_mn_bbl"].iloc[0]
    return delta, sub


def event_study(daily_df, events, label="RT"):
    """Event study for a daily series DataFrame with cols [date, vol_mn_bbl]."""
    rows = []
    for eid, edate, elabel, expect, _ in events:
        delta, sub = _window_delta(daily_df, edate)
        rows.append({
            "id": eid, "date": edate, "event": elabel,
            "expected": expect, "delta_mb": round(float(delta), 3) if not np.isnan(delta) else np.nan,
            "n_obs": len(sub), "series": label,
        })
    return pd.DataFrame(rows)


def placebo_test(daily_df, n=10, seed=42):
    """
    Sample n random dates from 2024-2025 (peaceful period).
    Compute |Δ| in ±3-pass window for each.
    Returns p90 of |Δ| distribution.
    """
    rng = np.random.default_rng(seed)
    pool = daily_df[(daily_df["date"] >= "2024-01-01") &
                    (daily_df["date"] <= "2025-12-31")]["date"]
    if len(pool) < n:
        pool = daily_df["date"]
    sample_dates = pool.sample(n, random_state=seed).values
    deltas = []
    for d in sample_dates:
        dlt, _ = _window_delta(daily_df, d)
        if not np.isnan(dlt):
            deltas.append(abs(dlt))
    if not deltas:
        return np.nan
    return float(np.percentile(deltas, 90))


def _sign_verdict(delta, expected, placebo_p90):
    """Return (sign_ok, magnitude_ok, verdict_str)."""
    if np.isnan(delta):
        return False, False, "NO_DATA"
    sign_ok = False
    if expected in ("build_or_plateau", "high_level"):
        sign_ok = delta >= 0
    elif expected in ("draw", "draw_or_low"):
        sign_ok = delta < 0
    elif expected == "high_volatility":
        sign_ok = abs(delta) > 1.0   # any significant move
    magnitude_ok = abs(delta) > placebo_p90 if not np.isnan(placebo_p90) else None
    verdict = "✓" if (sign_ok and magnitude_ok) else ("~" if sign_ok else "✗")
    return sign_ok, magnitude_ok, verdict


# ── Plots ─────────────────────────────────────────────────────────────────────

def plot_monthly_panel(rt_m, jodi, events, out_path):
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.patch.set_facecolor("#0d1117")

    for ax in axes:
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#8b949e")
        ax.spines[:].set_color("#30363d")

    event_dates = [pd.to_datetime(e[1]) for e in events]
    event_labels = [e[0] for e in events]

    # Panel 1: RT storage
    ax = axes[0]
    ax.plot(rt_m.index, rt_m["rt_level_mb"], color="#58a6ff", linewidth=1.5, label="RT level (Mb)")
    ax.set_ylabel("RT Storage (Mb)", color="#e6edf3")
    ax.set_title("Ras Tanura SAR Storage", color="#e6edf3", fontsize=10)
    for d, lbl in zip(event_dates, event_labels):
        ax.axvline(d, color="#f85149", alpha=0.6, linewidth=1, linestyle="--")
        ax.text(d, ax.get_ylim()[1] * 0.95 if ax.get_ylim()[1] else 1, lbl,
                color="#f85149", fontsize=7, rotation=90, va="top", ha="right")

    # Panel 2: JODI exports
    ax = axes[1]
    if "exports_mbpd" in jodi.columns:
        ax.bar(jodi.index, jodi["exports_mbpd"], width=20, color="#3fb950", alpha=0.8, label="Exports (Mbpd)")
        ax.set_ylabel("Saudi Exports (Mbpd)", color="#e6edf3")
        ax.set_title("JODI Saudi Arabia Crude Exports", color="#e6edf3", fontsize=10)
        for d in event_dates:
            ax.axvline(d, color="#f85149", alpha=0.6, linewidth=1, linestyle="--")

    # Panel 3: JODI closing stocks
    ax = axes[2]
    if "closing_stocks_mb" in jodi.columns:
        ax.plot(jodi.index, jodi["closing_stocks_mb"], color="#d2a8ff", linewidth=1.5, label="Closing stocks (Mb)")
        ax.set_ylabel("Saudi Stocks (Mb)", color="#e6edf3")
        ax.set_title("JODI Saudi Arabia Closing Stocks", color="#e6edf3", fontsize=10)
        for d in event_dates:
            ax.axvline(d, color="#f85149", alpha=0.6, linewidth=1, linestyle="--")

    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axes[2].xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.setp(axes[2].xaxis.get_majorticklabels(), rotation=45, ha="right", color="#8b949e")

    plt.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Plot → {out_path}")


def plot_event_zoom(daily_df, eid, edate, elabel, delta, out_path):
    d = pd.to_datetime(edate)
    days = 4 * PASS_INTERVAL_DAYS
    sub = daily_df[(daily_df["date"] >= d - pd.Timedelta(days=days)) &
                   (daily_df["date"] <= d + pd.Timedelta(days=days))]
    if len(sub) < 2:
        return

    fig, ax = plt.subplots(figsize=(8, 3.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")
    ax.tick_params(colors="#8b949e")
    ax.spines[:].set_color("#30363d")

    ax.plot(sub["date"], sub["vol_mn_bbl"], color="#58a6ff", linewidth=2, marker="o", markersize=4)
    ax.axvline(d, color="#f85149", linewidth=1.5, linestyle="--", label=f"{eid}: {edate}")
    ax.axhline(sub["vol_mn_bbl"].mean(), color="#8b949e", linewidth=0.8, linestyle=":")
    delta_str = f"Δ={delta:+.2f} Mb" if not np.isnan(delta) else "Δ=N/A"
    ax.set_title(f"{eid} — {elabel}\n{delta_str}", color="#e6edf3", fontsize=9)
    ax.set_ylabel("RT Storage (Mb)", color="#e6edf3")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", color="#8b949e")
    plt.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()


# ── Report ─────────────────────────────────────────────────────────────────────

def write_report(corr_df, rt_events, kharg_events, placebo_p90, rt_m, jodi):
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()

    # Determine best lags per pair
    def best_lag(pair_label, window="full"):
        sub = corr_df[(corr_df["pair"] == pair_label) & (corr_df["window"] == window)]
        if sub.empty:
            return "N/A", "N/A"
        best = sub.loc[sub["pearson_r"].abs().idxmax()]
        return int(best["lag_months"]), round(best["pearson_r"], 3)

    lines = [
        f"# Ras Tanura — External Validation Report",
        f"",
        f"**Generated**: {today}  ",
        f"**Series**: `terminal_volume_timeseries` (DESC-35, ~6d revisit)  ",
        f"**External reference**: JODI-Oil World Database (Saudi Arabia, Crude Oil)  ",
        f"",
        f"---",
        f"",
        f"## 1. Correlation: RT Storage vs JODI Saudi Crude",
        f"",
        f"Monthly aggregation: RT mean level + end-of-month delta.",
        f"JODI units: exports/production/refinery intake in Mbpd; closing stocks in Mb.",
        f"",
        f"### 1a. Full history ({WINDOW_START} → latest)",
        f"",
    ]

    # Correlation table
    full_corr = corr_df[(corr_df["window"] == "full") & (corr_df["lag_months"] == 0)]
    lines += [
        "| Pair | N | Pearson r | p | Spearman r | p |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in full_corr.iterrows():
        lines.append(
            f"| {row['pair']} | {row['n']} | {row['pearson_r']} | {row['pearson_p']:.4f} "
            f"| {row['spearman_r']} | {row['spearman_p']:.4f} |"
        )

    lines += [
        f"",
        f"### 1b. Cross-lag analysis (best lag per pair, full history)",
        f"",
        f"| Pair | Best lag (months) | Pearson r at best lag |",
        f"|---|---|---|",
    ]
    for pair_label in corr_df["pair"].unique():
        lag, r = best_lag(pair_label, "full")
        lines.append(f"| {pair_label} | {lag} | {r} |")

    lines += [
        f"",
        f"### 1c. Recent window ({CORR_WINDOW} → latest)",
        f"",
        f"| Pair | N | Pearson r | p | Spearman r | p |",
        f"|---|---|---|---|---|---|",
    ]
    rec_corr = corr_df[(corr_df["window"] == f"{CORR_WINDOW}+") & (corr_df["lag_months"] == 0)]
    for _, row in rec_corr.iterrows():
        lines.append(
            f"| {row['pair']} | {row['n']} | {row['pearson_r']} | {row['pearson_p']:.4f} "
            f"| {row['spearman_r']} | {row['spearman_p']:.4f} |"
        )

    lines += [
        f"",
        f"### 1d. Conflict window (2026)",
        f"",
        f"| Pair | N | Pearson r | p | Spearman r | p |",
        f"|---|---|---|---|---|---|",
    ]
    conf_corr = corr_df[(corr_df["window"] == "2026") & (corr_df["lag_months"] == 0)]
    for _, row in conf_corr.iterrows():
        lines.append(
            f"| {row['pair']} | {row['n']} | {row['pearson_r']} | {row['pearson_p']:.4f} "
            f"| {row['spearman_r']} | {row['spearman_p']:.4f} |"
        )

    lines += [
        f"",
        f"> **Interpretation note**: Δstorage_RT vs JODI_exports is expected negative",
        f"> (RT builds when exports are low, draws when exports are high).",
        f"> RT_level vs JODI_stocks_level is shown for illustration only — both series",
        f"> share a common slow trend and the correlation is not causal.",
        f"",
        f"![Monthly panel](plots/monthly_panel.png)",
        f"",
        f"---",
        f"",
        f"## 2. Event Study",
        f"",
        f"Window: ±3 SAR passes (~±18 days) around each event date.",
        f"Δstorage = last observed value minus first observed value in window.",
        f"",
        f"**Placebo p90 (|Δ| over 10 random 2024–2025 dates)**: {placebo_p90:.2f} Mb",
        f"",
        f"### 2a. Ras Tanura — event verdicts",
        f"",
        f"| # | Date | Event | Expected | Δ (Mb) | Placebo p90 | Sign ✓? | Above p90? | Verdict |",
        f"|---|---|---|---|---|---|---|---|---|",
    ]

    for _, row in rt_events.iterrows():
        delta = row["delta_mb"]
        sign_ok, mag_ok, verdict = _sign_verdict(delta, row["expected"], placebo_p90)
        delta_str = f"{delta:+.2f}" if not np.isnan(delta) else "N/A"
        mag_str = "yes" if mag_ok else ("no" if mag_ok is not None else "N/A")
        lines.append(
            f"| {row['id']} | {row['date']} | {row['event']} | {row['expected']} "
            f"| {delta_str} | {placebo_p90:.2f} | {'yes' if sign_ok else 'no'} | {mag_str} | {verdict} |"
        )

    lines += [
        f"",
        f"#### Event zoom charts",
        f"",
    ]
    for _, row in rt_events.iterrows():
        lines.append(f"![{row['id']}](plots/event_{row['id']}.png)")
        lines.append(f"")

    lines += [
        f"#### News volume cross-check",
        f"",
        f"| Event | News volume | RT Δ | Notes |",
        f"|---|---|---|---|",
        f"| E3 (resumption) | ~8 Mb (4 VLCCs) | {rt_events[rt_events['id']=='E3']['delta_mb'].values[0] if len(rt_events[rt_events['id']=='E3']) else 'N/A':.2f} Mb | Loadings spread over days + concurrent production inflow |",
        f"| E5 (pipeline attack) | ~60 Mb STS Sohar | {rt_events[rt_events['id']=='E5']['delta_mb'].values[0] if len(rt_events[rt_events['id']=='E5']) else 'N/A':.2f} Mb (window only) | STS transfer redistributes, RT terminal may not fully reflect |",
        f"",
        f"---",
        f"",
        f"## 3. Kharg Negative Control",
        f"",
        f"E1–E5 are Saudi events. Kharg (Iran) should NOT show systematic response.",
        f"If Kharg Δ > placebo_p90 on the same events: signal is market-wide, not terminal-specific.",
        f"",
        f"| # | Date | Event | RT Δ | Kharg Δ | Kharg verdict |",
        f"|---|---|---|---|---|---|",
    ]

    for _, rt_row in rt_events.iterrows():
        k_row = kharg_events[kharg_events["id"] == rt_row["id"]]
        k_delta = k_row["delta_mb"].values[0] if len(k_row) else np.nan
        k_delta_str = f"{k_delta:+.2f}" if not np.isnan(k_delta) else "N/A"
        rt_delta = rt_row["delta_mb"]
        rt_delta_str = f"{rt_delta:+.2f}" if not np.isnan(rt_delta) else "N/A"
        k_above_p90 = abs(k_delta) > placebo_p90 if not np.isnan(k_delta) else False
        k_verdict = "⚠️ reacts" if k_above_p90 else "✓ quiet"
        lines.append(
            f"| {rt_row['id']} | {rt_row['date']} | {rt_row['event']} "
            f"| {rt_delta_str} | {k_delta_str} | {k_verdict} |"
        )

    # Final verdict
    rt_verdicts = []
    for _, row in rt_events.iterrows():
        _, _, v = _sign_verdict(row["delta_mb"], row["expected"], placebo_p90)
        rt_verdicts.append(v)
    confirmed = rt_verdicts.count("✓")
    partial   = rt_verdicts.count("~")
    failed    = rt_verdicts.count("✗") + rt_verdicts.count("NO_DATA")

    lines += [
        f"",
        f"---",
        f"",
        f"## 4. Final Verdict",
        f"",
        f"**Event study**: {confirmed}/5 events confirmed (✓), {partial} sign-only (~), {failed} not confirmed (✗).",
        f"",
    ]

    # Per-event verdict narrative
    for i, (_, row) in enumerate(rt_events.iterrows()):
        delta = row["delta_mb"]
        sign_ok, mag_ok, verdict = _sign_verdict(delta, row["expected"], placebo_p90)
        delta_str = f"{delta:+.2f} Mb" if not np.isnan(delta) else "N/A"
        if verdict == "✓":
            narrative = f"**Confirmed** — {delta_str}, sign matches expectation and exceeds placebo p90."
        elif verdict == "~":
            narrative = f"**Sign only** — {delta_str}, correct direction but magnitude within placebo range."
        elif verdict == "NO_DATA":
            narrative = "**No data** — insufficient SAR observations in window."
        else:
            narrative = f"**Not confirmed** — {delta_str}, sign or magnitude inconsistent with expectation."
        lines.append(f"- **{row['id']} ({row['date']})**: {narrative}")

    # Correlation verdict
    best_exp_pair = corr_df[
        (corr_df["pair"] == "Δstorage_RT vs JODI_exports") &
        (corr_df["window"] == "full") &
        (corr_df["lag_months"] == 0)
    ]
    exp_r = best_exp_pair["pearson_r"].values[0] if len(best_exp_pair) else np.nan
    exp_p = best_exp_pair["pearson_p"].values[0] if len(best_exp_pair) else np.nan

    if not np.isnan(exp_r):
        corr_verdict = "negative and statistically significant" if (exp_r < -0.2 and exp_p < 0.05) \
            else ("negative but not significant" if exp_r < 0 else "positive — unexpected direction")
        lines += [
            f"",
            f"**Correlation (Δstorage_RT vs JODI exports)**: r = {exp_r:.3f} (p = {exp_p:.4f}) — {corr_verdict}.",
        ]

    lines += [
        f"",
        f"**Overall assessment**: The SAR series {'reflects real terminal operations' if confirmed >= 3 else 'shows partial alignment with' if confirmed >= 2 else 'does not yet conclusively validate against'} external JODI/event data.",
        f"Discrepancies where present are noted individually above; no results were adjusted post-hoc.",
        f"",
        f"---",
        f"",
        f"*Generated by `scripts/validate_external_ras_tanura.py`*  ",
        f"*JODI data: jodidata.org (cached {today})*",
    ]

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"[validate] Report → {REPORT_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PLOT_DIR, exist_ok=True)

    # 1. JODI
    if not download_jodi():
        sys.exit(1)
    jodi = load_jodi_saudi()

    # 2. Supabase data
    rt_m, rt_daily = load_rt_monthly()
    kharg_m, kharg_daily = load_kharg_monthly()

    # 3. Correlation
    print("[validate] Computing correlations...")
    corr_df = compute_correlations(rt_m, jodi)
    corr_df.to_csv(os.path.join(OUT_DIR, "correlations.csv"), index=False)
    print(f"  {len(corr_df)} correlation rows")

    # 4. Event study
    print("[validate] Running event study...")
    rt_events    = event_study(rt_daily, EVENTS, "RT")
    kharg_events = event_study(kharg_daily, EVENTS, "Kharg")
    placebo_p90  = placebo_test(rt_daily)
    print(f"  Placebo p90 = {placebo_p90:.2f} Mb")

    # 5. Plots
    print("[validate] Generating plots...")
    plot_monthly_panel(rt_m, jodi, EVENTS, os.path.join(PLOT_DIR, "monthly_panel.png"))
    for _, row in rt_events.iterrows():
        plot_event_zoom(
            rt_daily, row["id"], row["date"], row["event"], row["delta_mb"],
            os.path.join(PLOT_DIR, f"event_{row['id']}.png"),
        )

    # 6. Report
    write_report(corr_df, rt_events, kharg_events, placebo_p90, rt_m, jodi)

    print("\n[validate] Done.")
    print(f"  Report: {REPORT_PATH}")
    print(f"  Plots:  {PLOT_DIR}/")


if __name__ == "__main__":
    main()
