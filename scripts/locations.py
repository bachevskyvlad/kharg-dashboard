"""
locations.py — Adapts tank_registry.LOCATIONS for use by the SAR pipeline.

Source of truth: scripts/tank_registry.py
Do not edit coordinates here — edit tank_registry.py.

Exports:
  raw_tanks   — list of Kharg tanks in pipeline format (radius/h_max keys)
  TERMINALS   — list of all non-Kharg terminals in pipeline format
  get_super_bbox(tanks, buffer_m) — BBox enclosing all tanks + buffer
"""

import numpy as np
from sentinelhub import BBox, CRS
from tank_registry import LOCATIONS

_BY_ID = {loc["id"]: loc for loc in LOCATIONS}


def _to_pipeline_tank(t):
    entry = {"id": t["id"], "lat": t["lat"], "lon": t["lon"],
             "radius": t["radius_m"], "h_max": t["h_max_m"]}
    if t.get("uncertain"):
        entry["uncertain"] = True
    return entry


raw_tanks = [_to_pipeline_tank(t) for t in _BY_ID["kharg"]["tanks"]]

TERMINALS = [
    {
        "id": loc["id"],
        "name": loc["name"],
        "country": loc["country"],
        "los_azimuth": loc.get("los_azimuth", 282),
        "orbit_state": loc.get("orbit_state", "descending"),
        "relative_orbit": loc.get("relative_orbit"),
        "tanks": [_to_pipeline_tank(t) for t in loc["tanks"]],
        **( {"clusters": loc["clusters"]} if "clusters" in loc else {} ),
    }
    for loc in LOCATIONS if loc["id"] != "kharg"
]


def get_super_bbox(tanks, buffer_m=200):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox(
        [min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat],
        crs=CRS.WGS84
    )
