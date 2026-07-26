"""
config.py — Credentials, API clients, and pipeline constants.

All secrets come from environment variables injected by GitHub Actions.
The Sentinel Hub client_id/secret here are the Copernicus Dataspace
credentials (NOT the old sentinel-hub.com credentials — different OAuth
endpoint). If auth fails, check sh_token_url matches the CDSE identity URL.
"""

import os
from sentinelhub import SHConfig, DataCollection
from supabase import create_client

# ── Sentinel Hub (Copernicus Dataspace) ──────────────────────────────────────
# Credentials default to env vars so the same config works locally and in CI.
# The CDSE token URL differs from legacy sentinelhub.com — keep it explicit.
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID",     "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4")
config.sh_client_secret = os.environ.get("CLIENT_SECRET", "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU")
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

# Custom DataCollection pointing at CDSE service URL.
# sentinel-hub.com and dataspace.copernicus.eu use the same collection names
# but different base URLs — defining it here keeps sar_fetch.py clean.
S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse",
    service_url="https://sh.dataspace.copernicus.eu"
)

# ── Supabase ─────────────────────────────────────────────────────────────────
# Publishable (anon) key — safe to commit. Row-level security controls writes.
# Tables: raw_observations, volume_timeseries, kharg_daily, kharg_signals.
sb = create_client(
    "https://yoekhkczwdeyauwarsyy.supabase.co",
    "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
)

# ── SAR / imagery constants ───────────────────────────────────────────────────
# Sentinel-1 descending orbit over Kharg: ~6-day revisit.
# History starts June 2014 (Sentinel-1A launch + first Kharg coverage).
from datetime import datetime
import numpy as np

START_DATE     = datetime.strptime("2014-06-01", "%Y-%m-%d")
RESOLUTION     = 5          # metres per pixel — 5m is the Sentinel-1 IW GRD floor
BUFFER_METERS  = 300        # padding around the tank cluster bounding box

# ── Volume calculation constants ──────────────────────────────────────────────
BARREL_PER_M3  = 6.2898     # 1 m³ = 6.2898 barrels (exact petroleum conversion)
TANK_HEIGHT_MIN = 2         # metres — floating roof minimum resting height;
                            # below this the roof physically can't go, so any
                            # fill_ratio=0 still yields 2m of dead volume.

# ── Crescent mask geometry ────────────────────────────────────────────────────
# Sentinel-1 descending orbit illuminates from the SW at Kharg (azimuth ~282°).
# The crescent mask captures the radar shadow on the FAR side of each tank wall.
# Wider crescent = more shadow pixels = more stable dB mean, but risk of
# capturing adjacent tank reflections. 15m annulus is empirically validated.
LOS_AZIMUTH    = 282        # degrees — line-of-sight azimuth from satellite
WIDTH_WIDE     = 15         # metres — annulus width of the crescent mask

# ── Smoothing ─────────────────────────────────────────────────────────────────
# EWMA on weekly aggregates only. Alpha=0.1 gives ~10-week effective memory,
# which smooths out single-pass noise without lagging trend reversals badly.
# Daily output is intentionally unsmoothed — analysts want raw signal.
EWMA_ALPHA     = 0.1

# ── File paths ────────────────────────────────────────────────────────────────
RAW_CACHE_PATH = "public/data_raw_cache.json"   # per-tank raw dB records
DAILY_PATH     = "data/data_daily.json"         # served to frontend
WEEKLY_PATH    = "data/data.json"               # served to frontend (smoothed)
PLOT_PATH      = "public/volume_graph.png"      # uploaded as CI artifact
