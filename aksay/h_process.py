"""
aksay/h_process.py — rev6 Part H (offline): Landsat 1990–2026 + Sentinel-2 2016–2026 on the frozen geometry.
Landsat frames (30 m, aksay/data/landsat/<year>.npz) are upsampled ×3 (nearest) onto the 10 m S2 fetch grid, so the
frozen polygon / centerline are used unchanged (≤ 1 px = 10 m shift at the far edge from the 741→740 trim).
Metrics per frame, inside the polygon (as fractions of valid, cloud-free polygon pixels × polygon area, so L7 SLC-off
gaps do not bias areas): bright (NDSI > 0.1), snow/ice split at NIR 0.60, debris; L_clean_edge on the centerline.
Outputs: aksay/out/h_landsat/ (combined CSV, fits JSON, chart, GIF 1990→2026, frames).
"""
import os, sys, json, csv
import numpy as np
from scipy import stats
from PIL import Image, ImageDraw
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = sys.argv[:1]
import process as P

LD = os.path.join(P.DATA, "landsat"); OUT = os.path.join(P.OUT, "h_landsat"); os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)
L_EDGE_MIN_M = 3000      # QA parameter: every S2 frame shows clean ice ≥ 3.3 km down the centerline; a 30 m run that
                         # breaks before 3.0 km is a mixed-pixel / stripe failure, recorded as 'edge not detected'
SENSOR = {"landsat-4": "L4", "landsat-5": "L5", "landsat-7": "L7", "landsat-8": "L8", "landsat-9": "L9"}
_FL = json.load(open(os.path.join(HERE, "aksay_flags.json")))
FLAGS_L = _FL.get("landsat_fresh_snow", {}); CLOUD_L = _FL.get("landsat_cloud", {})

def up(a):
    return np.repeat(np.repeat(a, 3, 0), 3, 1)[:P.H, :P.W]

def load_landsat(y):
    f = os.path.join(LD, f"{y}.npz")
    if not os.path.exists(f): return None
    z = np.load(f); qa = up(z["qa_pixel"].astype(np.uint16))
    sr = lambda k: np.clip(up(z[k]).astype(np.float32) * 0.275 - 2000.0, 0, 1e4)          # ×1e4 reflectance
    d = {"b02": sr("blue"), "b03": sr("green"), "b04": sr("red"), "b08": sr("nir08"), "b11": sr("swir16"),
         "mask": ((qa & 1) == 0).astype(np.uint8)}
    # same rule as frame selection (h_fetch.score): CFMask flags glacier ice as cloud here, so only
    # high-confidence cloud (bit 3, conf bits 8–9 = 3) that is NOT snow/ice-like (NDSI ≤ 0.4) counts as cloud
    nd = (d["b03"] - d["b11"]) / (d["b03"] + d["b11"] + 1e-6)
    # rev6-final: + blue SR > 0.2 — CFMask's high-confidence 'cloud' over dirty ice is dark in blue; validated on the
    # visually-QA'd frames (clear ≤ 2 % in polygon, cloudy 10–80 %). Without it dirty ice was dropped as cloud → bright area biased up.
    cloud = (((qa >> 3) & 1) == 1) & (((qa >> 8) & 3) == 3) & (nd <= 0.4) & (d["b02"] > 2000)
    return d, cloud, str(z["platform"]), str(z["date"])

def front_skip_invalid(m, valid, cl_m, cl_d):
    """As P.front_on_centerline (ice within ±40 m of the line, gaps ≤ 100 m bridged), but samples with no valid data
    (L7 SLC-off stripes, cloud) are unknown and skipped instead of counting as 'no ice'."""
    r = int(round(P.FRONT_BUF_M / P.PXW))
    ice = P.sample(P.dilate(m, r), cl_m); ok = P.sample(P.dilate(valid, 1), cl_m)
    d = cl_d[ok]; v = ice[ok]
    if d.size == 0 or not v[:max(1, int(300 / P.STEP_M))].any(): return None
    end, last_ice = None, None
    for di, vi in zip(d, v):
        if vi:
            if last_ice is not None and di - last_ice > P.FRONT_GAP_M: break
            end = last_ice = di
    return None if end is None else round(float(end), 1)

