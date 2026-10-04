"""
aksay/h_pairs.py — rev6-final Part I (network, GitHub Actions): extra Landsat frames for sensor cross-calibration.
Same source and scoring as h_fetch.py (Planetary Computer landsat-c2-l2, Tier 1).
  I1: all Landsat-7 and Landsat-8 scenes, Aug 1 – Sep 30, 2013–2015.
  I2: all Landsat-8/9 scenes within ±10 days of each clean S2 frame (2017–2026).
Cloud (rev6-final, validated on the visually-QA'd main frames: 12 clear frames ≤ 2 % in the polygon, cloudy 2003/2004/2011
10–80 %): CFMask high-confidence cloud AND NDSI ≤ 0.4 AND blue SR > 0.2 (dirty ice is flagged by CFMask but is dark in blue).
A frame is kept if clean in the polygon: cloud_poly ≤ 5 %, snow (NDSI > 0.4, bbox) ≤ 1.25 × 26.2 %, valid_poly ≥ 0.50
(L7 SLC-off stripes are handled by the common-pixel comparison in rev6_final.py). Output: aksay/data/landsat_pairs/<date>_<platform>.npz + index.json
"""
import os, sys, json, datetime as dt
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import h_fetch as HF

OUT = os.path.join(HERE, "data", "landsat_pairs"); os.makedirs(OUT, exist_ok=True)
SNOW_MAX = 1.25 * 0.262; CLOUD_POLY_MAX = 0.05; VALID_POLY_MIN = 0.50

def score(item):
    qa = HF.read(item, "qa_pixel").astype(np.uint16)
    sr = lambda k: HF.read(item, k).astype(np.float32) * 2.75e-5 - 0.2
    g, sw, b = sr("green"), sr("swir16"), sr("blue"); nd = (g - sw) / (g + sw + 1e-6)
    valid = ((qa & 1) == 0) & (qa != 0)
    cloud = (((qa >> 3) & 1) == 1) & (((qa >> 8) & 3) == 3) & (nd <= 0.4) & (b > 0.2)
    if valid[HF.TP].mean() < VALID_POLY_MIN or valid.mean() < 0.3: return {"valid_poly": round(float(valid[HF.TP].mean()), 3)}
    return {"valid_poly": round(float(valid[HF.TP].mean()), 3), "cloud": float(cloud[valid].mean()),
            "cloud_poly": float(cloud[HF.TP & valid].mean()), "snow": float((nd > 0.4)[valid].mean())}

def scan(t0, t1, platforms, tag, idx):
    for f in HF.search(t0, t1):
        p = f["properties"]
        if p["platform"] not in platforms: continue
        key = f"{p['datetime'][:10]}_{p['platform']}"
        if key in idx: continue
        try: s = score(f)
        except Exception as e: print("score error", key, e); continue
        row = {"date": p["datetime"][:10], "platform": p["platform"], "purpose": tag, **{k: v for k, v in s.items()}}
        clean = "cloud" in s and s["cloud_poly"] <= CLOUD_POLY_MAX and s["snow"] <= SNOW_MAX and s["valid_poly"] >= VALID_POLY_MIN
        row["clean"] = bool(clean)
        if clean:
            arr = {k: HF.read(f, k) for k in HF.BANDS}
            arr["qa_pixel"] = np.where(arr["qa_pixel"] == 0, 1, arr["qa_pixel"])
            np.savez_compressed(os.path.join(OUT, key + ".npz"), **{k: v.astype(np.uint16) for k, v in arr.items()},
                                platform=p["platform"], date=row["date"])
        idx[key] = row; print(json.dumps(row, default=str), flush=True)

def main():
    HF.TP = HF.poly30(); idx = {}
    for y in (2013, 2014, 2015):
        scan(f"{y}-08-01", f"{y}-09-30", ("landsat-7", "landsat-8"), "I1", idx)
    cand = json.load(open(os.path.join(HERE, "data", "candidates.json")))
    flags = json.load(open(os.path.join(HERE, "aksay_flags.json")))["fresh_snow"]
    for y, v in cand["years"].items():
        if v is None or int(y) < 2017 or y in flags: continue
        d = dt.date.fromisoformat(v["pick"]["date"])
        if 100 * v["pick"]["snow"] > 1.25 * 23.1: continue                       # S2 auto snow flag (rev1 rule)
        scan(str(d - dt.timedelta(days=10)), str(d + dt.timedelta(days=10)), ("landsat-8", "landsat-9"), f"I2:{v['pick']['date']}", idx)
    json.dump(idx, open(os.path.join(OUT, "index.json"), "w"), indent=1, default=str)

if __name__ == "__main__":
    main()
