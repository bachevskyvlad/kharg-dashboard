"""
aksay/hugonnet.py — external check (rev3 A6): Hugonnet et al. 2021 per-glacier dh/dt for Ak-Sai.
Source: per-glacier rates (RGI6) as redistributed by OGGM; RGI6 centroids from OGGM rgi62_stats.
Runs in GitHub Actions (network). Output: aksay/data/hugonnet_aksay.json
"""
import os, json, io, math, requests
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "hugonnet_aksay.json")
LON, LAT = 74.535, 42.515          # Ak-Sai, mid-glacier (inside the frozen crop)
RATES = "https://cluster.klima.uni-bremen.de/~oggm/geodetic_ref_mb/hugonnet_2021_ds_rgi60_pergla_rates_10_20_worldwide.csv"
STATS = ["https://cluster.klima.uni-bremen.de/~oggm/rgi/rgi62_stats.h5",
         "https://cluster.klima.uni-bremen.de/~oggm/rgi/rgi62_stats.csv"]

def get(url):
    r = requests.get(url, timeout=300); r.raise_for_status(); return r.content

res = {"source_rates": RATES, "point": [LON, LAT]}
try:
    stats = None
    for u in STATS:
        try:
            b = get(u)
            if u.endswith(".h5"):
                open("/tmp/s.h5", "wb").write(b); stats = pd.read_hdf("/tmp/s.h5")
            else:
                stats = pd.read_csv(io.BytesIO(b))
            res["source_stats"] = u; break
        except Exception as e:
            res.setdefault("errors", []).append(f"{u}: {e}")
    if "RGIId" not in stats.columns: stats = stats.reset_index()
    stats = stats[stats["RGIId"].str.startswith("RGI60-13")]
    d = stats.apply(lambda r: math.hypot((r.CenLon - LON) * 82.1, (r.CenLat - LAT) * 111.0), axis=1)
    near = stats.assign(dist_km=d).nsmallest(8, "dist_km")
    res["nearest_rgi"] = near[["RGIId", "Name", "CenLon", "CenLat", "Area", "Zmin", "Zmax", "Lmax", "dist_km"]].to_dict("records") \
        if "Name" in near else near.to_dict("records")
    rates = pd.read_csv(io.BytesIO(get(RATES)))
    sel = rates[rates["rgiid"].isin(near["RGIId"])]
    res["rates"] = sel.to_dict("records")
except Exception as e:
    res.setdefault("errors", []).append(repr(e))
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str)[:4000])
