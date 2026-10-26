"""
fetch_portwatch_daily.py — Raw daily PortWatch data for P1 of ТЗ 2026-10-26 v3.

Fetches ALL fields from PortWatch ArcGIS FeatureServer for:
  port1091  Ras Tanura   (SA)
  port526   Juaymah      (SA)
  Yanbu     → discovered via portname query
  Kharg/Khark → searched by name (Iranian ports)

Outputs:
  diagnostics/portwatch/portwatch_daily.csv
  diagnostics/portwatch/portwatch_ports_meta.csv   (port registry dump)
  diagnostics/portwatch/README.txt

Column spec (portwatch_daily.csv):
  portid, portname, country, date, + ALL remaining fields returned by API
  (no filtering, no aggregation, raw as received)

Usage: python scripts/research/fetch_portwatch_daily.py
"""

import os
import sys
import json
import time
import datetime
import requests
import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
BASE_PORTCALLS = (
    "https://portwatch.imf.org/arcgis/rest/services/PortWatch"
    "/PortWatch_portcalls/FeatureServer/0/query"
)
BASE_PORTS_DB = (
    "https://portwatch.imf.org/arcgis/rest/services/PortWatch"
    "/PortWatch_ports_database/FeatureServer/0/query"
)
# Daily trade-flow endpoint (may or may not exist; tried opportunistically)
BASE_TRADE = (
    "https://portwatch.imf.org/arcgis/rest/services/PortWatch"
    "/PortWatch_daily_trade/FeatureServer/0/query"
)
# v2 / alternate naming attempts
ALTERNATE_ENDPOINTS = [
    "https://portwatch.imf.org/arcgis/rest/services/PortWatch/PortWatch_port_stats/FeatureServer/0/query",
    "https://portwatch.imf.org/arcgis/rest/services/PortWatch/PortWatch_Daily_Port_Stats/FeatureServer/0/query",
    "https://portwatch.imf.org/arcgis/rest/services/PortWatch/PortWatch_trade_daily/FeatureServer/0/query",
]

MAX_RECORDS = 2000
START_DATE  = "2019-01-01"

OUT_DIR = Path("diagnostics/portwatch")

# Known fixed port IDs
FIXED_PORTS = [
    ("port1091", "Ras Tanura",  "SA"),
    ("port526",  "Juaymah",    "SA"),
]

SEARCH_NAMES = [
    "YANBU",
    "KHARG",
    "KHARK",
    "KHARK ISLAND",
    "LAVAN",
    "BANDAR IMAM",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _get(url, params, retries=4):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=45)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def probe_endpoint(url):
    """Return True if endpoint responds with expected ArcGIS JSON."""
    try:
        r = requests.get(url, params={"where": "1=1", "resultRecordCount": "1", "f": "json"}, timeout=15)
        j = r.json()
        return "features" in j or "fields" in j
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Port discovery
# ---------------------------------------------------------------------------
def discover_ports(search_names):
    """Search PortWatch ports DB for matching portnames. Returns list of dicts."""
    found = []
    seen_ids = set()
    for name in search_names:
        params = {
            "where": f"portname LIKE '%{name}%'",
            "outFields": "*",
            "f": "json",
            "resultRecordCount": 50,
        }
        try:
            data = _get(BASE_PORTS_DB, params)
            for feat in data.get("features", []):
                a = feat.get("attributes", {})
                pid = a.get("portid") or a.get("PORT_ID") or a.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    found.append(a)
                    print(f"  FOUND: {pid} | {a.get('portname','?')} | {a.get('country','?')}")
        except Exception as e:
            print(f"  [WARN] name search '{name}' failed: {e}")
    return found


# ---------------------------------------------------------------------------
# Field introspection
# ---------------------------------------------------------------------------
def get_fields(url):
    try:
        data = _get(url, {"f": "json"})
        fields = data.get("fields", [])
        return [f["name"] for f in fields]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Fetch all rows for a port from an endpoint
