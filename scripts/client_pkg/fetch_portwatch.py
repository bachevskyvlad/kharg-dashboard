"""
fetch_portwatch.py — PortWatch (IMF AIS) port-call data fetcher.

PortWatch REST API: ArcGIS FeatureServer served by IMF.
Port IDs (PortWatch internal):
  Ras Tanura : port1091
  Juaymah    : port526
  Yanbu      : discovered via portname LIKE '%YANBU%' query (log candidates)

Fields returned per call:
  portid, date (YYYYMMDD or epoch ms), vessel_type, n_calls, dwt_in_mt
  → export_tanker = rows where vessel_type == 'Tanker' (or 'Oil Tanker')

Monthly aggregation:
  mbpd = (sum_dwt_mt × LOAD_FACTOR × BBL_PER_MT) / days_in_month / 1_000_000
  LOAD_FACTOR = 0.85 (industry average capacity utilisation)
  BBL_PER_MT  = 7.33

Cache: client_package/data/portwatch_{portid}.csv (refresh if > 7 days old)
"""

import os, json, time, datetime, requests
import pandas as pd

BASE_URL = "https://portwatch.imf.org/arcgis/rest/services/PortWatch/PortWatch_portcalls/FeatureServer/0/query"
YANBU_PORTS_URL = "https://portwatch.imf.org/arcgis/rest/services/PortWatch/PortWatch_ports_database/FeatureServer/0/query"

BBL_PER_MT   = 7.33
LOAD_FACTOR  = 0.85
CACHE_DIR    = "client_package/data"
MAX_RECORDS  = 2000   # FeatureServer page size cap


def _get(url, params, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def discover_yanbu_ports():
    """Query PortWatch ports DB for Yanbu candidates; print and return."""
    params = {
        "where": "portname LIKE '%YANBU%' OR portname LIKE '%YANBU%'",
        "outFields": "portid,portname,country,porttype",
        "f": "json",
        "resultRecordCount": 50,
    }
    try:
        data = _get(YANBU_PORTS_URL, params)
        features = data.get("features", [])
        rows = [f["attributes"] for f in features]
        print(f"  Yanbu port candidates ({len(rows)}):")
        for r in rows:
            print(f"    {r.get('portid')} | {r.get('portname')} | {r.get('porttype')}")
        return rows
    except Exception as e:
        print(f"  [WARN] Yanbu discovery failed: {e}")
        return []


def fetch_portcalls(portid: str, start_date: str = "2019-01-01") -> pd.DataFrame:
    """
    Fetch all portcalls for portid since start_date.
    Returns DataFrame with columns: date, vessel_type, n_calls, dwt_mt.
    Caches to CSV; refreshes if cache > 7 days old.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"portwatch_{portid}.csv")

    if os.path.exists(cache_path):
        age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
        if age_days < 7:
            print(f"  [{portid}] Using cache ({age_days:.1f}d old)")
            return pd.read_csv(cache_path, parse_dates=["date"])

    print(f"  [{portid}] Fetching from PortWatch API...")
    all_rows = []
    offset = 0
    while True:
        params = {
            "where": f"portid='{portid}' AND date>=DATE '{start_date}'",
            "outFields": "portid,date,vessel_type,n_calls,dwt_in_mt",
            "orderByFields": "date ASC",
            "resultOffset": offset,
            "resultRecordCount": MAX_RECORDS,
            "f": "json",
        }
        try:
            data = _get(BASE_URL, params)
        except Exception as e:
            print(f"  [WARN] PortWatch fetch failed for {portid} at offset {offset}: {e}")
            break

        features = data.get("features", [])
        if not features:
            break
        for f in features:
            a = f["attributes"]
            # date may be epoch ms
            d = a.get("date")
            if isinstance(d, (int, float)):
                d = pd.Timestamp(d, unit="ms")
            else:
                d = pd.Timestamp(d)
            all_rows.append({
                "date": d,
                "vessel_type": a.get("vessel_type", ""),
                "n_calls": a.get("n_calls", 0) or 0,
                "dwt_mt": a.get("dwt_in_mt", 0) or 0,
            })
        if len(features) < MAX_RECORDS:
            break
        offset += MAX_RECORDS

    if not all_rows:
        print(f"  [WARN] No data for {portid}")
        return pd.DataFrame(columns=["date", "vessel_type", "n_calls", "dwt_mt"])

    df = pd.DataFrame(all_rows)
    df.to_csv(cache_path, index=False)
    print(f"  [{portid}] {len(df)} rows, saved to {cache_path}")
    return df


def monthly_export_mbpd(df: pd.DataFrame, tanker_types=None) -> pd.DataFrame:
    """
    Aggregate portcalls to monthly export estimate in mbpd.
    tanker_types: set of vessel_type strings considered tankers.
    Returns DataFrame: month (period), n_calls, dwt_mt, mbpd.
    """
    if tanker_types is None:
        tanker_types = {"Tanker", "Oil tanker", "Oil Tanker", "Crude Oil Tanker", "TANKER"}

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    # Filter tankers
    mask = df["vessel_type"].str.strip().isin(tanker_types)
    if mask.sum() == 0:
        print(f"  [WARN] No tanker rows found; vessel_types present: {df['vessel_type'].unique()[:10]}")
        # Fall back: use all rows
        mask = pd.Series(True, index=df.index)

    df = df[mask].copy()
    df["month"] = df["date"].dt.to_period("M")
    days_map = df["month"].apply(lambda p: p.days_in_month)
    df["days"] = days_map

    monthly = df.groupby("month").agg(
        n_calls=("n_calls", "sum"),
        dwt_mt=("dwt_mt", "sum"),
        days=("days", "first"),
    ).reset_index()
    monthly["mbpd"] = (monthly["dwt_mt"] * LOAD_FACTOR * BBL_PER_MT) / monthly["days"] / 1_000_000
    monthly["month_start"] = monthly["month"].dt.to_timestamp()
    return monthly
