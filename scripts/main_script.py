import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
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
config.sh_client_id     = "sh-0ebad36f-b87f-419a-bb0a-7c1b1aec203c"
config.sh_client_secret = "Xr591lJqxer8R1l9jmvYiNBjyYMP14U0"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

sb = create_client(
    "https://yoekhkczwdeyauwarsyy.supabase.co",
    "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
)

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse",
    service_url="https://sh.dataspace.copernicus.eu"
)

START_DATE     = datetime.strptime("2025-01-01", "%Y-%m-%d")
END_DATE       = datetime.strptime("2026-06-01", "%Y-%m-%d")
RESOLUTION     = 5
BUFFER_METERS  = 300
BARREL_PER_M3  = 6.2898

LOS_AZIMUTH    = 282
WIDTH_WIDE     = 15
TANK_HEIGHT_MIN = 2

SMA_WINDOW     = 13

RAW_CACHE_PATH = "public/data_raw_cache.json"
DAILY_PATH     = "public/data_daily.json"
WEEKLY_PATH    = "public/data.json"
PLOT_PATH      = "public/volume_graph.png"

# ================= TANKS DATA =================
raw_tanks = []

for lat, lon in [
    (29.22173338156137,50.31654819949878), (29.221638820210273,50.315043243946754),
    (29.221840470329855,50.31352911803618), (29.223156804822317,50.315849029771684),
    (29.222897954869325,50.3144195158663)
]:
    raw_tanks.append({"id": f"t60_{len(raw_tanks)}", "lat": lat, "lon": lon, "diameter": 60, "h_max": 23})

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
    raw_tanks.append({"id": f"t55_{len(raw_tanks)}", "lat": lat, "lon": lon, "diameter": 55, "h_max": 23})

for lat, lon in [
    (29.22952889859548,50.312187062458655), (29.22976318244905,50.313501526357314),
    (29.229963868790236,50.31481471193477), (29.228423649589555,50.31244141506387),
    (29.228616543309535,50.313733669284346), (29.22881135666992,50.31501996070619),
    (29.22720915866873,50.312698386632235), (29.227435720069668,50.31399368455909),
    (29.227640813983122,50.31526721678495)
]:
    raw_tanks.append({"id": f"t40_{len(raw_tanks)}", "lat": lat, "lon": lon, "diameter": 40, "h_max": 23})

print(f"Loaded {len(raw_tanks)} tanks.")

# ================= GEOMETRY & MASKS =================
def get_super_bbox(tanks, buffer_m=200):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min_lat)))
    return BBox([min_lon - dlon, min_lat - dlat, max_lon + dlon, max_lat + dlat], crs=CRS.WGS84)

super_bbox = get_super_bbox(raw_tanks, buffer_m=BUFFER_METERS)

os.makedirs("public", exist_ok=True)

cached_records = []
cached_dates   = set()

if os.path.exists(RAW_CACHE_PATH):
    with open(RAW_CACHE_PATH) as f:
        cached_records = json.load(f)
    cached_dates = {pd.to_datetime(r["date"]).normalize() for r in cached_records}
    print(f"Loaded {len(cached_records)} cached raw records covering {len(cached_dates)} dates.")
else:
    print("No raw cache found — full download will run.")

try:
    catalog = SentinelHubCatalog(config=config)
    scenes = list(catalog.search(
        S1_CDSE,
        bbox=super_bbox,
        time=(START_DATE.strftime("%Y-%m-%d"), END_DATE.strftime("%Y-%m-%d")),
        filter="sat:orbit_state = 'descending'",
        fields={"include": ["properties.datetime"]}
    ))
    all_dates = sorted({pd.to_datetime(s["properties"]["datetime"]).normalize() for s in scenes})
except Exception as e:
    print(f"CRITICAL: Failed to connect to Sentinel Hub. Error: {e}")
    sys.exit(1)

new_dates = [d for d in all_dates if d not in cached_dates]
print(f"Total: {len(all_dates)} | Cached: {len(cached_dates)} | New: {len(new_dates)}")

if new_dates:
    width_m = (super_bbox.max_x - super_bbox.min_x) * 111320 * np.cos(np.deg2rad(raw_tanks[0]["lat"]))
    height_m = (super_bbox.max_y - super_bbox.min_y) * 111320
    img_width = int(width_m / RESOLUTION)
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
        radius_m = tank["diameter"] / 2.0
        cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], super_bbox, img_width, img_height)
        dist_sq = (X_grid - cx)**2 + (Y_grid - cy)**2
        dot_product = (X_grid - cx) * dx + (Y_grid - cy) * dy
        mask_far_side = dot_product > 0
        r_inner = max(0, (radius_m - WIDTH_WIDE)) / RESOLUTION
        mask_ring = (dist_sq <= (radius_m/RESOLUTION)**2) & (dist_sq >= r_inner**2)
        crescent = mask_ring & mask_far_side
        tanks_data.append({"id": tank["id"], "radius_m": radius_m, "diameter": tank["diameter"], "h_max": tank["h_max"], "crescent": crescent})

    evalscript = """
    //VERSION=3
    function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
    function evaluatePixel(sample) { return [sample.VV]; }
    """

    new_records = []
    for d in new_dates:
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
            if not data: continue
            img_linear = data[0].squeeze()
            for tank in tanks_data:
                pixels = img_linear[tank["crescent"]]
                if pixels.size > 0:
                    mean_linear = np.mean(pixels)
                    db_mean = 10 * np.log10(np.clip(mean_linear, 1e-10, None))
                    new_records.append({"date": d_str, "tank_id": tank["id"], "radius_m": float(tank["radius_m"]), "diameter": float(tank["diameter"]), "h_max": float(tank["h_max"]), "db_mean": float(db_mean)})
        except Exception as e:
            print(f"  [!] Skip {d_str}: {e}")

    if new_records:
        cached_records.extend(new_records)
        with open(RAW_CACHE_PATH, "w") as f:
            json.dump(cached_records, f)
        try:
            sb.table("raw_observations").upsert(new_records, on_conflict="date,tank_id").execute()
            print(f"  ✓ Supabase raw_observations: {len(new_records)} records saved")
        except Exception as e:
            print(f"  [!] Supabase write failed (GitHub still updated): {e}")

