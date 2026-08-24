"""
locations.py — All terminal and tank data, loaded from data/locations.json.

To add or edit tanks: update data/locations.json, then re-run the pipeline.
"""

import json
from pathlib import Path
import numpy as np
from sentinelhub import BBox, CRS

_DATA = json.loads((Path(__file__).parent.parent / "data" / "locations.json").read_text())
_BY_ID = {loc["id"]: loc for loc in _DATA["locations"]}

def _to_tank(t):
    entry = {"id": t["id"], "lat": t["lat"], "lon": t["lon"],
             "radius": t["radius_m"], "h_max": t["h_max_m"]}
    if t.get("uncertain"):
        entry["uncertain"] = True
    return entry

# Kharg tanks list — used directly by main pipeline
raw_tanks = [_to_tank(t) for t in _BY_ID["kharg"]["tanks"]]

# All non-Kharg terminals — used by multi-terminal pipeline
TERMINALS = [
    {
        "id": loc["id"],
        "name": loc["name"],
        "country": loc["country"],
        "los_azimuth": loc.get("los_azimuth", 282),
        "tanks": [_to_tank(t) for t in loc["tanks"]],
    }
    for loc in _DATA["locations"] if loc["id"] != "kharg"
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
