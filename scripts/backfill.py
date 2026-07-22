"""
Historical backfill: Sentinel-1 SAR data for Kharg Island from 2014 to present.
Seeds already-processed dates from Supabase so re-runs are safe and incremental.
Trigger via GitHub Actions workflow_dispatch or run locally.
"""
import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
warnings.filterwarnings('ignore')
from sentinelhub import (
    SentinelHubRequest,
    DataCollection,
    MimeType,
    BBox,
    CRS,
    SentinelHubCatalog,
    SHConfig
)
from supabase import create_client

# ================= CONFIG =================
config = SHConfig()
config.sh_client_id     = os.environ.get("SH_CLIENT_ID", "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4")
config.sh_client_secret = os.environ.get("SH_CLIENT_SECRET", "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU")
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://yoekhkczwdeyauwarsyy.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse",
    service_url="https://sh.dataspace.copernicus.eu"
)

# Sentinel-1A first usable imagery over Persian Gulf: ~June 2014
START_DATE    = datetime.strptime(os.environ.get("BACKFILL_START", "2014-06-01"), "%Y-%m-%d")
END_DATE      = datetime.strptime(os.environ.get("BACKFILL_END", datetime.utcnow().strftime("%Y-%m-%d")), "%Y-%m-%d")
RESOLUTION    = 5
BUFFER_METERS = 300
BARREL_PER_M3 = 6.2898
LOS_AZIMUTH   = 282
WIDTH_WIDE    = 15
TANK_HEIGHT_MIN = 2

print(f"Backfill range: {START_DATE.date()} → {END_DATE.date()}")

# ================= TANKS =================
raw_tanks = []

