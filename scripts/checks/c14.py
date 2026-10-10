"""
C14 — S2 shadow resolvability per-tank.

Expected shadow width at empty tank = h_max_m × tan(sun_zenith_rad) / 10
(result in pixels at 10 m/px S2 resolution).

Sun zenith is approximated from terminal lat/lon and the date of each observation
in the DB. We use SAR observation dates as a proxy for S2 availability, since S2
coverage dates are not stored separately for non-Jask terminals.

For terminals without any S2-specific obs in the DB, this gives the theoretical
resolvability envelope over the SAR series dates.

Approximation: sun zenith via simplified solar declination + hour-angle formula
(NOAA-style). Noon local time assumed (worst case = shortest shadow = most demanding).
"""

import math
import numpy as np
import pandas as pd
from . import RUN_DATE, SHA

S2_RESOLUTION_M = 10.0  # metres per pixel


def _sun_zenith_noon(lat_deg: float, lon_deg: float, date: pd.Timestamp) -> float:
    """Approximate solar zenith at local solar noon for given lat/lon/date."""
    doy = date.day_of_year
    # Solar declination (degrees)
    decl = 23.45 * math.sin(math.radians(360 / 365 * (doy - 81)))
    decl_r = math.radians(decl)
    lat_r = math.radians(lat_deg)
    # At solar noon, hour angle = 0 → cos(zenith) = cos(lat)*cos(decl) + sin(lat)*sin(decl)
    cos_z = math.cos(lat_r) * math.cos(decl_r) + math.sin(lat_r) * math.sin(decl_r)
    cos_z = max(-1.0, min(1.0, cos_z))
    zenith_deg = math.degrees(math.acos(cos_z))
    return zenith_deg


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    # Representative lat/lon per terminal from registry
    lats = [t["lat"] for t in registry_tanks]
    lons = [t["lon"] for t in registry_tanks]
    term_lat = float(np.mean(lats))
    term_lon = float(np.mean(lons))

    # Compute zenith for each unique date
    unique_dates = df["date"].drop_duplicates()
    zenith_by_date = {
        d: _sun_zenith_noon(term_lat, term_lon, d)
        for d in unique_dates
    }

    rows = []
    for t in registry_tanks:
        tid = t["id"]
        h_max = float(t.get("h_max_m", 14))
        tank_dates = df[df["tank_id"] == tid]["date"].drop_duplicates()

        if len(tank_dates) == 0:
            rows.append({
                "terminal_id": terminal_id, "tank_id": tid,
                "h_max_m": h_max, "n_dates": 0,
                "shadow_px_median": None, "shadow_px_p10": None, "shadow_px_p90": None,
                "check_id": "C14", "run_date": RUN_DATE, "git_sha": SHA,
                "verdict": "",
            })
            continue

        shadow_pxs = []
        for d in tank_dates:
            z_deg = zenith_by_date.get(d, float("nan"))
            if np.isnan(z_deg) or z_deg >= 90.0:
                continue
            shadow_m = h_max * math.tan(math.radians(z_deg))
            shadow_pxs.append(shadow_m / S2_RESOLUTION_M)

        if not shadow_pxs:
            med = p10 = p90 = None
        else:
            med = round(float(np.median(shadow_pxs)), 2)
            p10 = round(float(np.percentile(shadow_pxs, 10)), 2)
            p90 = round(float(np.percentile(shadow_pxs, 90)), 2)

        # Sample calc for the median date (for audit/verification)
        sample_date = None
        sample_zenith = None
        sample_shadow_m = None
        sample_shadow_px = None
        if shadow_pxs:
            sorted_dates = sorted(tank_dates)
            mid_idx = len(sorted_dates) // 2
            for d in [sorted_dates[mid_idx]]:
                z = zenith_by_date.get(d, float("nan"))
                if not np.isnan(z) and z < 90.0:
                    sample_date = str(d.date())
                    sample_zenith = round(z, 2)
                    sample_shadow_m = round(h_max * math.tan(math.radians(z)), 2)
                    sample_shadow_px = round(sample_shadow_m / S2_RESOLUTION_M, 3)

        h_source = "registry_measured" if "h_max_m" in t else "assumed_default"

        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "h_max_m": h_max, "h_source": h_source,
            "n_dates": len(tank_dates),
            "shadow_px_median": med,
            "shadow_px_p10": p10,
            "shadow_px_p90": p90,
            "sample_date": sample_date,
            "sample_zenith_deg": sample_zenith,
            "sample_shadow_m": sample_shadow_m,
            "sample_shadow_px": sample_shadow_px,
            "check_id": "C14", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