def metrics(d, cloud, TP, tg, cl_m, cl_d):
    ok = TP & (d["mask"] > 0) & ~cloud
    nd = P.ndsi(d); nir = d["b08"] / 1e4
    bright = (nd > tg["ndsi_threshold"]) & ok; snow = bright & (nir >= P.B08_SNOW)
    area = TP.sum() * P.PXW * P.PXH / 1e6; n = max(1, ok.sum())
    m = P.ice_mask(d) & ~cloud
    L = front_skip_invalid(m, (d["mask"] > 0) & ~cloud, cl_m, cl_d)
    L_raw = L
    if L is not None and L < L_EDGE_MIN_M: L = None
    return {"valid_poly_frac": round(float(ok.sum() / TP.sum()), 3),
            "bright_km2": round(float(bright.sum() / n * area), 3), "snow_km2": round(float(snow.sum() / n * area), 3),
            "ice_km2": round(float((bright & ~snow).sum() / n * area), 3), "debris_km2": round(float((ok & ~bright).sum() / n * area), 3),
            "L_clean_edge_m": L, "L_clean_edge_raw_m": L_raw}

def ols(rows, col, extra=None):
    rr = [r for r in rows if r[col] is not None]
    y = np.array([r[col] for r in rr], float); t = np.array([r["dec"] for r in rr]) - 2008
    X = [np.ones_like(t), t] + ([np.array([extra(r) for r in rr], float)] if extra else [])
    X = np.array(X).T; b, *_ = np.linalg.lstsq(X, y, rcond=None); df = len(y) - X.shape[1]
    s2 = ((y - X @ b) ** 2).sum() / df; se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X))); tt = b[1] / se[1]
    out = {"slope_per_yr": round(float(b[1]), 4), "sigma": round(float(se[1]), 4), "t": round(float(tt), 2), "df": int(df),
           "p": float(f"{2 * stats.t.sf(abs(tt), df):.2g}"), "n": len(y), "years": [r["year"] for r in rr]}
    if extra:
        out["sensor_offset"] = round(float(b[2]), 4); out["sensor_offset_sigma"] = round(float(se[2]), 4)
    return out