for lat, lon in [
    (29.22173338156137,50.31654819949878), (29.221638820210273,50.315043243946754),
    (29.221840470329855,50.31352911803618), (29.223156804822317,50.315849029771684),
    (29.222897954869325,50.3144195158663)
]:
    raw_tanks.append({"id": f"t60_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 60, "h_max": 18})

for lat, lon in [
    (29.23596908236423,50.31262041733899), (29.236450714039865,50.31093940154909),
    (29.237953299445927,50.31084946173491), (29.239068986858886,50.30968216852048),
    (29.238926386438376,50.306814019231595), (29.237608159424354,50.30726790052075),
    (29.236277626607237,50.307724616432196), (29.23632825886285,50.30930379724654),
    (29.234972729277782,50.3081865326845), (29.23511372342508,50.30981988700097),
    (29.233643820114974,50.30863801777735), (29.233830846966335,50.31028094525233),
    (29.23144045674206,50.308740382712706), (29.231783644202153,50.310385461009616),
    (29.232076421746672,50.312234207587345), (29.23021527314446,50.3101614506557),
    (29.228871179764596,50.309966015820464)
]:
    raw_tanks.append({"id": f"t55_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 55, "h_max": 17})

for lat, lon in [
    (29.22952889859548,50.312187062458655), (29.22976318244905,50.313501526357314),
    (29.229963868790236,50.31481471193477), (29.228423649589555,50.31244141506387),
    (29.228616543309535,50.313733669284346), (29.22881135666992,50.31501996070619),
    (29.22720915866873,50.312698386632235), (29.227435720069668,50.31399368455909),
    (29.227640813983122,50.31526721678495)
]:
    raw_tanks.append({"id": f"t40_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 40, "h_max": 14})

raw_tanks.append({"id": "t55_31", "lat": 29.232346117474552, "lon": 50.313766336270064, "radius": 55, "h_max": 17})

for lat, lon in [
    (29.230717804109947, 50.31192906218232),
    (29.230947410364493, 50.31324186520479),
    (29.231135117157258, 50.31454377331308),
]:
    raw_tanks.append({"id": f"t40_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 40, "h_max": 14})

for lat, lon in [
    (29.226211162396392, 50.31288089406942), (29.226383532289553, 50.313809940357444),
    (29.226542892768347, 50.31474811903658), (29.226689095834363, 50.31565041182445),
    (29.22515641565613,  50.31310639099006), (29.22532307473051,  50.31405678957588),
    (29.225474230400657, 50.31496277701283), (29.22561536435356,  50.315886541434466),
    (29.22435349448338,  50.313281303050275), (29.224512933127766, 50.31422412891253),
    (29.224656215116465, 50.31513496008376), (29.22481810164328,  50.316063441149545),
]:
    raw_tanks.append({"id": f"t25_{len(raw_tanks)}", "lat": lat, "lon": lon, "radius": 25, "h_max": 12})

print(f"Loaded {len(raw_tanks)} tanks.")

# ================= SEED CACHED DATES FROM SUPABASE =================
print("Loading already-processed dates from Supabase raw_observations...")
try:
    existing = set()
    offset = 0
    while True:
        rows = sb.table("raw_observations").select("date").range(offset, offset + 999).execute().data
        for r in rows:
            existing.add(pd.to_datetime(r["date"]).normalize())
        if len(rows) < 1000:
            break
        offset += 1000
    print(f"  {len(existing)} dates already in Supabase — will skip these.")
except Exception as e:
    print(f"  [!] Could not load existing dates from Supabase: {e}. Will process everything.")
    existing = set()

# ================= GEOMETRY =================
def get_super_bbox(tanks, buffer_m=200):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min_lat)))
    return BBox([min_lon - dlon, min_lat - dlat, max_lon + dlon, max_lat + dlat], crs=CRS.WGS84)

super_bbox = get_super_bbox(raw_tanks, buffer_m=BUFFER_METERS)

# ================= SCENE DISCOVERY =================
try:
    catalog = SentinelHubCatalog(config=config)
    scenes = list(catalog.search(
        S1_CDSE,
        bbox=super_bbox,
        time=(START_DATE.strftime("%Y-%m-%d"), END_DATE.strftime("%Y-%m-%d")),
        filter="sat:orbit_state = 'descending'",
        fields={"include": ["properties.datetime"]}
    ))
    raw_dates = sorted(pd.to_datetime(s["properties"]["datetime"]) for s in scenes)
    deduped = []
    for dt in raw_dates:
        if not deduped or (dt - deduped[-1]).total_seconds() > 43200:
            deduped.append(dt)
    all_dates = sorted({d.normalize() for d in deduped})
    print(f"Catalog: {len(all_dates)} unique scene dates found.")
except Exception as e:
    print(f"CRITICAL: Sentinel Hub catalog failed: {e}")
    sys.exit(1)

new_dates = [d for d in all_dates if d not in existing]
print(f"To process: {len(new_dates)} new dates (skipping {len(all_dates) - len(new_dates)} already in Supabase)")

if not new_dates:
    print("Nothing to do — all dates already in Supabase.")
    sys.exit(0)

# ================= IMAGE PROCESSING =================
width_m  = (super_bbox.max_x - super_bbox.min_x) * 111320 * np.cos(np.deg2rad(raw_tanks[0]["lat"]))
height_m = (super_bbox.max_y - super_bbox.min_y) * 111320
img_width  = int(width_m / RESOLUTION)
img_height = int(height_m / RESOLUTION)

def lonlat_to_pix(lon, lat, bbox, w, h):
    x = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    y = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return x, y

Y_grid, X_grid = np.indices((img_height, img_width))
dx = np.sin(np.deg2rad(LOS_AZIMUTH))
dy = -np.cos(np.deg2rad(LOS_AZIMUTH))

tanks_data = []
for tank in raw_tanks:
    radius_m = tank["radius"]
    cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], super_bbox, img_width, img_height)
    dist_sq = (X_grid - cx)**2 + (Y_grid - cy)**2
    dot_product = (X_grid - cx) * dx + (Y_grid - cy) * dy
    mask_far_side = dot_product > 0
    r_inner = max(0, (radius_m - WIDTH_WIDE)) / RESOLUTION
    mask_ring = (dist_sq <= (radius_m/RESOLUTION)**2) & (dist_sq >= r_inner**2)
    tanks_data.append({
        "id": tank["id"],
        "radius_m": radius_m,
        "diameter": radius_m * 2,
        "h_max": tank["h_max"],
        "crescent": mask_ring & mask_far_side
    })

evalscript = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""

BATCH_SIZE = 50  # upsert to Supabase every N processed dates
batch_records = []
total_saved = 0

for i, d in enumerate(new_dates):
    d_str = d.strftime("%Y-%m-%d")
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(d_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
            other_args={"processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN", "demInstance": "COPERNICUS_30"}}
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=super_bbox, size=(img_width, img_height), config=config
    )
    try:
        data = request.get_data()
        if not data:
            print(f"  [{i+1}/{len(new_dates)}] {d_str}: no data returned, skipping")
            continue
        img_linear = data[0].squeeze()
        for tank in tanks_data:
            pixels = img_linear[tank["crescent"]]
            if pixels.size > 0:
                mean_linear = np.mean(pixels)
                db_mean = 10 * np.log10(np.clip(mean_linear, 1e-10, None))
                batch_records.append({
                    "date": d_str,
                    "tank_id": tank["id"],
                    "radius_m": float(tank["radius_m"]),
                    "diameter": float(tank["diameter"]),
                    "h_max": float(tank["h_max"]),
                    "db_mean": float(db_mean)
                })
        print(f"  [{i+1}/{len(new_dates)}] {d_str}: processed {len(tanks_data)} tanks")
    except Exception as e:
        print(f"  [{i+1}/{len(new_dates)}] {d_str}: error — {e}")

    # Flush batch to Supabase
    if len(batch_records) >= BATCH_SIZE * len(tanks_data) or i == len(new_dates) - 1:
        if batch_records:
            try:
                sb.table("raw_observations").upsert(batch_records, on_conflict="date,tank_id").execute()
                total_saved += len(batch_records)
                print(f"  → Flushed {len(batch_records)} records to Supabase (total: {total_saved})")
                batch_records = []
            except Exception as e:
                print(f"  [!] Supabase flush failed: {e}")

print(f"\nBackfill complete. Total records saved: {total_saved}")