# ---------------------------------------------------------------------------
def fetch_all(url, portid, out_fields="*"):
    all_rows = []
    offset = 0
    while True:
        params = {
            "where":              f"portid='{portid}'",
            "outFields":          out_fields,
            "orderByFields":      "date ASC",
            "resultOffset":       offset,
            "resultRecordCount":  MAX_RECORDS,
            "f":                  "json",
        }
        try:
            data = _get(url, params)
        except Exception as e:
            print(f"    [WARN] fetch failed at offset {offset}: {e}")
            break
        features = data.get("features", [])
        if not features:
            break
        for feat in features:
            a = feat.get("attributes", {})
            # normalise epoch-ms dates to ISO
            for k, v in list(a.items()):
                if "date" in k.lower() and isinstance(v, (int, float)) and v > 1e9:
                    a[k] = pd.Timestamp(v, unit="ms").strftime("%Y-%m-%d")
            all_rows.append(a)
        if len(features) < MAX_RECORDS:
            break
        offset += MAX_RECORDS
    return all_rows


def fetch_all_with_datefilter(url, portid, start_date=START_DATE, out_fields="*"):
    """Same as fetch_all but with date filter — tries epoch-ms and string DATE."""
    all_rows = []
    offset = 0
    # try epoch first
    epoch_ms = int(pd.Timestamp(start_date).timestamp() * 1000)
    date_clause_variants = [
        f"portid='{portid}' AND date >= {epoch_ms}",
        f"portid='{portid}' AND date >= DATE '{start_date}'",
        f"portid='{portid}'",
    ]
    working_clause = None
    for clause in date_clause_variants:
        params = {"where": clause, "outFields": out_fields, "resultRecordCount": 1, "f": "json"}
        try:
            data = _get(url, params)
            if "error" not in data:
                working_clause = clause
                break
        except Exception:
            continue
    if working_clause is None:
        print(f"    [WARN] no working date clause for {portid}")
        return []

    while True:
        params = {
            "where":             working_clause,
            "outFields":         out_fields,
            "orderByFields":     "date ASC",
            "resultOffset":      offset,
            "resultRecordCount": MAX_RECORDS,
            "f":                 "json",
        }
        try:
            data = _get(url, params)
        except Exception as e:
            print(f"    [WARN] fetch failed at offset {offset}: {e}")
            break
        features = data.get("features", [])
        if not features:
            break
        for feat in features:
            a = feat.get("attributes", {})
            for k, v in list(a.items()):
                if "date" in k.lower() and isinstance(v, (int, float)) and v > 1e9:
                    a[k] = pd.Timestamp(v, unit="ms").strftime("%Y-%m-%d")
            all_rows.append(a)
        if len(features) < MAX_RECORDS:
            break
        offset += MAX_RECORDS
    return all_rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    log_lines = [f"Run: {run_ts}", f"Script: scripts/research/fetch_portwatch_daily.py", ""]

    # ---- discover ports ----
    print("=== Discovering ports ===")
    discovered = discover_ports(SEARCH_NAMES)
    disc_df = pd.DataFrame(discovered) if discovered else pd.DataFrame()
    if not disc_df.empty:
        disc_df.to_csv(OUT_DIR / "portwatch_ports_meta.csv", index=False)
        print(f"  Saved {len(disc_df)} discovered ports → portwatch_ports_meta.csv")

    # Build final port list: fixed + discovered (deduplicated)
    ports = list(FIXED_PORTS)
    fixed_ids = {p[0] for p in ports}
    for _, row in disc_df.iterrows():
        pid = str(row.get("portid", row.get("PORT_ID", "")))
        pname = str(row.get("portname", "?"))
        country = str(row.get("country", "?"))
        if pid and pid not in fixed_ids:
            ports.append((pid, pname, country))
            fixed_ids.add(pid)

    print(f"\nTotal ports to fetch: {len(ports)}")
    for p in ports:
        print(f"  {p[0]} | {p[1]} | {p[2]}")
    log_lines.append(f"Ports fetched ({len(ports)}):")
    for p in ports:
        log_lines.append(f"  {p[0]} | {p[1]} | {p[2]}")
    log_lines.append("")

    # ---- probe endpoints ----
    print("\n=== Probing endpoints ===")
    endpoints_to_try = [BASE_PORTCALLS, BASE_TRADE] + ALTERNATE_ENDPOINTS
    live_endpoints = []
    for ep in endpoints_to_try:
        ok = probe_endpoint(ep)
        status = "OK" if ok else "NO"
        print(f"  {status} {ep}")
        if ok:
            live_endpoints.append(ep)
    log_lines.append(f"Live endpoints: {live_endpoints}")
    log_lines.append("")

    if not live_endpoints:
        print("ERROR: no PortWatch endpoints reachable")
        log_lines.append("ERROR: no endpoints reachable")
        (OUT_DIR / "README.txt").write_text("\n".join(log_lines))
        sys.exit(1)

    # ---- fetch data ----
    print("\n=== Fetching data ===")
    all_dfs = []
    port_stats = {}

    for endpoint in live_endpoints:
        fields = get_fields(endpoint)
        print(f"\n  Endpoint: {endpoint}")
        print(f"  Fields: {fields}")
        log_lines.append(f"Endpoint {endpoint}: fields={fields}")

        for portid, portname, country in ports:
            print(f"    [{portid}] {portname} ...", end=" ", flush=True)
            rows = fetch_all_with_datefilter(endpoint, portid)
            if not rows:
                print("0 rows")
                if portid not in port_stats:
                    port_stats[portid] = {"portname": portname, "country": country, "n_rows": 0, "note": "no data from any endpoint"}
                continue
            df = pd.DataFrame(rows)
            df.insert(0, "portname", portname)
            df.insert(0, "portid", portid)
            df.insert(1, "country", country)
            df.insert(2, "_endpoint", endpoint.split("/")[-4])
            all_dfs.append(df)
            n = len(df)
            dates = sorted(df["date"].dropna().astype(str).unique()) if "date" in df.columns else []
            period = f"{dates[0]}–{dates[-1]}" if dates else "?"
            print(f"{n} rows, {period}")
            port_stats[portid] = {"portname": portname, "country": country, "n_rows": n, "period": period, "note": "ok"}

    if not all_dfs:
        print("ERROR: no data fetched from any port/endpoint")
        log_lines.append("ERROR: no data at all")
        (OUT_DIR / "README.txt").write_text("\n".join(log_lines))
        sys.exit(1)

    combined = pd.concat(all_dfs, ignore_index=True, sort=False)

    # sort by portid, date
    if "date" in combined.columns:
        combined = combined.sort_values(["portid", "date"], na_position="last")

    out_path = OUT_DIR / "portwatch_daily.csv"
    combined.to_csv(out_path, index=False)
    print(f"\nSaved {len(combined)} rows → {out_path}")

    # ---- README ----
    nan_cols = combined.isnull().sum()
    nan_str = "; ".join(f"{c}:{nan_cols[c]}" for c in nan_cols.index if nan_cols[c] > 0)
    log_lines += [
        "",
        "=== portwatch_daily.csv ===",
        f"Rows: {len(combined)}",
        f"Columns: {list(combined.columns)}",
        f"NaN per column: {nan_str or 'none'}",
        "",
        "=== Per-port summary ===",
    ]
    for pid, s in port_stats.items():
        log_lines.append(f"  {pid} | {s['portname']} | {s['country']} | {s.get('n_rows',0)} rows | {s.get('period','?')} | {s.get('note','')}")

    log_lines += [
        "",
        "Missing ports (not in PortWatch DB):",
    ]
    found_names_upper = {p[1].upper() for p in ports}
    for name in SEARCH_NAMES:
        if name not in found_names_upper and not any(name in p[1].upper() for p in ports):
            log_lines.append(f"  {name}: not found in PortWatch ports_database query")

    (OUT_DIR / "README.txt").write_text("\n".join(log_lines))
    print(f"README written → {OUT_DIR}/README.txt")


if __name__ == "__main__":
    main()
