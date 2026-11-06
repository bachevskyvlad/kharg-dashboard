"""
export_data.py — Write pipeline outputs to JSON files and Supabase.

Output files (committed to repo, served directly to frontend):
  data/data_daily.json   — unsmoothed daily volumes; used by dashboard charts
  data/data.json         — EWMA-smoothed weekly volumes; used by marketing/monitor

Supabase tables written:
  raw_observations       — per-tank dB records (upsert on date, tank_id)
  volume_timeseries      — aggregated volumes (upsert on date, aggregation, smoothed)
  kharg_gross_flows_v2   — gross in/out flows per pass, computed in Python by
                           scripts/gross_flows.py (calibration = volume.py),
                           upsert on date_to. See update_gross_flows().
  kharg_gross_flows      — DEPRECATED matview (old per-tank calibration),
                           no longer refreshed; drop after 2026-11-20.

All Supabase writes are non-fatal: the pipeline continues and commits the
JSON files even if Supabase is unreachable. GitHub is the source of truth
for frontend data; Supabase is secondary (used for the paid dashboard queries).
"""

import json
import os


def save_raw_cache(cached_records, raw_cache_path):
    """Writes the full raw observations list to the local JSON cache file."""
    os.makedirs(os.path.dirname(raw_cache_path), exist_ok=True)
    with open(raw_cache_path, "w") as f:
        json.dump(cached_records, f)


def upsert_raw_observations(new_records, sb):
    """
    Upserts new per-tank dB records to Supabase raw_observations.
    on_conflict="date,tank_id" means re-running the pipeline for the same
    date is safe — it updates rather than duplicates.
    """
    if not new_records:
        return
    try:
        sb.table("raw_observations").upsert(new_records, on_conflict="date,tank_id").execute()
        print(f"  Supabase raw_observations: {len(new_records)} records saved")
    except Exception as e:
        print(f"  [!] Supabase write failed (GitHub still updated): {e}")


def save_daily_json(daily_total, daily_path, sb):
    """
    Writes data_daily.json and upserts to volume_timeseries (aggregation='daily').

    JSON format: {dates: [...], values: [...], meta: {...}}
    values are in Mn bbl rounded to 3 decimal places (~1000 bbl precision).
    """
    result = {
        "dates":  daily_total["day"].dt.strftime("%Y-%m-%d").tolist(),
        "values": daily_total["vol_mn_bbl"].round(3).tolist(),
        "meta": {
            "orbit":        "descending",
            "polarization": "VV",
            "method":       "inverse_crescent_radiometry",
            "mask_type":    "15m_far_side_crescent",
            "scaling":      "global_5_95_percentiles",
            "smoothing":    "none",
            "aggregation":  "daily",
        }
    }
    os.makedirs(os.path.dirname(daily_path), exist_ok=True)
    with open(daily_path, "w") as f:
        json.dump(result, f, indent=2)

    try:
        rows = [
            {"date": d, "vol_mn_bbl": v, "aggregation": "daily", "smoothed": False}
            for d, v in zip(result["dates"], result["values"])
        ]
        sb.table("volume_timeseries").upsert(rows, on_conflict="date,aggregation,smoothed").execute()
        print(f"  Supabase volume_timeseries daily: {len(rows)} rows")
    except Exception as e:
        print(f"  [!] Supabase daily write failed: {e}")

    return result


def save_weekly_json(weekly_total, weekly_path, sb):
    """
    Writes data.json (EWMA-smoothed weekly) and upserts to volume_timeseries.

    Uses smoothed_vol column, not raw vol_mn_bbl. The raw weekly values are
    available in weekly_total for the plot but are NOT written to the JSON —
    the frontend always gets smoothed data from data.json.
    """
    from config import EWMA_ALPHA
    result = {
        "dates":  weekly_total["week_start"].dt.strftime("%Y-%m-%d").tolist(),
        "values": weekly_total["smoothed_vol"].round(3).tolist(),
        "meta": {
            "orbit":        "descending",
            "polarization": "VV",
            "method":       "inverse_crescent_radiometry",
            "mask_type":    "15m_far_side_crescent",
            "scaling":      "global_5_95_percentiles",
            "smoothing":    f"EWMA_alpha{EWMA_ALPHA}",
        }
    }
    os.makedirs(os.path.dirname(weekly_path), exist_ok=True)
    with open(weekly_path, "w") as f:
        json.dump(result, f, indent=2)

    try:
        rows = [
            {"date": d, "vol_mn_bbl": v, "aggregation": "weekly", "smoothed": True}
            for d, v in zip(result["dates"], result["values"])
        ]
        sb.table("volume_timeseries").upsert(rows, on_conflict="date,aggregation,smoothed").execute()
        print(f"  Supabase volume_timeseries weekly: {len(rows)} rows")
    except Exception as e:
        print(f"  [!] Supabase weekly write failed: {e}")

    return result


def update_gross_flows(sb):
    """
    Recomputes Kharg gross flows in Python (scripts/gross_flows.py) and upserts
    them into kharg_gross_flows_v2. Calibration is volume.compute_volumes —
    the same as volume_timeseries. Non-fatal on error.
    """
    try:
        from gross_flows import run as _run_gross_flows
        _run_gross_flows(sb)
    except Exception as e:
        print(f"  [!] kharg_gross_flows_v2 update failed: {e}")


def refresh_gross_flows(sb):
    """
    DEPRECATED (2026-11-06) — do not call. Kept only for the 2-week transition.

    Called the Supabase RPC refresh_gross_flows(), which recomputed the matview
    kharg_gross_flows inside the database with a per-tank p5/p95 calibration
    (different from volume.py). Replaced by update_gross_flows().
    The RPC and the matview are dropped after 2026-11-20 (see README.md).
    """
    raise RuntimeError("refresh_gross_flows() is deprecated — use update_gross_flows()")
