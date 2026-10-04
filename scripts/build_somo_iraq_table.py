"""
build_somo_iraq_table.py — Этап 4 (measurement-ТЗ)
===================================================
Build SOMO monthly Iraq crude export table 2017→2026.
Each row: year, month, total_kbd, basra_south_kbd, url_source.

Data sources (public, no auth):
  1. EIA monthly Iraq crude exports: https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=MCREXIQ2&f=M
     (but EIA CSV structure changes — use direct table download)
  2. JODI-Oil: https://www.jodidata.org/_resources/files/downloads/oil-data/jodi-oil-world-database_beta.csv
     (Kolya-side per ТЗ — do not open here)
  3. SOMO / Iraqi MoO press releases archived at oilprice.com, arabianoilgas.com, and direct SOMO PDFs
  4. IEA Oil Market Report monthly supply tables (subscription — skip)
  5. Kpler / Vortexa aggregate data published in Reuters/Bloomberg articles — with URL

Rules (ТЗ):
  - Export anchors NOT opened (JODI, PortWatch)
  - Each row must have a URL source
  - Basra/south exports separate from total where available
  - "вероятно" prohibited — if value uncertain, mark estimate_flag=1

Since live scraping is blocked by the container proxy for most data sources,
this script outputs a TEMPLATE CSV with known-public rows filled from
EIA open data API (no auth required) plus placeholder rows for source URLs
the analyst will verify. EIA endpoint is accessible via the proxy.
"""

import os, sys, json, csv
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# EIA open data — Iraq crude oil exports (thousand barrels per day)
# Series ID: MCREXIQ2 = Iraq total crude exports
EIA_URL = "https://api.eia.gov/v2/petroleum/move/exp/data/?frequency=monthly&data[0]=value&facets[product][]=EPC0&facets[area][]=IZ&start=2017-01&sort[0][column]=period&sort[0][direction]=asc&offset=0&length=500"
# No API key needed for v2 API basic queries (returns up to 500 rows free)


def fetch_eia_iraq_exports():
    """Fetch Iraq crude export series from EIA open API. Returns list of {year,month,kbd,url}."""
    try:
        r = requests.get(EIA_URL, timeout=30)
        if r.status_code != 200:
            print(f"  EIA API: HTTP {r.status_code}")
            return []
        d = r.json()
        rows_raw = d.get("response", {}).get("data", [])
        results = []
        for row in rows_raw:
            period = row.get("period", "")  # "2017-01"
            if not period or len(period) < 7:
                continue
            year, month = int(period[:4]), int(period[5:7])
            val = row.get("value")
            if val is None:
                continue
            kbd = float(val)
            url = f"https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=MCREXIQ2&f=M"
            results.append({"year": year, "month": month,
                            "total_kbd": round(kbd, 0),
                            "source": "EIA", "url": url,
                            "estimate_flag": 0})
        print(f"  EIA: {len(results)} monthly rows")
        return results
    except Exception as e:
        print(f"  EIA fetch failed: {e}")
        return []


def main():
    print("=== SOMO Iraq monthly export table 2017→2026 ===")

    # 1. EIA data
    print("\n[1] EIA open API…")
    eia_rows = fetch_eia_iraq_exports()

    # 2. Build full month grid 2017-01 → 2026-09
    all_months = []
    for yr in range(2017, 2027):
        for mo in range(1, 13):
            if yr == 2026 and mo > 9:
                break
            all_months.append((yr, mo))

    # Index EIA rows
    eia_idx = {(r["year"], r["month"]): r for r in eia_rows}

    # 3. Assemble table
    table = []
    for yr, mo in all_months:
        period = f"{yr}-{mo:02d}"
        eia = eia_idx.get((yr, mo))

        row = {
            "period":          period,
            "year":            yr,
            "month":           mo,
            "total_kbd":       eia["total_kbd"] if eia else "",
            "basra_south_kbd": "",           # Basra/south — not in EIA; placeholder
            "source":          eia["source"] if eia else "missing",
            "url":             eia["url"] if eia else "",
            "estimate_flag":   eia["estimate_flag"] if eia else 1,
            "notes":           "" if eia else "no_data",
        }
        table.append(row)

    # 4. Write CSV
    csv_path = OUT_DIR / "somo_iraq_monthly_exports.csv"
    fieldnames = ["period","year","month","total_kbd","basra_south_kbd",
                  "source","url","estimate_flag","notes"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(table)

    # 5. Summary
    n_filled = sum(1 for r in table if r["total_kbd"] != "")
    n_total  = len(table)
    print(f"\n  {n_filled}/{n_total} months have total_kbd")
    print(f"  basra_south_kbd: all empty (requires SOMO PDF scraping — analyst side per ТЗ)")
    print(f"  CSV → {csv_path}")

    # Print first and last 3 rows
    print("\n  period     total_kbd  basra  source  estimate_flag")
    for r in table[:3] + table[-3:]:
        print(f"  {r['period']}  {str(r['total_kbd']):<9}  {str(r['basra_south_kbd']):<5}  "
              f"{r['source']:<6}  {r['estimate_flag']}")

    print("\nDone.")


if __name__ == "__main__":
    main()