if not cached_records:
    print("CRITICAL: No Sentinel-1 imagery was successfully processed!")
    sys.exit(1)

df = pd.DataFrame(cached_records)
df['date'] = pd.to_datetime(df['date'])

dB_min = df['db_mean'].quantile(0.05)
dB_max = df['db_mean'].quantile(0.95)

def calculate_volume_row(row):
    vv = row["db_mean"]
    vv = max(dB_min, min(dB_max, vv))
    fill_ratio = (dB_max - vv) / (dB_max - dB_min)
    h_current = TANK_HEIGHT_MIN + fill_ratio * (row["h_max"] - TANK_HEIGHT_MIN)
    vol_m3 = np.pi * (row["radius_m"])**2 * h_current
    return vol_m3 * BARREL_PER_M3

df["barrels"] = df.apply(calculate_volume_row, axis=1)

df["day"] = df["date"].dt.normalize()
daily_tank = df.groupby(['day', 'tank_id'])["barrels"].mean().reset_index()
daily_total = daily_tank.groupby("day")["barrels"].sum().reset_index()
daily_total["vol_mn_bbl"] = daily_total["barrels"] / 1_000_000

result_daily = {
    "dates": daily_total["day"].dt.strftime("%Y-%m-%d").tolist(),
    "values": daily_total["vol_mn_bbl"].round(3).tolist(),
    "meta": {"orbit": "descending", "polarization": "VV", "method": "inverse_crescent_radiometry", "mask_type": "15m_far_side_crescent", "scaling": "global_5_95_percentiles", "smoothing": "none", "aggregation": "daily"}
}
with open(DAILY_PATH, "w") as f:
    json.dump(result_daily, f, indent=2)

try:
    daily_rows = [{"date": date, "vol_mn_bbl": val, "aggregation": "daily", "smoothed": False} for date, val in zip(result_daily["dates"], result_daily["values"])]
    sb.table("volume_timeseries").upsert(daily_rows, on_conflict="date,aggregation,smoothed").execute()
    print(f"  ✓ Supabase volume_timeseries daily: {len(daily_rows)} rows")
except Exception as e:
    print(f"  [!] Supabase daily write failed: {e}")

df["week_start"] = (df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")).dt.normalize()
weekly_tank = df.groupby(['week_start', 'tank_id'])["barrels"].mean().reset_index()
weekly_total = weekly_tank.groupby("week_start")["barrels"].sum().reset_index()
weekly_total["vol_mn_bbl"] = weekly_total["barrels"] / 1_000_000
weekly_total['smoothed_vol'] = weekly_total['vol_mn_bbl'].rolling(window=SMA_WINDOW, min_periods=1, center=True).mean()
weekly_total = weekly_total.dropna(subset=['smoothed_vol'])

result_weekly = {
    "dates": weekly_total["week_start"].dt.strftime("%Y-%m-%d").tolist(),
    "values": weekly_total["smoothed_vol"].round(3).tolist(),
    "meta": {"orbit": "descending", "polarization": "VV", "method": "inverse_crescent_radiometry", "mask_type": "15m_far_side_crescent", "scaling": "global_5_95_percentiles", "smoothing": f"SMA_{SMA_WINDOW}w_centered", "predictive_edge": 10}
}
with open(WEEKLY_PATH, "w") as f:
    json.dump(result_weekly, f, indent=2)

try:
    weekly_rows = [{"date": date, "vol_mn_bbl": val, "aggregation": "weekly", "smoothed": True} for date, val in zip(result_weekly["dates"], result_weekly["values"])]
    sb.table("volume_timeseries").upsert(weekly_rows, on_conflict="date,aggregation,smoothed").execute()
    print(f"  ✓ Supabase volume_timeseries weekly: {len(weekly_rows)} rows")
except Exception as e:
    print(f"  [!] Supabase weekly write failed: {e}")

print(f"\nPipeline complete!")

plt.figure(figsize=(14, 7))
plt.plot(weekly_total["week_start"], weekly_total["vol_mn_bbl"], marker='.', linestyle='', color='gray', alpha=0.4, label="Raw Weekly Extractions")
plt.plot(weekly_total["week_start"], weekly_total["smoothed_vol"], marker='s', linestyle='-', color='indigo', linewidth=3, label=f"Inverse Production Model (SMA-{SMA_WINDOW})")
plt.title(f"Production Output: Kharg Island Storage (31 Tanks)\nInverse Crescent Radiometry (SMA-{SMA_WINDOW})", fontsize=16)
plt.xlabel("Date", fontsize=13)
plt.ylabel("Total Active Volume (Mn. Barrels)", fontsize=13)
plt.grid(True, linestyle='--', alpha=0.5)
max_cap = sum([np.pi*(t["diameter"]/2)**2 * t["h_max"] * BARREL_PER_M3 for t in raw_tanks]) / 1_000_000
plt.axhline(y=max_cap, color='r', linestyle='--', linewidth=1, label=f"Max Capacity (~{max_cap:.1f} M)")
plt.legend(fontsize=12, loc="upper right")
plt.tight_layout()
plt.savefig(PLOT_PATH)
