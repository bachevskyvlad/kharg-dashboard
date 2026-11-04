"""
Gulf Oil Storage Intelligence Pipeline
P95 calibration per country, weekly + monthly aggregation
"""

import os
import sys
import json
import warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

SH_CLIENT_ID = os.environ.get("CLIENT_ID", "")
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_BASE_URL = "https://sh.dataspace.copernicus.eu"

CACHE_PATH = 'data/gulf_raw_cache.json'
OUTPUT_PATH = 'data/fragility.json'

RESOLUTION = 5
BUFFER_METERS = 300
BARREL_PER_M3 = 6.2898
LOS_AZIMUTH = 282
WIDTH_WIDE = 15
TANK_HEIGHT_MIN = 2

START_DATE = "2019-01-01"
END_DATE = datetime.utcnow().strftime("%Y-%m-%d")

FRAGILITY_LOOKBACK = 52
SIGNAL_START = '2019-01-01'

ALL_TERMINALS = {
    "Kharg_Island": {
        "country": "Iran",
        "tanks": [
            {"d": 60, "h": 23, "lat": 29.221733, "lon": 50.316548},
            {"d": 60, "h": 23, "lat": 29.221638, "lon": 50.315043},
            {"d": 60, "h": 23, "lat": 29.221840, "lon": 50.313529},
            {"d": 60, "h": 23, "lat": 29.223156, "lon": 50.315849},
            {"d": 60, "h": 23, "lat": 29.222897, "lon": 50.314419},
            {"d": 55, "h": 23, "lat": 29.235969, "lon": 50.312620},
            {"d": 55, "h": 23, "lat": 29.236450, "lon": 50.310939},
            {"d": 55, "h": 23, "lat": 29.237953, "lon": 50.310849},
            {"d": 55, "h": 23, "lat": 29.239068, "lon": 50.309682},
            {"d": 55, "h": 23, "lat": 29.238926, "lon": 50.306814},
            {"d": 55, "h": 23, "lat": 29.237608, "lon": 50.307267},
            {"d": 55, "h": 23, "lat": 29.236277, "lon": 50.307724},
            {"d": 55, "h": 23, "lat": 29.236328, "lon": 50.309303},
            {"d": 55, "h": 23, "lat": 29.234972, "lon": 50.308186},
            {"d": 55, "h": 23, "lat": 29.235113, "lon": 50.309819},
            {"d": 55, "h": 23, "lat": 29.233643, "lon": 50.308638},
            {"d": 55, "h": 23, "lat": 29.233830, "lon": 50.310280},
            {"d": 55, "h": 23, "lat": 29.231440, "lon": 50.308740},
            {"d": 55, "h": 23, "lat": 29.231783, "lon": 50.310385},
            {"d": 55, "h": 23, "lat": 29.232076, "lon": 50.312234},
            {"d": 55, "h": 23, "lat": 29.230215, "lon": 50.310161},
            {"d": 55, "h": 23, "lat": 29.228871, "lon": 50.309966},
            {"d": 40, "h": 23, "lat": 29.229528, "lon": 50.312187},
            {"d": 40, "h": 23, "lat": 29.229763, "lon": 50.313501},
            {"d": 40, "h": 23, "lat": 29.229963, "lon": 50.314814},
            {"d": 40, "h": 23, "lat": 29.228423, "lon": 50.312441},
            {"d": 40, "h": 23, "lat": 29.228616, "lon": 50.313733},
            {"d": 40, "h": 23, "lat": 29.228811, "lon": 50.315019},
            {"d": 40, "h": 23, "lat": 29.227209, "lon": 50.312698},
            {"d": 40, "h": 23, "lat": 29.227435, "lon": 50.313993},
            {"d": 40, "h": 23, "lat": 29.227640, "lon": 50.315267},
        ]
    },
    "Ras_Tanura": {
        "country": "Saudi Arabia",
        "tanks": [
            {"d": 60, "h": 23, "lat": 26.638626, "lon": 50.154236},
            {"d": 60, "h": 23, "lat": 26.640430, "lon": 50.153296},
            {"d": 60, "h": 23, "lat": 26.641913, "lon": 50.152682},
            {"d": 60, "h": 23, "lat": 26.650024, "lon": 50.152807},
            {"d": 60, "h": 23, "lat": 26.652085, "lon": 50.149920},
            {"d": 60, "h": 23, "lat": 26.688442, "lon": 50.118714},
            {"d": 60, "h": 23, "lat": 26.687011, "lon": 50.117969},
            {"d": 60, "h": 23, "lat": 26.687548, "lon": 50.116596},
            {"d": 60, "h": 23, "lat": 26.689090, "lon": 50.117193},
            {"d": 60, "h": 23, "lat": 26.688215, "lon": 50.114879},
            {"d": 60, "h": 23, "lat": 26.689749, "lon": 50.115695},
            {"d": 35, "h": 20, "lat": 26.651657, "lon": 50.148320},
            {"d": 35, "h": 20, "lat": 26.649988, "lon": 50.148177},
            {"d": 35, "h": 20, "lat": 26.650865, "lon": 50.148946},
            {"d": 35, "h": 20, "lat": 26.650985, "lon": 50.150605},
            {"d": 35, "h": 20, "lat": 26.648982, "lon": 50.148984},
            {"d": 35, "h": 20, "lat": 26.649609, "lon": 50.149935},
            {"d": 35, "h": 20, "lat": 26.650144, "lon": 50.151086},
            {"d": 35, "h": 20, "lat": 26.648029, "lon": 50.149726},
            {"d": 35, "h": 20, "lat": 26.648818, "lon": 50.150545},
            {"d": 35, "h": 20, "lat": 26.649339, "lon": 50.151734},
            {"d": 35, "h": 20, "lat": 26.646997, "lon": 50.150259},
            {"d": 35, "h": 20, "lat": 26.647971, "lon": 50.151020},
            {"d": 35, "h": 20, "lat": 26.648509, "lon": 50.152502},
            {"d": 35, "h": 20, "lat": 26.648872, "lon": 50.153435},
            {"d": 35, "h": 20, "lat": 26.647181, "lon": 50.151415},
        ]
    },
    "Fujairah": {
        "country": "UAE",
        "tanks": [
            {"d": 60, "h": 23, "lat": 25.214715, "lon": 56.347140},
            {"d": 60, "h": 23, "lat": 25.214482, "lon": 56.349844},
            {"d": 60, "h": 23, "lat": 25.212250, "lon": 56.346868},
            {"d": 60, "h": 23, "lat": 25.212001, "lon": 56.349583},
            {"d": 60, "h": 23, "lat": 25.209462, "lon": 56.346572},
            {"d": 60, "h": 23, "lat": 25.209186, "lon": 56.349279},
            {"d": 60, "h": 23, "lat": 25.206718, "lon": 56.348999},
            {"d": 60, "h": 23, "lat": 25.206969, "lon": 56.346304},
            {"d": 35, "h": 20, "lat": 25.188396, "lon": 56.347077},
            {"d": 35, "h": 20, "lat": 25.188415, "lon": 56.348150},
            {"d": 35, "h": 20, "lat": 25.188410, "lon": 56.349228},
            {"d": 35, "h": 20, "lat": 25.187433, "lon": 56.347067},
            {"d": 35, "h": 20, "lat": 25.187434, "lon": 56.348152},
            {"d": 35, "h": 20, "lat": 25.187432, "lon": 56.349220},
        ]
    },
    "Mina_Ahmadi": {
        "country": "Kuwait",
        "tanks": [
            {"d": 30, "h": 18, "lat": 29.049582, "lon": 48.059250},
            {"d": 30, "h": 18, "lat": 29.048254, "lon": 48.058547},
            {"d": 30, "h": 18, "lat": 29.049275, "lon": 48.056029},
            {"d": 30, "h": 18, "lat": 29.050591, "lon": 48.056712},
            {"d": 50, "h": 20, "lat": 29.055651, "lon": 48.062169},
            {"d": 50, "h": 20, "lat": 29.056725, "lon": 48.059436},
            {"d": 50, "h": 20, "lat": 29.058623, "lon": 48.060438},
            {"d": 50, "h": 20, "lat": 29.057526, "lon": 48.063134},
            {"d": 50, "h": 20, "lat": 29.062873, "lon": 48.059721},
            {"d": 50, "h": 20, "lat": 29.063729, "lon": 48.057560},
            {"d": 50, "h": 20, "lat": 29.054845, "lon": 48.058486},
            {"d": 50, "h": 20, "lat": 29.053768, "lon": 48.061199},
            {"d": 50, "h": 20, "lat": 29.051889, "lon": 48.060216},
            {"d": 50, "h": 20, "lat": 29.052961, "lon": 48.057500},
        ]
    },
}


