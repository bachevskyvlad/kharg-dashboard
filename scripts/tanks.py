"""
tanks.py — Tank inventory and bounding-box geometry.

Each entry in raw_tanks describes one floating-roof tank at Kharg Island.
Fields:
  id        — type prefix + index (t60_0, t55_5, t40_12, t25_25, …)
  lat/lon   — centre of the tank roof (WGS84)
  radius    — actual tank radius in metres (from satellite measurement)
  h_max     — maximum fill height in metres (API 650 standard aspect ratios)

Tank sizing groups:
  t60  radius=60m  h_max=18m   5 tanks   — largest, southern cluster
  t55  radius=55m  h_max=17m  18 tanks   — main northern cluster
  t40  radius=40m  h_max=14m  12 tanks   — mid-size, most active loading tanks
  t25  radius=25m  h_max=12m  12 tanks   — smallest; several persistently empty

Known structural outliers (do not use for trend analysis alone):
  t55_5, t55_10   — mean dB >14 over 18 months → likely offline / idle
  t55_31, t60_1   — mean dB <1 chronically → probable strategic reserve, always full
  t60_2, t55_17   — std >6 dB → primary active loading terminals

Total mapped: 47 tanks. Max theoretical capacity ≈ 31.78 Mn bbl.
Adding a new tank: append to raw_tanks with correct id prefix and measured
radius/lat/lon. Re-run the full pipeline (force_reset=true) to recalculate
global dB percentiles and historical volumes with the new tank included.
"""

import numpy as np
from sentinelhub import BBox, CRS


raw_tanks = []

# ── t60 cluster (5 tanks, southern shore) ────────────────────────────────────
for lat, lon in [
    (29.22173338156137,  50.31654819949878),
    (29.221638820210273, 50.315043243946754),
    (29.221840470329855, 50.31352911803618),
    (29.223156804822317, 50.315849029771684),
    (29.222897954869325, 50.3144195158663),
]:
    raw_tanks.append({"id": f"t60_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 60, "h_max": 18})

# ── t55 cluster (17 tanks, central-northern) ─────────────────────────────────
for lat, lon in [
    (29.23596908236423,  50.31262041733899),
    (29.236450714039865, 50.31093940154909),
    (29.237953299445927, 50.31084946173491),
    (29.239068986858886, 50.30968216852048),
    (29.238926386438376, 50.306814019231595),
    (29.237608159424354, 50.30726790052075),
    (29.236277626607237, 50.307724616432196),
    (29.23632825886285,  50.30930379724654),
    (29.234972729277782, 50.3081865326845),
    (29.23511372342508,  50.30981988700097),
    (29.233643820114974, 50.30863801777735),
    (29.233830846966335, 50.31028094525233),
    (29.23144045674206,  50.308740382712706),
    (29.231783644202153, 50.310385461009616),
    (29.232076421746672, 50.312234207587345),
    (29.23021527314446,  50.3101614506557),
    (29.228871179764596, 50.309966015820464),
]:
    raw_tanks.append({"id": f"t55_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 55, "h_max": 17})

# ── t40 cluster (9 tanks, central) ───────────────────────────────────────────
for lat, lon in [
    (29.22952889859548,  50.312187062458655),
    (29.22976318244905,  50.313501526357314),
    (29.229963868790236, 50.31481471193477),
    (29.228423649589555, 50.31244141506387),
    (29.228616543309535, 50.313733669284346),
    (29.22881135666992,  50.31501996070619),
    (29.22720915866873,  50.312698386632235),
    (29.227435720069668, 50.31399368455909),
    (29.227640813983122, 50.31526721678495),
]:
    raw_tanks.append({"id": f"t40_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 40, "h_max": 14})

# ── t55_31 — new tank added after initial mapping ────────────────────────────
# Confirmed full most passes (probable strategic reserve). Added separately to
# preserve original t55_* index numbering.
raw_tanks.append({
    "id": "t55_31",
    "lat": 29.232346117474552,
    "lon": 50.313766336270064,
    "radius": 55,
    "h_max": 17,
})

# ── t40 additions (3 tanks, western row) ─────────────────────────────────────
for lat, lon in [
    (29.230717804109947, 50.31192906218232),
    (29.230947410364493, 50.31324186520479),
    (29.231135117157258, 50.31454377331308),
]:
    raw_tanks.append({"id": f"t40_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 40, "h_max": 14})

# ── t25 cluster (12 tanks, eastern rows) ─────────────────────────────────────
for lat, lon in [
    (29.226211162396392, 50.31288089406942),
    (29.226383532289553, 50.313809940357444),
    (29.226542892768347, 50.31474811903658),
    (29.226689095834363, 50.31565041182445),
    (29.22515641565613,  50.31310639099006),
    (29.22532307473051,  50.31405678957588),
    (29.225474230400657, 50.31496277701283),
    (29.22561536435356,  50.315886541434466),
    (29.22435349448338,  50.313281303050275),
    (29.224512933127766, 50.31422412891253),
    (29.224656215116465, 50.31513496008376),
    (29.22481810164328,  50.316063441149545),
]:
    raw_tanks.append({"id": f"t25_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 25, "h_max": 12})


def get_super_bbox(tanks, buffer_m=200):
    """
    Returns a BBox enclosing all tanks plus a buffer in metres.

    The buffer prevents the image crop from cutting off crescent pixels
    at the cluster edge. 200m default; pipeline uses 300m (BUFFER_METERS)
    to be safe with larger tanks whose crescents extend beyond tank radius.
    """
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox(
        [min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat],
        crs=CRS.WGS84
    )
