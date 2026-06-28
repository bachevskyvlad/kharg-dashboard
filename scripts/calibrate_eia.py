"""
Correlation: OilRadar satellite signal vs EIA Iran crude oil exports.
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


def fetch_eia_iran_exports():
    # Step 1: discover what's available for Iran
    irn_url = (
        "https://api.eia.gov/v2/international/data/"
        f"?api_key={EIA_API_KEY}"
        "&facets[countryRegionId][]=IRN"
        "&facets[unit][]=TBPD"
        "&sort[0][column]=period&sort[0][direction]=desc&length=20"
    )
    ir = requests.get(irn_url, timeout=30)
    irn_data = ir.json()["response"]["data"]
    print(f"Iran TBPD rows available: {len(irn_data)}")
    for row in irn_data[:5]:
        print(f"  activityId={row.get('activityId')} ({row.get('activityName')}) productId={row.get('productId')} ({row.get('productName')})")

    # EIA does not publish Iran exports via API (sanctions).
    # Use crude oil production as proxy (activityId=1, productId=57).
    exports_id = "1"
    crude_id = "57"
    print(f"Iran exports not in EIA API. Using Production (activityId=1) crude oil (productId=57) as proxy.")

    url = (
        "https://api.eia.gov/v2/international/data/"
        f"?api_key={EIA_API_KEY}"
        f"&facets[activityId][]={exports_id}"
        f"&facets[productId][]={crude_id}"
        "&facets[countryRegionId][]=IRN"
        "&facets[unit][]=TBPD"
        "&sort[0][column]=period&sort[0][direction]=desc&length=36"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    raw = r.json()
    rows = raw["response"]["data"]
    print(f"EIA rows fetched: {len(rows)}")
    if not rows:
        raise ValueError("EIA returned empty data after discovery")
    df = pd.DataFrame(rows)
    print(f"EIA columns: {list(df.columns)}")
    print(df.head(2).to_string())
    period_col = "period" if "period" in df.columns else df.columns[0]
    value_col = "value" if "value" in df.columns else df.columns[1]
    df = df[[period_col, value_col]].copy()
    df.columns = ["month", "eia_production_kbd"]
    df["eia_production_kbd"] = pd.to_numeric(df["eia_production_kbd"], errors="coerce")
    df["month"] = pd.to_datetime(df["month"])
    return df.dropna().sort_values("month")


def load_satellite_data():
    with open(DATA_PATH) as f:
        d = json.load(f)
    df = pd.DataFrame({"date": pd.to_datetime(d["dates"]), "vol_mb": d["values"]})
    # Monthly average storage volume
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    monthly = df.groupby("month")["vol_mb"].mean().reset_index()
    monthly.columns = ["month", "avg_vol_mb"]
    return monthly


def main():
    print("Fetching EIA Iran export data...")
    eia = fetch_eia_iran_exports()
    print(f"EIA: {len(eia)} months, {eia['month'].min().date()} — {eia['month'].max().date()}")

    print("Loading satellite data...")
    sat = load_satellite_data()
    print(f"SAR: {len(sat)} months, {sat['month'].min().date()} — {sat['month'].max().date()}")

    # Merge on month
    merged = pd.merge(sat, eia, on="month", how="inner")
    merged = merged.sort_values("month")
    print(f"\nOverlapping months: {len(merged)}")

    if len(merged) < 4:
        print("Not enough overlapping data for correlation.")
        return

    print("\nMerged data:")
    print(merged[["month", "avg_vol_mb", "eia_production_kbd"]].to_string())
    print(f"\nNaN in avg_vol_mb: {merged['avg_vol_mb'].isna().sum()}")
    print(f"NaN in eia_production_kbd: {merged['eia_production_kbd'].isna().sum()}")
    print(f"Unique EIA values: {merged['eia_production_kbd'].nunique()}")

    # Drop NaN before correlation
    clean = merged.dropna(subset=["avg_vol_mb", "eia_production_kbd"])
    print(f"Clean rows for correlation: {len(clean)}")
    if len(clean) < 4:
        print("Not enough clean data for correlation.")
        return
    merged = clean

    # Correlation: SAR volume vs EIA production (same month)
    r0, p0 = pearsonr(merged["avg_vol_mb"], merged["eia_production_kbd"])
    print(f"\nCorrelation (same month):        r={r0:.3f}, p={p0:.3f}")

    # Correlation: SAR volume (t) vs EIA exports (t+1) — SAR leads by 1 month
    if len(merged) > 4:
        lead1 = merged.copy()
        lead1["eia_next"] = lead1["eia_production_kbd"].shift(-1)
        lead1 = lead1.dropna()
        r1, p1 = pearsonr(lead1["avg_vol_mb"], lead1["eia_next"])
        print(f"Correlation (SAR leads 1 month): r={r1:.3f}, p={p1:.3f}")

    if len(merged) > 5:
        lead2 = merged.copy()
        lead2["eia_next2"] = lead2["eia_production_kbd"].shift(-2)
        lead2 = lead2.dropna()
        r2, p2 = pearsonr(lead2["avg_vol_mb"], lead2["eia_next2"])
        print(f"Correlation (SAR leads 2 months): r={r2:.3f}, p={p2:.3f}")

    # Plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.suptitle("OilRadar SAR Signal vs EIA Iran Crude Production", fontsize=14)

    ax1.plot(merged["month"], merged["avg_vol_mb"], color="#3b82f6", marker="o", ms=4, label="SAR Storage (Mb, monthly avg)")
    ax1.set_ylabel("Storage (Mb)")
    ax1.legend()
    ax1.grid(alpha=0.3)

    ax2.plot(merged["month"], merged["eia_production_kbd"], color="#f0a500", marker="o", ms=4, label="EIA Iran Production (kbd)")
    ax2.set_ylabel("Production (kbd)")
    ax2.legend()
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    out = Path(__file__).parent.parent / "public" / "eia_correlation.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="#080a0d")
    print(f"\nChart saved: {out}")
    plt.show()


if __name__ == "__main__":
    main()
