"""
fetch_jodi.py — JODI-Oil bulk data downloader.

JODI (Joint Organisations Data Initiative) oil database:
  Bulk CSV: https://www.jodidata.org/_resources/files/downloads/oil-data/world-oil-data.zip
  Contains monthly production/exports/imports per country.

We extract:
  - Iran: crude exports (EXPO or TOTEXP, product CRUD or CRUDEOIL)
  - Saudi Arabia: crude exports as reference

Columns in JODI CSV (JODI-World format):
  REF_AREA, PRODUCT, FLOW, TIME_PERIOD, OBS_VALUE, UNIT, OBS_STATUS

Cache: client_package/data/jodi_world.csv (refresh if > 30 days old)
Output: client_package/data/jodi_iran_exports.csv
        client_package/data/jodi_saudi_exports.csv
"""

import os, time, io, zipfile, requests
import pandas as pd

JODI_URL  = "https://www.jodidata.org/_resources/files/downloads/oil-data/world-oil-data.zip"
CACHE_DIR = "client_package/data"

# JODI country codes
IRAN_CODES   = {"IR", "IRN", "IRAN"}
SAUDI_CODES  = {"SA", "SAU", "SAUDI ARABIA", "SAUDIARA"}

# Product and flow codes for crude exports
CRUDE_PRODUCTS = {"CRUDEOIL", "CRUD", "CRUDE", "C"}
EXPORT_FLOWS   = {"EXPO", "TOTEXP", "EXPORTS", "EXP"}


def _download_jodi() -> pd.DataFrame:
    """Download and parse JODI bulk CSV. Returns raw DataFrame."""
    cache_zip  = os.path.join(CACHE_DIR, "jodi_world.zip")
    cache_csv  = os.path.join(CACHE_DIR, "jodi_world.csv")

    if os.path.exists(cache_csv):
        age_days = (time.time() - os.path.getmtime(cache_csv)) / 86400
        if age_days < 30:
            print(f"  [JODI] Using cache ({age_days:.1f}d old)")
            return pd.read_csv(cache_csv, low_memory=False)

    print("  [JODI] Downloading bulk CSV...")
    try:
        r = requests.get(JODI_URL, timeout=120)
        r.raise_for_status()
    except Exception as e:
        print(f"  [JODI] Download failed: {e}")
        if os.path.exists(cache_csv):
            print("  [JODI] Using stale cache")
            return pd.read_csv(cache_csv, low_memory=False)
        return pd.DataFrame()

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        csv_names = [n for n in z.namelist() if n.endswith(".csv")]
        if not csv_names:
            print(f"  [JODI] No CSV in ZIP; contents: {z.namelist()}")
            return pd.DataFrame()
        print(f"  [JODI] Found in ZIP: {csv_names}")
        with z.open(csv_names[0]) as f:
            df = pd.read_csv(f, low_memory=False)

    df.to_csv(cache_csv, index=False)
    print(f"  [JODI] {len(df)} rows, saved to {cache_csv}")
    return df


def _normalise_col(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


def _extract_country_exports(df: pd.DataFrame, country_codes: set, label: str) -> pd.DataFrame:
    """Filter JODI df for crude exports of given country codes."""
    if df.empty:
        return pd.DataFrame()

    # Detect column names (JODI has changed its schema over versions)
    cols = {c.upper(): c for c in df.columns}
    area_col    = cols.get("REF_AREA") or cols.get("COUNTRY") or cols.get("AREA")
    product_col = cols.get("PRODUCT") or cols.get("ENERGY_PRODUCT")
    flow_col    = cols.get("FLOW") or cols.get("TRANSACTION")
    time_col    = cols.get("TIME_PERIOD") or cols.get("DATE") or cols.get("PERIOD")
    value_col   = cols.get("OBS_VALUE") or cols.get("VALUE") or cols.get("QUANTITY")

    if not all([area_col, product_col, flow_col, time_col, value_col]):
        print(f"  [JODI] Cannot identify columns; available: {list(df.columns[:10])}")
        return pd.DataFrame()

    mask = (
        _normalise_col(df[area_col]).isin(country_codes) &
        _normalise_col(df[product_col]).isin(CRUDE_PRODUCTS) &
        _normalise_col(df[flow_col]).isin(EXPORT_FLOWS)
    )
    sub = df[mask].copy()
    if sub.empty:
        print(f"  [JODI] No {label} crude export rows found")
        print(f"         Sample area codes: {_normalise_col(df[area_col]).unique()[:10]}")
        print(f"         Sample products:   {_normalise_col(df[product_col]).unique()[:10]}")
        print(f"         Sample flows:      {_normalise_col(df[flow_col]).unique()[:10]}")
        return pd.DataFrame()

    sub["date"] = pd.to_datetime(sub[time_col], errors="coerce")
    sub["value_kbd"] = pd.to_numeric(sub[value_col], errors="coerce")
    # JODI values are in thousand barrels/day or thousand tonnes/month — detect
    # JODI-World typically reports in kb/d. If unit col present, check.
    unit_col = cols.get("UNIT") or cols.get("UNIT_MEASURE")
    if unit_col:
        units = _normalise_col(sub[unit_col]).unique()
        print(f"  [JODI] {label} units: {units}")

    out = sub[["date", "value_kbd"]].dropna().sort_values("date")
    out.columns = ["date", "kbd"]
    print(f"  [JODI] {label}: {len(out)} monthly obs, {out['date'].min().date()} → {out['date'].max().date()}")
    return out


def fetch_jodi() -> tuple:
    """Returns (iran_df, saudi_df) each with columns: date, kbd."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    df = _download_jodi()

    iran_path  = os.path.join(CACHE_DIR, "jodi_iran_exports.csv")
    saudi_path = os.path.join(CACHE_DIR, "jodi_saudi_exports.csv")

    iran  = _extract_country_exports(df, IRAN_CODES,  "Iran")
    saudi = _extract_country_exports(df, SAUDI_CODES, "Saudi")

    if not iran.empty:
        iran.to_csv(iran_path, index=False)
    if not saudi.empty:
        saudi.to_csv(saudi_path, index=False)

    return iran, saudi