def main():
    g = json.load(open(P.GEOM)); tg = json.load(open(P.TONGUE)); TP = P.poly_mask(tg["polygon"])
    cl_m, cl_d = P.densify([P.to_m(*q) for q in g["centerline"]])
    cand = json.load(open(os.path.join(LD, "candidates.json")))
    rows = []
    for ys, v in sorted(cand["years"].items()):
        y = int(ys); L = load_landsat(y)
        if v is None or L is None: continue
        d, cloud, plat, date = L
        r = {"year": y, "sensor": SENSOR.get(plat, plat), "date": date, "window": v["window"],
             "snow_bbox_pct": round(100 * v["pick"]["snow"], 1), "cloud_bbox_pct": round(100 * v["pick"]["cloud"], 2),
             **metrics(d, cloud, TP, tg, cl_m, cl_d)}
        rows.append(r)
    med = float(np.median([r["snow_bbox_pct"] for r in rows if str(r["year"]) not in CLOUD_L]))
    for r in rows:
        r["flag"] = ("cloud (visual QA)" if str(r["year"]) in CLOUD_L else "manual snow" if str(r["year"]) in FLAGS_L
                     else ("auto snow" if r["snow_bbox_pct"] > P.SNOW_FLAG_X * med else ""))
        r["dec"] = r["year"] + (np.datetime64(r["date"]).astype("datetime64[D]") - np.datetime64(f"{r['year']}-01-01")).astype(int) / 365.25
    # S2 rows from the main pipeline (same definitions; S2 valid ≈ 1 so km² are comparable)
    s2 = []
    for r in csv.DictReader(open(os.path.join(P.OUT, "aksay_metrics.csv"))):
        y = int(r["year"])
        s2.append({"year": y, "sensor": "S2", "date": r["date"], "window": "aug-sep", "snow_bbox_pct": None, "cloud_bbox_pct": float(r["cloud_bbox_pct"]),
                   "valid_poly_frac": 1.0, "bright_km2": float(r["tongue_bright_km2"]), "snow_km2": float(r["tongue_snow_km2"]),
                   "ice_km2": float(r["tongue_ice_km2"]), "debris_km2": float(r["tongue_debris_km2"]),
                   "L_clean_edge_m": float(r["L_clean_edge_m"]) if r["L_clean_edge_m"] else None,
                   "flag": "flagged (S2 pipeline)" if r["snow_flag"] == "1" else "",
                   "dec": y + (np.datetime64(r["date"]) - np.datetime64(f"{y}-01-01")).astype(int) / 365.25})
    allrows = sorted(rows + s2, key=lambda r: (r["year"], r["sensor"] != "S2"))
    cols = ["year", "sensor", "date", "window", "valid_poly_frac", "bright_km2", "snow_km2", "ice_km2", "debris_km2", "L_clean_edge_m", "L_clean_edge_raw_m",
            "snow_bbox_pct", "cloud_bbox_pct", "flag"]
    with open(os.path.join(OUT, "aksay_landsat_s2_metrics.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(allrows)

    clean = lambda rs: [r for r in rs if not r["flag"]]
    L_hist = [r for r in clean(rows) if r["year"] <= 2015]
    L_all = clean(rows); S2c = clean(s2)
    res = {"landsat_snow_median_pct": med, "fits": {}, "calibration": {}}
    for col in ("bright_km2", "ice_km2", "debris_km2", "L_clean_edge_m"):
        res["fits"][col] = {"landsat_1990_2015": ols(L_hist, col), "landsat_all": ols(L_all, col), "s2_2018_2026": ols(S2c, col),
                            "combined_landsat_le2015_plus_s2": ols(L_hist + S2c, col),
                            "combined_with_sensor_offset": ols(L_hist + S2c, col, extra=lambda r: 1.0 if r["sensor"] == "S2" else 0.0)}
    # calibration: same year, both clean, Landsat 8/9 vs S2
    pairs = [(a, b) for a in L_all for b in S2c if a["year"] == b["year"] and a["sensor"] in ("L8", "L9")]
    for col in ("bright_km2", "ice_km2", "debris_km2", "L_clean_edge_m"):
        dif = [a[col] - b[col] for a, b in pairs if a[col] is not None and b[col] is not None]
        res["calibration"][col] = {"n": len(dif), "years": [a["year"] for a, b in pairs], "mean_L_minus_S2": round(float(np.mean(dif)), 3) if dif else None,
                                   "sd": round(float(np.std(dif, ddof=1)), 3) if len(dif) > 1 else None}
    json.dump(res, open(os.path.join(OUT, "h_fits.json"), "w"), indent=1)
    plot(allrows); gif(rows, s2, TP, tg, g)
    print(json.dumps(res["calibration"], indent=1))
    for c, v in res["fits"].items():
        for k, f in v.items(): print(c, k, f["slope_per_yr"], "±", f["sigma"], "p", f["p"], "n", f["n"], f.get("sensor_offset", ""))

def plot(allrows):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    surf, ink, mute, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    col = {"L5": "#2a78d6", "L7": "#2a78d6", "L4": "#2a78d6", "L8": "#1baf7a", "L9": "#1baf7a", "S2": "#eb6834"}
    lab = {"#2a78d6": "Landsat 5/7 (30 m)", "#1baf7a": "Landsat 8/9 (30 m)", "#eb6834": "Sentinel-2 (10 m)"}
    fig, axs = plt.subplots(2, 1, figsize=(9, 6.6), sharex=True, dpi=130); fig.patch.set_facecolor(surf)
    for ax, key, title, unit in ((axs[0], "bright_km2", "Bright (snow + bare ice, NDSI > 0.1) in the frozen tongue polygon", "km²"),
                                 (axs[1], "L_clean_edge_m", "Clean-ice edge along the centerline (not the terminus)", "m from top")):
        ax.set_facecolor(surf); seen = set()
        for r in allrows:
            if r[key] is None: continue
            c = col.get(r["sensor"], "#52514e"); flagged = bool(r["flag"])
            ax.scatter(r["dec"], r[key], s=40, color=surf if flagged else c, edgecolor=c, linewidth=1.8, zorder=3,
                       label=lab[c] if c not in seen else None); seen.add(c)
        ax.set_title(title, loc="left", fontsize=10, color=ink); ax.set_ylabel(unit, fontsize=9, color=mute)
        ax.grid(axis="y", color=grid, lw=0.8); ax.tick_params(colors=mute, labelsize=8.5)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"): ax.spines[sp].set_color(grid)
    axs[0].legend(frameon=False, fontsize=8.5, loc="upper right")
    axs[1].text(0, -0.2, "filled = clean frame · hollow = flagged (fresh snow or cloud) · Landsat areas = valid-pixel fractions × polygon area · Landsat edge on 30 m pixels",
                transform=axs[1].transAxes, fontsize=7.5, color=mute)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "h_metrics.png"), facecolor=surf)

