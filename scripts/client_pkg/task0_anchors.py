"""
Task 0 — External anchors.

1. PortWatch: fetch monthly port-call / export estimates for RT, JU, YB.
2. Storage series: Kharg volume_timeseries + terminal_volume_timeseries for RT/YB.
3. Events table: hardcoded 2026 chronology with sources.

Outputs:
  client_package/data/portwatch_rt_monthly.csv
  client_package/data/portwatch_ju_monthly.csv
  client_package/data/portwatch_yb_monthly.csv
  client_package/data/storage_kharg_daily.csv
  client_package/data/storage_rt_daily.csv
  client_package/data/storage_yb_daily.csv
  client_package/data/events_2026.csv
"""

import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import sb
from client_pkg.fetch_portwatch import (
    discover_yanbu_ports, fetch_portcalls, monthly_export_mbpd
)

CACHE_DIR = "client_package/data"

# 2026 event chronology — hardcoded with sources
EVENTS_2026 = [
    {
        "date": "2026-03-02",
        "terminal": "RT",
        "label": "Attack / Halt",
        "description": "Drone/missile strike halts RT operations; loading suspended",
        "expected_signal": "BUILD (export freeze)",
        "source": "Reuters 2026-03-02; internal SAR obs gap",
    },
    {
        "date": "2026-06-26",
        "terminal": "RT",
        "label": "Operations resumed",
        "description": "Saudi Aramco resumes RT loadings; ~4 VLCC cleared in first week",
        "expected_signal": "DRAW (export restart)",
        "source": "Kpler; Reuters 2026-06-26",
    },
    {
        "date": "2026-07-15",
        "terminal": "RT",
        "label": "Ramp to ~90%",
        "description": "Terminal capacity back to ~90%; daily liftings normalise",
        "expected_signal": "DRAW (continued drawdown)",
        "source": "Saudi Aramco official statement; our SAR obs",
    },
    {
        "date": "2026-09-20",
        "terminal": "RT",
        "label": "East-West pipeline",
        "description": "East-West pipeline partial diversion; ~60 Mb STS transfer Sohar reported",
        "expected_signal": "DRAW or PLATEAU (route diversion)",
        "source": "TradeWinds 2026-09-20; Kpler STS estimate",
    },
    {
        "date": "2026-09-21",
        "terminal": "RT",
        "label": "Sohar STS ~60 Mb",
        "description": "Ship-to-ship transfer at Sohar anchorage confirms routing change",
        "expected_signal": "DRAW",
        "source": "Kpler; Bloomberg 2026-09-21",
    },
]


def _fetch_supabase_storage(table, terminal_id=None, label=""):
    """Fetch daily volume from Supabase. Returns DataFrame: date, vol_mn_bbl."""
    try:
        if terminal_id:
            rows = (sb.table(table)
                      .select("date,vol_mn_bbl")
                      .eq("terminal_id", terminal_id)
                      .eq("aggregation", "daily")
                      .order("date").execute().data)
        else:
            rows = (sb.table(table)
                      .select("date,vol_mn_bbl")
                      .eq("aggregation", "daily")
                      .order("date").execute().data)
        df = pd.DataFrame(rows)
        if df.empty:
            print(f"  [storage] {label}: no rows")
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"])
        df["vol_mn_bbl"] = pd.to_numeric(df["vol_mn_bbl"], errors="coerce")
        print(f"  [storage] {label}: {len(df)} rows {df['date'].min().date()} → {df['date'].max().date()}")
        return df
    except Exception as e:
        print(f"  [WARN] storage fetch {label}: {e}")
        return pd.DataFrame()


def run():
    os.makedirs(CACHE_DIR, exist_ok=True)
    print("\n[Task 0] External anchors")

    # ── PortWatch ─────────────────────────────────────────────────────────────
    print("\n  PortWatch: discovering Yanbu port candidates...")
    yanbu_candidates = discover_yanbu_ports()
    # Pick crude/commercial candidate — use first result if ambiguous
    yanbu_portid = None
    for c in yanbu_candidates:
        pt = str(c.get("porttype", "")).lower()
        if "crude" in pt or "commercial" in pt or "oil" in pt:
            yanbu_portid = c.get("portid")
            print(f"  → Selected Yanbu portid: {yanbu_portid} ({c.get('portname')})")
            break
    if yanbu_portid is None and yanbu_candidates:
        yanbu_portid = yanbu_candidates[0].get("portid")
        print(f"  → Defaulting to first candidate: {yanbu_portid} ({yanbu_candidates[0].get('portname')})")
    if yanbu_portid is None:
        yanbu_portid = "port_yanbu_unknown"
        print("  [WARN] No Yanbu port found; placeholder used")

    port_map = {
        "rt": "port1091",
        "ju": "port526",
        "yb": yanbu_portid,
    }

    for key, portid in port_map.items():
        print(f"\n  Fetching PortWatch {key.upper()} ({portid})...")
        try:
            raw = fetch_portcalls(portid, start_date="2019-01-01")
            monthly = monthly_export_mbpd(raw)
            out = os.path.join(CACHE_DIR, f"portwatch_{key}_monthly.csv")
            monthly.to_csv(out, index=False)
            print(f"  Saved: {out} ({len(monthly)} months)")
        except Exception as e:
            print(f"  [WARN] PortWatch {key}: {e}")

    # ── Storage series ─────────────────────────────────────────────────────────
    print("\n  Fetching storage series from Supabase...")

    kharg = _fetch_supabase_storage("volume_timeseries", label="Kharg")
    if not kharg.empty:
        kharg.to_csv(os.path.join(CACHE_DIR, "storage_kharg_daily.csv"), index=False)

    rt = _fetch_supabase_storage("terminal_volume_timeseries", terminal_id="ras_tanura", label="RT")
    if not rt.empty:
        rt.to_csv(os.path.join(CACHE_DIR, "storage_rt_daily.csv"), index=False)

    yb = _fetch_supabase_storage("terminal_volume_timeseries", terminal_id="yanbu", label="YB")
    if not yb.empty:
        yb.to_csv(os.path.join(CACHE_DIR, "storage_yb_daily.csv"), index=False)

    # ── Events table ──────────────────────────────────────────────────────────
    events_df = pd.DataFrame(EVENTS_2026)
    events_path = os.path.join(CACHE_DIR, "events_2026.csv")
    events_df.to_csv(events_path, index=False)
    print(f"\n  Events table: {events_path}")

    return {
        "portwatch": port_map,
        "yanbu_candidates": yanbu_candidates,
        "events": EVENTS_2026,
    }


if __name__ == "__main__":
    run()
