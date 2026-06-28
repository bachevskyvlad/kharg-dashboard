"""
Correlation: OilRadar satellite signal vs JODI Iran crude oil exports.
Falls back to EIA Iran production if JODI is unavailable.
Run locally: python scripts/calibrate_eia.py
Requires: pip install requests pandas scipy matplotlib
"""
import json
import requests
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import pearsonr
from pathlib import Path

EIA_API_KEY = "ng4UlxTeT7EgyvAIPSsbA2QR94o6D01CqOwge6C6"
DATA_PATH = Path(__file__).parent.parent / "data" / "data_daily.json"

JODI_URL = "https://www.jodidb.org/api/1.0/bulk_download/archivefileposapi/"


def fetch_jodi_iran_exports():
    """Fetch Iran crude oil exports from JODI World database (monthly, kbd)."""
    params = {
        "country_name": "IRAN, ISLAMIC REP. OF",
        "product_name": "CRUDEOIL",
        "flow_breakdown": "EXPORTS",
        "unit_measure": "KBD",
        "frequency": "Monthly",
        "recent_vintage": "true",
    }
    print("Fetching JODI Iran crude exports...")
    r = requests.get(JODI_URL, params=params, timeout=30)
    print(f"JODI status: {r.status_code}")
    if r.status_code != 200:
        raise ValueError(f"JODI returned {r.status_code}: {r.text[:300]}")

    data = r.json()
    # JODI returns list of dicts with keys: country_name, product_name, flow_breakdown,
    # unit_measure, frequency, date (YYYY-MM), quantity
    if isinstance(data, dict) and "data" in data:
        rows = data["data"]
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError(f"Unexpected JODI response structure: {str(data)[:300]}")

    print(f"JODI rows: {len(rows)}")
    if not rows:
        raise ValueError("JODI returned empty data")

    df = pd.DataFrame(rows)
    print(f"JODI columns: {list(df.columns)}")
    print(df.head(3).to_string())

    # Normalise column names (JODI uses various casing)
    df.columns = [c.lower().strip() for c in df.columns]
    date_col = next((c for c in df.columns if "date" in c or "period" in c or "time" in c), None)
    val_col = next((c for c in df.columns if "quantity" in c or "value" in c), None)
    if not date_col or not val_col:
        raise ValueError(f"Cannot find date/value columns in JODI response: {list(df.columns)}")

    df = df[[date_col, val_col]].copy()
    df.columns = ["month", "exports_kbd"]
    df["exports_kbd"] = pd.to_numeric(df["exports_kbd"], errors="coerce")
    df["month"] = pd.to_datetime(df["month"])
    df = df.dropna().sort_values("month")
    print(f"JODI: {len(df)} months, {df['month'].min().date()} — {df['month'].max().date()}")
    return df


def fetch_eia_iran_production():
    """Fallback: EIA Iran crude production (activityId=1, productId=57)."""
    url = (
        "https://api.eia.gov/v2/international/data/"
        f"?api_key={EIA_API_KEY}"
        "&data[]=value"
        "&facets[activityId][]=1"
        "&facets[productId][]=57"
        "&facets[countryRegionId][]=IRN"
        "&facets[unit][]=TBPD"
        "&sort[0][column]=period&sort[0][direction]=desc&length=36"
    )
    print("Fetching EIA Iran crude production (fallback)...")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    rows = r.json()["response"]["data"]
    print(f"EIA rows: {len(rows)}")
    if not rows:
        raise ValueError("EIA returned empty data")
    df = pd.DataFrame(rows)
    df = df[["period", "value"]].copy()
    df.columns = ["month", "exports_kbd"]
    df["exports_kbd"] = pd.to_numeric(df["exports_kbd"], errors="coerce")
    df["month"] = pd.to_datetime(df["month"])
    df = df.dropna().sort_values("month")
    print(f"EIA production: {len(df)} months, {df['month'].min().date()} — {df['month'].max().date()}")
    return df, "EIA Iran Crude Production (kbd)"


def load_satellite_data():
    with open(DATA_PATH) as f:
        d = json.load(f)
    df = pd.DataFrame({"date": pd.to_datetime(d["dates"]), "vol_mb": d["values"]})
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    monthly = df.groupby("month")["vol_mb"].mean().reset_index()
    monthly.columns = ["month", "avg_vol_mb"]
    return monthly


def correlate_and_plot(merged, ext_label):
    clean = merged.dropna(subset=["avg_vol_mb", "exports_kbd"])
    print(f"Clean rows for correlation: {len(clean)}")
    if len(clean) < 4:
        print("Not enough clean data for correlation.")
        return
    m = clean.copy()

    r0, p0 = pearsonr(m["avg_vol_mb"], m["exports_kbd"])
    print(f"\nCorrelation (same month):         r={r0:.3f}, p={p0:.3f}")

    if len(m) > 4:
        l1 = m.copy()
        l1["ext_next"] = l1["exports_kbd"].shift(-1)
        l1 = l1.dropna()
        r1, p1 = pearsonr(l1["avg_vol_mb"], l1["ext_next"])
        print(f"Correlation (SAR leads 1 month):  r={r1:.3f}, p={p1:.3f}")

    if len(m) > 5:
        l2 = m.copy()
        l2["ext_next2"] = l2["exports_kbd"].shift(-2)
        l2 = l2.dropna()
        r2, p2 = pearsonr(l2["avg_vol_mb"], l2["ext_next2"])
        print(f"Correlation (SAR leads 2 months): r={r2:.3f}, p={p2:.3f}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.suptitle(f"OilRadar SAR Signal vs {ext_label}", fontsize=13)

    ax1.plot(m["month"], m["avg_vol_mb"], color="#3b82f6", marker="o", ms=4, label="SAR Storage (Mb)")
    ax1.set_ylabel("Storage (Mb)")
    ax1.legend()
    ax1.grid(alpha=0.3)

    ax2.plot(m["month"], m["exports_kbd"], color="#f0a500", marker="o", ms=4, label=ext_label)
    ax2.set_ylabel("kbd")
    ax2.legend()
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    out = Path(__file__).parent.parent / "public" / "eia_correlation.png"
    out.parent.mkdir(exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="#080a0d")
    print(f"\nChart saved: {out}")


def main():
    # Try JODI first, fall back to EIA production
    ext_label = "JODI Iran Crude Exports (kbd)"
    try:
        ext_df = fetch_jodi_iran_exports()
        ext_df = ext_df.rename(columns={"exports_kbd": "exports_kbd"})
    except Exception as e:
        print(f"JODI failed: {e}")
        print("Falling back to EIA Iran production...")
        ext_df, ext_label = fetch_eia_iran_production()

    print("\nLoading satellite data...")
    sat = load_satellite_data()
    print(f"SAR: {len(sat)} months, {sat['month'].min().date()} — {sat['month'].max().date()}")

    merged = pd.merge(sat, ext_df, on="month", how="inner").sort_values("month")
    print(f"\nOverlapping months: {len(merged)}")
    print(merged[["month", "avg_vol_mb", "exports_kbd"]].to_string())

    if len(merged) < 4:
        print("Not enough overlapping data for correlation.")
        return

    correlate_and_plot(merged, ext_label)


if __name__ == "__main__":
    main()
