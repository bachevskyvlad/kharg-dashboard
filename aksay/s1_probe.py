"""
aksay/s1_probe.py — rev3 Part B1 feasibility probe (GitHub Actions, network).
1. CDSE OData: all S1 IW SLC over Ak-Sai, Jul 20 – Sep 15, 2017–2026 → relative orbits, directions, dates.
   Per orbit & year: shortest pair inside August (6 d preferred, 12 d fallback).
2. Copernicus DEM GLO-30 (AWS open data) → layover / shadow over the frozen crop for every orbit,
   incidence estimated from the scene footprint. Output: aksay/data/s1_probe.json, aksay/out/s1_geometry.png
"""
import os, json, math, datetime as dt, io
import numpy as np, requests, tifffile
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
G = json.load(open(os.path.join(HERE, "aksay_geometry.json")))
TG = json.load(open(os.path.join(HERE, "aksay_tongue_polygon.json")))
LON, LAT = 74.535, 42.525
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DEM = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N42_00_E074_00_DEM/Copernicus_DSM_COG_10_N42_00_E074_00_DEM.tif"

def query(y):
    flt = ("Collection/Name eq 'SENTINEL-1' and "
           "Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/OData.CSC.StringAttribute/Value eq 'IW_SLC__1S') and "
           f"OData.CSC.Intersects(area=geography'SRID=4326;POINT({LON} {LAT})') and "
           f"ContentDate/Start gt {y}-07-20T00:00:00.000Z and ContentDate/Start lt {y}-09-15T00:00:00.000Z")
    out, url, params = [], ODATA, {"$filter": flt, "$expand": "Attributes", "$top": 200}
    while url:
        r = requests.get(url, params=params, timeout=120); r.raise_for_status(); j = r.json()
        for p in j["value"]:
            at = {a["Name"]: a["Value"] for a in p["Attributes"]}
            out.append({"name": p["Name"], "start": p["ContentDate"]["Start"], "relorb": at.get("relativeOrbitNumber"),
                        "dir": at.get("orbitDirection"), "platform": at.get("platformSerialIdentifier"),
                        "footprint": p.get("GeoFootprint", {}).get("coordinates"), "size_mb": round(p.get("ContentLength", 0) / 1e6)})
        url, params = j.get("@odata.nextLink"), None
    return out

def incidence(fp, direction):
    ring = np.array(fp[0] if isinstance(fp[0][0][0], (int, float)) else fp[0][0])
    # across-track position at our latitude: west→east fraction
    xs = []
    for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
        if (y0 - LAT) * (y1 - LAT) <= 0 and y0 != y1: xs.append(x0 + (LAT - y0) * (x1 - x0) / (y1 - y0))
    if len(xs) < 2: return None
    f = (LON - min(xs)) / (max(xs) - min(xs))
    f = f if direction == "ASCENDING" else 1 - f       # ascending looks east: near range = west edge
    return 30.9 + f * (46.0 - 30.9)

