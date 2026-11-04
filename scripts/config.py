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
# Use env var if non-empty, otherwise fall back to the hardcoded credentials.
# os.environ.get(key, default) doesn't help when the env var is set to ""
# (GitHub Actions sets it to "" if the secret is missing), hence the `or`.
_sh_id  = os.environ.get("CLIENT_ID")     or ""
_sh_sec = os.environ.get("CLIENT_SECRET") or ""
if not _sh_id or not _sh_sec:
    import sys; sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")
config.sh_client_id     = _sh_id
config.sh_client_secret = _sh_sec
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
_sb_url = os.environ.get("SUPABASE_URL") or ""
_sb_key = os.environ.get("SUPABASE_KEY") or ""
if not _sb_url or not _sb_key:
    import sys; sys.exit("FATAL: SUPABASE_URL and SUPABASE_KEY env vars must be set")
sb = create_client(_sb_url, _sb_key)

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
# The crescent mask captures the double-bounce (dihedral) return from the FAR
# interior wall. 282° selects the hemisphere of maximum dihedral contrast for
# DESC orbit. Wider annulus = more pixels = more stable dB, but risk of
# spilling into adjacent tanks. 15m is empirically validated.
LOS_AZIMUTH    = 282        # degrees — line-of-sight azimuth from satellite
WIDTH_WIDE     = 15         # metres — annulus width of the crescent mask

# ── Raw dB floor ──────────────────────────────────────────────────────────────
# Minimum physically plausible SAR dB return for open water/oil surface.
# Values below this are instrument noise or processing artefacts.
# Keep at -5.0 — changing this breaks time-series comparability.
DB_FLOOR = -5.0

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