def calc_tank_capacity(d, h):
    return np.pi * (d/2)**2 * h * BARREL_PER_M3 / 1_000_000

CAPACITY_BY_COUNTRY = {}
for term_name, info in ALL_TERMINALS.items():
    country = info["country"]
    for t in info["tanks"]:
        cap = calc_tank_capacity(t["d"], t["h"])
        CAPACITY_BY_COUNTRY[country] = CAPACITY_BY_COUNTRY.get(country, 0) + cap
    for i, t in enumerate(info["tanks"]):
        t["id"] = f"{term_name}_{t['d']}m_{i}"

TOTAL_CAPACITY = sum(CAPACITY_BY_COUNTRY.values())
TOTAL_TANKS = sum(len(info["tanks"]) for info in ALL_TERMINALS.values())

EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""


def setup_sentinelhub():
    from sentinelhub import SHConfig
    config = SHConfig()
    config.sh_client_id = SH_CLIENT_ID
    config.sh_client_secret = SH_CLIENT_SECRET
    config.sh_token_url = CDSE_TOKEN_URL
    config.sh_base_url = CDSE_BASE_URL
    return config


def get_bbox(tanks, buffer_m=BUFFER_METERS):
    from sentinelhub import BBox, CRS
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min_lat)))
    return BBox([min_lon - dlon, min_lat - dlat, max_lon + dlon, max_lat + dlat], crs=CRS.WGS84)


