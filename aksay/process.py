"""
aksay/process.py — Stages 0.4–4 of the Ak-Sai glacier timelapse (offline, no network).

Inputs : aksay/data/candidates.json, aksay/data/raw/<year>.npz   (from fetch.py)
         aksay/aksay_geometry.json  (FROZEN after first commit: crop, centerline, transects)
Outputs: aksay/out/frames/<year>.png, aksay/out/frames_ruler/<year>.png
         aksay/out/aksay_timelapse.gif, aksay/out/aksay_timelapse_ruler.gif
         aksay/out/aksay_metrics.csv, aksay/out/aksay_frames.csv, aksay/out/aksay_metrics.png
         aksay/AKSAY_BASELINE.md
"""
import os, json, csv, math
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT  = os.path.join(HERE, "out")
GEOM = os.path.join(HERE, "aksay_geometry.json")
MANUAL_FLAGS = json.load(open(os.path.join(HERE, "aksay_flags.json")))["fresh_snow"] if os.path.exists(os.path.join(HERE, "aksay_flags.json")) else {}
TONGUE = os.path.join(HERE, "aksay_tongue_polygon.json")

NDSI_T, NIR_T = 0.4, 0.11       # NDSI snow/ice threshold; NIR>0.11 rejects water (Hall et al.)
FRONT_GAP_M   = 100             # gaps in the ice run along the centerline tolerated up to this
FRONT_BUF_M   = 40              # ice counts on the centerline if present within ±this (debris bands split the tongue)
STEP_M        = 5               # sampling step along polylines
PX_ERR_M      = 10              # ±1 px
GAMMA         = 0.6             # one gamma for the whole series (applied after the common p2–p98 stretch)
B08_SNOW      = 0.60            # rev3: tongue bright pixels with B08 ≥ this = snow, else ice (sweep 0.50/0.60/0.70 in rev3.py)
SNOW_FLAG_X   = 1.25            # frame snow fraction > 1.25 × series median → fresh-snow flag

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

cand = json.load(open(os.path.join(DATA, "candidates.json")))
LON0, LAT0, LON1, LAT1 = cand["fetch_bbox"]
W, H = cand["size"]
LATC = 0.5 * (LAT0 + LAT1)
MX = 111320.0 * math.cos(math.radians(LATC))     # m per deg lon
MY = 110574.0                                    # m per deg lat
PXW = (LON1 - LON0) / W * MX                     # m per px (x)
PXH = (LAT1 - LAT0) / H * MY                     # m per px (y)

def ll2px(lon, lat):
    return ((lon - LON0) / (LON1 - LON0) * W - 0.5, (LAT1 - lat) / (LAT1 - LAT0) * H - 0.5)

def px2ll(x, y):
    return (LON0 + (x + 0.5) / W * (LON1 - LON0), LAT1 - (y + 0.5) / H * (LAT1 - LAT0))

def to_m(lon, lat):   # local metric plane (x east, y north)
    return np.array([(lon - LON0) * MX, (lat - LAT0) * MY])

def from_m(p):
    return (LON0 + p[0] / MX, LAT0 + p[1] / MY)

# ── load frames ──────────────────────────────────────────────────────────────
def load_years():
    ys = []
    for y, v in sorted(cand["years"].items()):
        f = os.path.join(DATA, "raw", f"{y}.npz")
        if v is None or not os.path.exists(f): continue
        ys.append((int(y), v, dict(np.load(f))))
    return ys

def crop_slices(g):
    x0, y0 = ll2px(g["crop"][0], g["crop"][3])
    x1, y1 = ll2px(g["crop"][2], g["crop"][1])
    return slice(int(round(y0)), int(round(y1))), slice(int(round(x0)), int(round(x1)))

def cloud_px(d, coll):
    return (d["scl"] == 255) if coll.endswith("l1c") else np.isin(d["scl"], [3, 8, 9, 10])