def main():
    res = {"scenes": [], "orbits": {}}
    for y in range(2017, 2027):
        try: res["scenes"] += query(y)
        except Exception as e: res.setdefault("errors", []).append(f"{y}: {e}")
    by = {}
    for s in res["scenes"]:
        by.setdefault((s["relorb"], s["dir"]), []).append(s)
    for (ro, d), ss in by.items():
        k = f"{d[:3]}_{ro}"; info = {"direction": d, "relorb": ro, "years": {}}
        incs = [incidence(s["footprint"], d) for s in ss if s["footprint"]]
        incs = [i for i in incs if i]; info["incidence_deg"] = round(float(np.median(incs)), 1) if incs else None
        info["size_mb_median"] = int(np.median([s["size_mb"] for s in ss]))
        for y in range(2017, 2027):
            ds = sorted({dt.date.fromisoformat(s["start"][:10]) for s in ss if s["start"].startswith(str(y))})
            aug = [x for x in ds if x.month == 8 or (x.month == 7 and x.day >= 26) or (x.month == 9 and x.day <= 6)]
            best = None
            for a in aug:
                for b in aug:
                    if b > a and (best is None or (b - a).days < best[2]): best = (str(a), str(b), (b - a).days)
            info["years"][y] = {"dates": [str(x) for x in ds], "best_pair": best,
                                "platforms": sorted({s["platform"] for s in ss if s["start"].startswith(str(y))})}
        res["orbits"][k] = info
    # ── DEM layover/shadow ─────────────────────────────────────────────────
    raw = requests.get(DEM, timeout=300).content
    z = tifffile.imread(io.BytesIO(raw)).astype(np.float32)          # 3600×3600, 1" grid, N42–43 E74–75
    n = z.shape[0]
    lon0, lat1 = 74.0, 43.0
    c = G["crop"]
    i0, i1 = int((lat1 - c[3]) * n), int((lat1 - c[1]) * n) + 1
    j0, j1 = int((c[0] - lon0) * n), int((c[2] - lon0) * n) + 1
    zz = z[i0:i1, j0:j1]
    dy = 111000 / n; dx = 111320 * math.cos(math.radians(LAT)) / n
    gy, gx = np.gradient(zz, dy, dx)                                  # gy: dz per m southward (row increases south)
    dzdE, dzdN = gx, -gy
    res["dem"] = {"source": DEM, "crop_rows_cols": [int(i1 - i0), int(j1 - j0)], "z_range": [float(zz.min()), float(zz.max())]}
    # tongue polygon mask on DEM grid
    def pm(poly):
        m = Image.new("L", (zz.shape[1], zz.shape[0]), 0)
        ImageDraw.Draw(m).polygon([((lo - c[0]) * n, (c[3] - la) * n) for lo, la in poly], fill=1); return np.array(m).astype(bool)
    tongue = pm(TG["polygon"])
    # lower debris-covered snout zone: crop rows north of the polygon top (down-glacier), x within polygon span ±300 m
    snout = np.zeros_like(tongue); top = np.nonzero(tongue.any(1))[0].min()
    cols = np.nonzero(tongue.any(0))[0]
    snout[max(0, top - int(1200 / dy)):top, max(0, cols.min() - 10):cols.max() + 10] = True
    panels = []
    for k, info in res["orbits"].items():
        th = math.radians(info["incidence_deg"] or 38.0)
        heading = -12.0 if info["direction"] == "ASCENDING" else 192.0
        look = math.radians(heading + 90.0)                           # right-looking
        ux, uy = math.sin(look), math.cos(look)                       # ground look vector (E, N), away from sensor
        s_r = dzdE * ux + dzdN * uy                                    # slope along look direction (+ = rising away from sensor)
        alpha = np.arctan(-s_r)                                        # + = slope facing the sensor
        layover = alpha > th
        shadow = (-alpha) > (math.pi / 2 - th)
        lia = np.degrees(th - alpha)                                   # local incidence angle
        info["tongue"] = {"layover_pct": round(100 * float(layover[tongue].mean()), 1), "shadow_pct": round(100 * float(shadow[tongue].mean()), 1),
                          "local_inc_median": round(float(np.median(lia[tongue])), 1)}
        info["snout_zone"] = {"layover_pct": round(100 * float(layover[snout].mean()), 1), "shadow_pct": round(100 * float(shadow[snout].mean()), 1),
                              "local_inc_median": round(float(np.median(lia[snout])), 1)}
        rgb = np.stack([np.clip((zz - zz.min()) / (zz.max() - zz.min()), 0, 1) * 200] * 3, -1)
        rgb[layover] = [230, 60, 60]; rgb[shadow] = [40, 40, 160]
        im = Image.fromarray(rgb.astype(np.uint8)).resize((zz.shape[1] * 3, zz.shape[0] * 3), Image.NEAREST)
        dr = ImageDraw.Draw(im)
        dr.polygon([((lo - c[0]) * n * 3, (c[3] - la) * n * 3) for lo, la in TG["polygon"]], outline=(255, 230, 0))
        dr.text((6, 6), f"{k} inc~{info['incidence_deg']}  red=layover blue=shadow", fill=(255, 255, 255))
        panels.append(im)
    if panels:
        W = sum(p.width for p in panels) + 10 * (len(panels) - 1); H = max(p.height for p in panels)
        out = Image.new("RGB", (W, H), (0, 0, 0)); x = 0
        for p in panels: out.paste(p, (x, 0)); x += p.width + 10
        os.makedirs(os.path.join(HERE, "out"), exist_ok=True); out.save(os.path.join(HERE, "out", "s1_geometry.png"))
    for s in res["scenes"]: s.pop("footprint", None)
    json.dump(res, open(os.path.join(HERE, "data", "s1_probe.json"), "w"), indent=1, default=str)
    for k, i in res["orbits"].items():
        print(k, i["incidence_deg"], i.get("tongue"), i.get("snout_zone"))
        for y, v in i["years"].items(): print("  ", y, v["best_pair"], v["platforms"])

if __name__ == "__main__":
    main()