def build_tank_masks(tanks, bbox, img_w, img_h):
    dx = np.sin(np.deg2rad(LOS_AZIMUTH))
    dy = -np.cos(np.deg2rad(LOS_AZIMUTH))
    Y_grid, X_grid = np.indices((img_h, img_w))
    result = []
    for tank in tanks:
        radius_m = tank["d"] / 2.0
        cx = (tank["lon"] - bbox.min_x) / (bbox.max_x - bbox.min_x) * img_w
        cy = (bbox.max_y - tank["lat"]) / (bbox.max_y - bbox.min_y) * img_h
        dist_sq = (X_grid - cx)**2 + (Y_grid - cy)**2
        dot = (X_grid - cx) * dx + (Y_grid - cy) * dy
        r_inner = max(0, radius_m - WIDTH_WIDE) / RESOLUTION
        crescent = (dist_sq <= (radius_m / RESOLUTION)**2) & (dist_sq >= r_inner**2) & (dot > 0)
        result.append({"id": tank["id"], "radius_m": radius_m, "h_max": tank["h"], "crescent": crescent})
    return result


def fetch_terminal(site_name, info, sh_config, cached_dates_for_site):
    from sentinelhub import SentinelHubRequest, SentinelHubCatalog, DataCollection, MimeType
    tanks = info["tanks"]
    country = info["country"]
    bbox = get_bbox(tanks)

    S1_CDSE = DataCollection.SENTINEL1_IW.define_from("s1iw_cdse", service_url=CDSE_BASE_URL)

    try:
        catalog = SentinelHubCatalog(config=sh_config)
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(START_DATE, END_DATE),
            filter="sat:orbit_state = 'descending'",
            fields={"include": ["properties.datetime"]}
        ))
    except Exception as e:
        print(f"  [{site_name}] Catalog search failed: {e}")
        return []

    all_dates = sorted({pd.to_datetime(s["properties"]["datetime"]).normalize() for s in scenes})
    new_dates = [d for d in all_dates if d.strftime("%Y-%m-%d") not in cached_dates_for_site]
    print(f"  [{site_name}] total={len(all_dates)} cached={len(cached_dates_for_site)} new={len(new_dates)}")

    if not new_dates:
        return []

    width_m = (bbox.max_x - bbox.min_x) * 111320 * np.cos(np.deg2rad(tanks[0]["lat"]))
    height_m = (bbox.max_y - bbox.min_y) * 111320
    img_w = max(1, int(width_m / RESOLUTION))
    img_h = max(1, int(height_m / RESOLUTION))
    masks = build_tank_masks(tanks, bbox, img_w, img_h)

    new_records = []
    for d in new_dates:
        d_str = d.strftime("%Y-%m-%d")
        req = SentinelHubRequest(
            evalscript=EVALSCRIPT,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(d_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                other_args={"processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN", "demInstance": "COPERNICUS_30"}}
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=(img_w, img_h), config=sh_config
        )
        try:
            data = req.get_data()
            if not data:
                continue
            img = data[0].squeeze()
            for mask in masks:
                pixels = img[mask["crescent"]]
                if pixels.size > 0:
                    db_mean = 10 * np.log10(np.clip(np.mean(pixels), 1e-10, None))
                    new_records.append({
                        "date": d_str,
                        "site": site_name,
                        "country": country,
                        "tank_id": mask["id"],
                        "radius_m": float(mask["radius_m"]),
                        "h_max": float(mask["h_max"]),
                        "db_mean": float(db_mean)
                    })
        except Exception as e:
            print(f"  [{site_name}] Skip {d_str}: {e}")

    return new_records