def ndsi(d):
    g, s = d["b03"] / 1e4, d["b11"] / 1e4
    return (g - s) / (g + s + 1e-6)

def poly_mask(poly_ll):
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).polygon([ll2px(lon, lat) for lon, lat in poly_ll], fill=1)
    return np.array(m).astype(bool)

def ice_mask(d):
    g, s, n = d["b03"] / 1e4, d["b11"] / 1e4, d["b08"] / 1e4
    ndsi = (g - s) / (g + s + 1e-6)
    return (ndsi > NDSI_T) & (n > NIR_T) & (d["mask"] > 0)

# ── geometry ─────────────────────────────────────────────────────────────────
def densify(pts_m, step=STEP_M):
    out, dist = [pts_m[0]], [0.0]
    for a, b in zip(pts_m[:-1], pts_m[1:]):
        L = np.linalg.norm(b - a); n = max(1, int(L // step))
        for i in range(1, n + 1):
            out.append(a + (b - a) * i / n); dist.append(dist[-1] + L / n)
    return np.array(out), np.array(dist)

def point_at(cl_m, cl_d, d):
    i = int(np.clip(np.searchsorted(cl_d, d), 1, len(cl_d) - 1))
    t = (d - cl_d[i - 1]) / max(1e-9, cl_d[i] - cl_d[i - 1])
    p = cl_m[i - 1] + (cl_m[i] - cl_m[i - 1]) * t
    tang = cl_m[i] - cl_m[i - 1]; tang /= np.linalg.norm(tang)
    return p, tang

def build_transects(g, cl_m, cl_d):
    tr = []
    for d in g["transect_dist_m"]:
        p, t = point_at(cl_m, cl_d, d)
        nrm = np.array([-t[1], t[0]]); h = g["transect_len_m"] / 2
        tr.append({"dist_m": d, "a": from_m(p - nrm * h), "b": from_m(p + nrm * h)})
    return tr

def sample(mask, pts_m):
    vals = []
    for p in pts_m:
        x, y = ll2px(*from_m(p)); xi, yi = int(round(x)), int(round(y))
        vals.append(bool(mask[yi, xi]) if 0 <= yi < H and 0 <= xi < W else False)
    return np.array(vals)

def runs_with_gaps(v, gap_n):
    """Boolean array → list of (i0, i1) runs, merging gaps ≤ gap_n samples."""
    idx = np.flatnonzero(v)
    if idx.size == 0: return []
    runs, s, e = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - e - 1 <= gap_n: e = i
        else: runs.append((s, e)); s = e = i
    runs.append((s, e)); return runs

def dilate(mask, r):
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out

def front_on_centerline(mask, cl_m, cl_d):
    v = sample(dilate(mask, int(round(FRONT_BUF_M / PXW))), cl_m)
    runs = runs_with_gaps(v, int(FRONT_GAP_M / STEP_M))
    if not runs or runs[0][0] * STEP_M > 300:  # ice must start near the top of the centerline
        return None, v
    return float(cl_d[runs[0][1]]), v

def width_on_transect(mask, a_ll, b_ll):
    a, b = to_m(*a_ll), to_m(*b_ll)
    pts, d = densify([a, b])
    return float(sample(mask, pts).sum() * STEP_M)   # total mask ∩ transect length

# ── rendering ────────────────────────────────────────────────────────────────
def common_stretch(frames, sl):
    pool = []
    for _, _, d in frames:
        m = d["mask"][sl] > 0
        for b in ("b04", "b03", "b02"):
            pool.append(d[b][sl][m][::7])
    pool = np.concatenate(pool).astype(np.float32)
    return float(np.percentile(pool, 2)), float(np.percentile(pool, 98))

def rgb(d, sl, lo, hi):
    a = np.stack([d["b04"][sl], d["b03"][sl], d["b02"][sl]], -1).astype(np.float32)
    return (np.clip((a - lo) / (hi - lo), 0, 1) ** GAMMA * 255).astype(np.uint8)

def font(p, s):
    try: return ImageFont.truetype(p, s)
    except Exception: return ImageFont.load_default()

def year_badge(img, text, sub=None):
    d = ImageDraw.Draw(img); f = font(FONT_B, 34); fs = font(FONT_R, 15)
    tw = d.textbbox((0, 0), text, font=f)[2]
    sw = d.textbbox((0, 0), sub, font=fs)[2] if sub else 0
    w = max(tw, sw) + 24; h = 54 + (22 if sub else 0)
    y0 = img.size[1] - 10 - h            # bottom-left: keeps the tongue (top of frame) clear
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    od.rounded_rectangle([10, y0, 10 + w, y0 + h], 8, fill=(0, 0, 0, 170))
    img.alpha_composite(ov)
    d.text((22, y0 + 6), text, font=f, fill=(255, 255, 255))
    if sub: d.text((22, y0 + 50), sub, font=fs, fill=(220, 220, 220))

def save_gif(paths_imgs, path, ms=1300):
    imgs = [im.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            for im in paths_imgs]
    durs = [ms] * len(imgs); durs[-1] = 2 * ms
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=durs, loop=0, optimize=False, disposal=1)

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    g = json.load(open(GEOM))
    frames = load_years()
    sl = crop_slices(g)
    oy, ox = sl[0].start, sl[1].start
    lo, hi = common_stretch(frames, sl)
    for sub in ("frames", "frames_ruler"): os.makedirs(os.path.join(OUT, sub), exist_ok=True)

    tg = json.load(open(TONGUE)); TP = poly_mask(tg["polygon"]); px_km2 = PXW * PXH / 1e6
    cl_m, cl_d = densify([to_m(*p) for p in g["centerline"]])
    trans = build_transects(g, cl_m, cl_d)

    def P(ll):  # lon/lat → crop pixel coords
        x, y = ll2px(*ll); return (x - ox, y - oy)

    rows, fr_rows, clean, ruler = [], [], [], []
    for y, meta, d in frames:
        pk, coll = meta["pick"], meta["collection"]
        m = ice_mask(d); cl = cloud_px(d, coll); m &= ~cl
        L, _ = front_on_centerline(m, cl_m, cl_d)
        Ws = [width_on_transect(m, t["a"], t["b"]) for t in trans]
        area = float(m[sl].sum() * px_km2)
        ok = TP & ~cl & (d["mask"] > 0)
        bright = (ndsi(d) > tg["ndsi_threshold"]) & ok
        snow = bright & (d["b08"] / 1e4 >= B08_SNOW)
        rows.append({"year": y, "date": pk["date"],
                     "tongue_bright_km2": round(float(bright.sum() * px_km2), 3),
                     "tongue_bright_pct": round(100 * float(bright.sum() / TP.sum()), 1),
                     "tongue_snow_km2": round(float(snow.sum() * px_km2), 3),
                     "tongue_ice_km2": round(float((bright & ~snow).sum() * px_km2), 3),
                     "tongue_debris_km2": round(float((ok & ~bright).sum() * px_km2), 3),
                     "L_clean_edge_m": None if L is None else round(L, 1),
                     **{f"W{i+1}_m": round(w, 1) for i, w in enumerate(Ws)},
                     "area_km2": round(area, 3), "err_m": PX_ERR_M,
                     "cloud_bbox_pct": round(100 * float(cl[sl].mean()), 2)})
        fr_rows.append({"year": y, "date": pk["date"], "collection": coll, "window": meta["window"],
                        "cloud_bbox_pct_fetch": round(100 * pk["cloud"], 2),
                        "snow_bbox_pct_fetch": round(100 * pk["snow"], 1)})

        base = Image.fromarray(rgb(d, sl, lo, hi)).convert("RGBA")
        im = base.copy(); year_badge(im, str(y))
        im.convert("RGB").save(os.path.join(OUT, "frames", f"{y}.png")); clean.append(im)

        im2 = base.copy(); dr = ImageDraw.Draw(im2)
        dr.polygon([P(q) for q in tg["polygon"]], outline=(255, 255, 255))
        dr.line([P(p) for p in g["centerline"]], fill=(255, 214, 0), width=2)
        for t in trans: dr.line([P(t["a"]), P(t["b"])], fill=(80, 200, 255), width=2)
        if L is not None:
            fx, fy = P(from_m(point_at(cl_m, cl_d, L)[0]))
            dr.ellipse([fx - 7, fy - 7, fx + 7, fy + 7], outline=(255, 40, 40), width=3)
            dr.ellipse([fx - 2, fy - 2, fx + 2, fy + 2], fill=(255, 40, 40))
        ruler.append((y, im2, L))

    med = float(np.median([f["snow_bbox_pct_fetch"] for f in fr_rows]))
    for r, f in zip(rows, fr_rows):
        f["snow_flag"] = r["snow_flag"] = int(f["snow_bbox_pct_fetch"] > SNOW_FLAG_X * med or str(r["year"]) in MANUAL_FLAGS)
        f["flag_reason"] = "manual (aksay_flags.json)" if str(r["year"]) in MANUAL_FLAGS else ("auto snow fraction" if f["snow_flag"] else "")
    # reference = first clean (not snow-flagged) year
    ref = next(r for r in rows if not r["snow_flag"] and r["L_clean_edge_m"] is not None)
    y0, L0, T0 = ref["year"], ref["L_clean_edge_m"], ref["tongue_ice_km2"]
    byyear = {r["year"]: r for r in rows}
    sw, sh = sl[1].stop - sl[1].start, sl[0].stop - sl[0].start
    bar_px = 500 / PXW
    for y, im2, L in ruler:
        t = byyear[y]["tongue_ice_km2"]
        sub = f"tongue bare ice: {t:.2f} km² ({100 * (t / T0 - 1):+.0f}% vs {y0})" + ("  ⚠snow" if byyear[y]["snow_flag"] else "")
        year_badge(im2, str(y), sub)
        dr = ImageDraw.Draw(im2); fx0, fy0 = sw - 24 - bar_px, sh - 30
        dr.rectangle([fx0 - 10, fy0 - 26, sw - 14, sh - 12], fill=(0, 0, 0, 170))
        dr.rectangle([fx0, fy0, fx0 + bar_px, fy0 + 6], fill=(255, 255, 255))
        dr.text((fx0 + bar_px / 2 - 22, fy0 - 22), "500 m", font=font(FONT_B, 15), fill=(255, 255, 255))
        im2.convert("RGB").save(os.path.join(OUT, "frames_ruler", f"{y}.png"))

    save_gif(clean, os.path.join(OUT, "aksay_timelapse.gif"))
    save_gif([r[1] for r in ruler], os.path.join(OUT, "aksay_timelapse_ruler.gif"))

    import rev3
    import sys
    fit = rev3.run(rows, sys.modules[__name__])
    with open(os.path.join(OUT, "aksay_metrics.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT, "aksay_frames.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fr_rows[0])); w.writeheader(); w.writerows(fr_rows)
    json.dump({"fit": fit, "snow_median_pct": med, "stretch_p2_p98": [lo, hi], "gamma": GAMMA, "px_m": [PXW, PXH], "transects": trans,
               "ref": {"year": y0, "L_clean_edge_m": L0, "tongue_ice_km2": T0}},
              open(os.path.join(OUT, "run_meta.json"), "w"), indent=1)
    print(f"stretch p2/p98 = {lo:.0f}/{hi:.0f}  px = {PXW:.2f}×{PXH:.2f} m")
    for r in rows: print(r)

if __name__ == "__main__":
    main()