def gif(rows, s2, TP, tg, g):
    sl = P.crop_slices(g); oy, ox = sl[0].start, sl[1].start
    lo, hi = json.load(open(os.path.join(P.OUT, "run_meta.json")))["stretch_p2_p98"]
    sw, sh = sl[1].stop - sl[1].start, sl[0].stop - sl[0].start
    Pp = lambda ll: (P.ll2px(*ll)[0] - ox, P.ll2px(*ll)[1] - oy)
    frames = []
    s2y = {r["year"] for r in s2}
    for r in [x for x in rows if x["year"] < min(s2y)] + [dict(x) for x in s2]:
        y = r["year"]
        if r["sensor"] == "S2":
            d = dict(np.load(os.path.join(P.DATA, "raw", f"{y}.npz")))
        else:
            d = load_landsat(y)[0]
        im = Image.fromarray(P.rgb(d, sl, lo, hi)).convert("RGBA"); dr = ImageDraw.Draw(im)
        dr.polygon([Pp(q) for q in tg["polygon"]], outline=(255, 255, 255))
        dr.line([Pp(q) for q in g["centerline"]], fill=(255, 214, 0), width=2)
        bar = 500 / P.PXW; fx0, fy0 = sw - 24 - bar, sh - 30
        dr.rectangle([fx0 - 10, fy0 - 26, sw - 14, sh - 12], fill=(0, 0, 0, 170)); dr.rectangle([fx0, fy0, fx0 + bar, fy0 + 6], fill=(255, 255, 255))
        dr.text((fx0 + bar / 2 - 22, fy0 - 22), "500 m", font=P.font(P.FONT_B, 15), fill=(255, 255, 255))
        sub = ("Sentinel-2 · 10 m" if r["sensor"] == "S2" else f"Landsat {r['sensor'][1]} · 30 m") + ("  ⚠ snow" if r["flag"] else "")
        P.year_badge(im, str(y), sub)
        im.convert("RGB").save(os.path.join(OUT, "frames", f"{y}.png")); frames.append(im)
    P.save_gif(frames, os.path.join(OUT, "aksay_timelapse_1990_2026.gif"), ms=900)

if __name__ == "__main__":
    main()