def calculate_volumes_per_terminal(df):
    df = df.copy()
    df['mn_bbl'] = 0.0
    for site in df['site'].unique():
        mask = df['site'] == site
        site_df = df[mask]
        dB_min = site_df['db_mean'].quantile(0.05)
        dB_max = site_df['db_mean'].quantile(0.95)
        def calc_barrels(row, dB_min=dB_min, dB_max=dB_max):
            vv = np.clip(row["db_mean"], dB_min, dB_max)
            fill_ratio = (dB_max - vv) / (dB_max - dB_min + 1e-6)
            h_current = TANK_HEIGHT_MIN + fill_ratio * (row["h_max"] - TANK_HEIGHT_MIN)
            return np.pi * row["radius_m"]**2 * h_current * BARREL_PER_M3 / 1_000_000
        df.loc[mask, 'mn_bbl'] = site_df.apply(calc_barrels, axis=1)
    return df


def aggregate_data(df, freq='W'):
    df = df.copy()
    daily = df.groupby(['date', 'country'])['mn_bbl'].sum().reset_index()
    daily['period'] = daily['date'].dt.to_period('W-MON' if freq == 'W' else 'M').dt.start_time
    period_data = daily.groupby(['period', 'country'])['mn_bbl'].mean().reset_index()
    period_data.columns = ['date', 'country', 'mn_bbl']
    pivot = period_data.pivot(index='date', columns='country', values='mn_bbl').reset_index()
    pivot = pivot.ffill().bfill()
    return pivot


def calculate_fragility(storage_series, lookback=FRAGILITY_LOOKBACK):
    def inv_pct_rank(x):
        if len(x) < 4:
            return 50.0
        return (1 - pd.Series(x).rank(pct=True).iloc[-1]) * 100
    fragility = storage_series.rolling(lookback, min_periods=4).apply(inv_pct_rank, raw=False)
    if fragility.isna().any():
        s_min, s_max = storage_series.min(), storage_series.max()
        simple = 100 * (1 - (storage_series - s_min) / (s_max - s_min + 0.001))
        fragility = fragility.fillna(simple)
    return fragility.round(1)


def load_price_data(freq='W'):
    try:
        import yfinance as yf
        brent = yf.download('BZ=F', start=SIGNAL_START, end=END_DATE, progress=False)['Close']
        brent = brent.ffill().dropna().reset_index()
        brent.columns = ['date', 'Brent']
        brent['date'] = pd.to_datetime(brent['date']).dt.tz_localize(None)
        brent['Dubai'] = brent['Brent'] * 0.975
        brent = brent.set_index('date')
        brent = brent.resample('W-MON' if freq == 'W' else 'MS').mean().reset_index()
        return brent[['date', 'Dubai']]
    except Exception as e:
        print(f"Warning: could not load prices: {e}")
        return None


