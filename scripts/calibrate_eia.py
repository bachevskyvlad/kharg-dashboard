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
    # First discover valid activityId values
    facet_url = f"https://api.eia.gov/v2/international/data/facet/activityId/?api_key={EIA_API_KEY}"
    fr = requests.get(facet_url, timeout=30)
    raw_facet = fr.json()
    print(f"Facet raw keys: {list(raw_facet.get('response', {}).keys())}")
    print(f"Facet raw: {json.dumps(raw_facet)[:500]}")
    activities = {r["id"]: r["description"] for r in raw_facet.get("response", {}).get("facets", [])}
    print(f"Available activityIds: {activities}")

    # Find exports activityId
    exports_id = next((k for k, v in activities.items() if "export" in v.lower()), "4")
    print(f"Using activityId={exports_id} for exports")

    # Discover valid productId values for crude
    pfacet_url = f"https://api.eia.gov/v2/international/data/facet/productId/?api_key={EIA_API_KEY}"
    pr = requests.get(pfacet_url, timeout=30)
    products = {r["id"]: r["description"] for r in pr.json().get("response", {}).get("facets", [])}
    crude_id = next((k for k, v in products.items() if "crude" in v.lower()), "57")
    print(f"Available productIds (crude): {[(k,v) for k,v in products.items() if 'crude' in v.lower()]}")
    print(f"Using productId={crude_id} for crude oil")

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
    print(f"EIA response total: {raw['response'].get('total', 0)}")
    rows = raw["response"]["data"]
    print(f"EIA rows count: {len(rows)}")
    if rows:
        print(f"First row: {rows[0]}")
    if not rows:
        # Try without Iran filter to check if endpoint works at all
        test_url = (
            "https://api.eia.gov/v2/international/data/"
            f"?api_key={EIA_API_KEY}"
            f"&facets[activityId][]={exports_id}"
            f"&facets[productId][]={crude_id}"
            "&facets[unit][]=TBPD"
            "&sort[0][column]=period&sort[0][direction]=desc&length=5"
        )
        tr = requests.get(test_url, timeout=30)
        test_raw = tr.json()
        print(f"Test (no country filter) total: {test_raw['response'].get('total', 0)}")
        if test_raw["response"]["data"]:
            print(f"Test first row: {test_raw['response']['data'][0]}")
        raise ValueError("EIA returned empty data for Iran — see test query above")
    df = pd.DataFrame(rows)
    print(f"EIA columns: {list(df.columns)}")
    print(df.head(2).to_string())
    period_col = "period" if "period" in df.columns else df.columns[0]
    value_col = "value" if "value" in df.columns else df.columns[1]
    df = df[[period_col, value_col]].copy()
    df.columns = ["month", "eia_exports_kbd"]
    df["eia_exports_kbd"] = pd.to_numeric(df["eia_exports_kbd"], errors="coerce")
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

    # Correlation: SAR volume vs EIA exports (same month)
    r0, p0 = pearsonr(merged["avg_vol_mb"], merged["eia_exports_kbd"])
    print(f"\nCorrelation (same month):        r={r0:.3f}, p={p0:.3f}")

    # Correlation: SAR volume (t) vs EIA exports (t+1) — SAR leads by 1 month
    if len(merged) > 4:
        lead1 = merged.copy()
        lead1["eia_next"] = lead1["eia_exports_kbd"].shift(-1)
        lead1 = lead1.dropna()
        r1, p1 = pearsonr(lead1["avg_vol_mb"], lead1["eia_next"])
        print(f"Correlation (SAR leads 1 month): r={r1:.3f}, p={p1:.3f}")

    if len(merged) > 5:
        lead2 = merged.copy()
        lead2["eia_next2"] = lead2["eia_exports_kbd"].shift(-2)
        lead2 = lead2.dropna()
        r2, p2 = pearsonr(lead2["avg_vol_mb"], lead2["eia_next2"])
        print(f"Correlation (SAR leads 2 months): r={r2:.3f}, p={p2:.3f}")

    # Plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.suptitle("OilRadar SAR Signal vs EIA Iran Crude Exports", fontsize=14)

    ax1.plot(merged["month"], merged["avg_vol_mb"], color="#3b82f6", marker="o", ms=4, label="SAR Storage (Mb, monthly avg)")
    ax1.set_ylabel("Storage (Mb)")
    ax1.legend()
    ax1.grid(alpha=0.3)

    ax2.plot(merged["month"], merged["eia_exports_kbd"], color="#f0a500", marker="o", ms=4, label="EIA Iran Exports (kbd)")
    ax2.set_ylabel("Exports (kbd)")
    ax2.legend()
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    out = Path(__file__).parent.parent / "public" / "eia_correlation.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="#080a0d")
    print(f"\nChart saved: {out}")
    plt.show()


if __name__ == "__main__":
    main()