def build_output(pivot_w, pivot_m, price_w, price_m):
    countries = [c for c in pivot_w.columns if c != 'date']

    def process_period(pivot, price_df):
        result = pivot.copy()
        result['total'] = result[countries].sum(axis=1)
        result['date'] = pd.to_datetime(result['date']).astype('datetime64[s]')
        if price_df is not None:
            price_df = price_df.copy()
            price_df['date'] = pd.to_datetime(price_df['date']).astype('datetime64[s]')
            result = pd.merge_asof(result.sort_values('date'), price_df.sort_values('date'), on='date', direction='nearest')
        result['utilization'] = (result['total'] / TOTAL_CAPACITY * 100).clip(0, 100)
        result['fragility'] = calculate_fragility(result['total'])
        storage_by_country, util_by_country = {}, {}
        for c in countries:
            cap = CAPACITY_BY_COUNTRY.get(c, 1)
            storage_by_country[c] = result[c].round(2).tolist()
            util_by_country[c] = (result[c] / cap * 100).clip(0, 100).round(1).tolist()
        return {
            "dates": result['date'].dt.strftime('%Y-%m-%d').tolist(),
            "storage_total": result['total'].round(2).tolist(),
            "utilization": result['utilization'].round(1).tolist(),
            "fragility": result['fragility'].tolist(),
            "dubai_prices": result['Dubai'].round(2).tolist() if 'Dubai' in result.columns else [],
            "storage_by_country": storage_by_country,
            "utilization_by_country": util_by_country,
        }

    return {
        "weekly": process_period(pivot_w, price_w),
        "monthly": process_period(pivot_m, price_m),
        "capacity": {"total": round(TOTAL_CAPACITY, 2), "by_country": {k: round(v, 2) for k, v in CAPACITY_BY_COUNTRY.items()}},
        "meta": {
            "updated": datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
            "tanks_count": TOTAL_TANKS,
            "terminals": list(ALL_TERMINALS.keys()),
            "countries": countries,
            "fragility_lookback_weeks": FRAGILITY_LOOKBACK,
            "start_date": START_DATE
        }
    }


def main():
    sh_config = setup_sentinelhub()

    cached_records = []
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r') as f:
            cached_records = json.load(f)
        print(f"Loaded {len(cached_records)} cached records")
    else:
        print("No cache found - full download will run")

    cached_dates = {}
    for r in cached_records:
        site = r.get("site", "Kharg_Island")
        cached_dates.setdefault(site, set()).add(r["date"])

    new_records = []
    for site_name, info in ALL_TERMINALS.items():
        site_cached = cached_dates.get(site_name, set())
        records = fetch_terminal(site_name, info, sh_config, site_cached)
        new_records.extend(records)
        print(f"  [{site_name}] fetched {len(records)} new records")

    if new_records:
        cached_records.extend(new_records)
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w') as f:
            json.dump(cached_records, f)
        print(f"Cache updated: {len(cached_records)} total records")

    if not cached_records:
        print("CRITICAL: No data available (empty cache and no new downloads)")
        sys.exit(1)

    df = pd.DataFrame(cached_records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.dropna(subset=['db_mean'])

    if 'site' not in df.columns:
        df['site'] = 'Kharg_Island'
    if 'country' not in df.columns:
        df['country'] = 'Iran'

    df = calculate_volumes_per_terminal(df)

    pivot_w = aggregate_data(df, freq='W')
    pivot_m = aggregate_data(df, freq='M')
    price_w = load_price_data(freq='W')
    price_m = load_price_data(freq='M')

    output = build_output(pivot_w, pivot_m, price_w, price_m)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Done. Weekly: {len(output['weekly']['dates'])} pts, latest: {output['weekly']['dates'][-1]}")


if __name__ == '__main__':
    main()
