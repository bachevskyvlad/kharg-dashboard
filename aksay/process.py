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

NDSI_T, NIR_T = 0.4, 0.11       # NDSI snow/ice threshold; NIR>0.11 rejects water (Hall et al.)
FRONT_GAP_M   = 100             # gaps in the ice run along the centerline tolerated up to this
FRONT_BUF_M   = 40              # ice counts on the centerline if present within ±this (debris bands split the tongue)
STEP_M        = 5               # sampling step along polylines
PX_ERR_M      = 10              # ±1 px
GAMMA         = 0.6             # one gamma for the whole series (applied after the common p2–p98 stretch)
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

# ── analysis ─────────────────────────────────────────────────────────────────
def linfit(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    (b, a), cov = np.polyfit(x, y, 1, cov=True)
    return float(b), float(np.sqrt(cov[0, 0])), float(a)

def analyse(rows, y0, L0):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    def dec(r):  # decimal year of the acquisition
        m, d = map(int, r["date"][5:].split("-")); return r["year"] + (m - 1) / 12 + (d - 1) / 365
    fit = {}
    for key, sel in (("clean", lambda r: not r["snow_flag"]), ("all", lambda r: True)):
        rr = [r for r in rows if sel(r) and r["L_front_m"] is not None]
        b, eb, _ = linfit([dec(r) for r in rr], [r["L_front_m"] for r in rr])
        ba, eba, _ = linfit([dec(r) for r in rr], [r["area_km2"] for r in rr])
        fit[key] = {"n": len(rr), "years": [r["year"] for r in rr],
                    "front_m_per_yr": round(b, 1), "front_se": round(eb, 1),
                    "area_km2_per_yr": round(ba, 3), "area_se": round(eba, 3)}
    ink, mute, grid, c1 = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"
    fig, axs = plt.subplots(2, 1, figsize=(8, 6.4), sharex=True, dpi=130)
    fig.patch.set_facecolor("#fcfcfb")
    for ax, key, lab, unit in ((axs[0], "L_front_m", "Front position along centerline", "m from top"),
                               (axs[1], "area_km2", "NDSI snow/ice area in frame", "km²")):
        ax.set_facecolor("#fcfcfb")
        xs = [dec(r) for r in rows if r[key] is not None]; ys = [r[key] for r in rows if r[key] is not None]
        ax.plot(xs, ys, color=c1, lw=2, zorder=2)
        for r in rows:
            if r[key] is None: continue
            ax.scatter(dec(r), r[key], s=46, zorder=3, color="#fcfcfb" if r["snow_flag"] else c1,
                       edgecolor=c1, linewidth=2)
        sub = [r for r in rows if not r["snow_flag"] and r[key] is not None]
        b, eb, a = linfit([dec(r) for r in sub], [r[key] for r in sub])
        xx = np.array([min(xs), max(xs)])
        ax.plot(xx, a + b * xx, color=mute, lw=1.2, ls="--", zorder=1)
        ax.set_title(f"{lab}  ·  fit (clean years): {b:+.{1 if key[0]=='L' else 3}f} ± {eb:.{1 if key[0]=='L' else 3}f} {unit.split()[0]}/yr",
                     loc="left", fontsize=10.5, color=ink)
        ax.set_ylabel(unit, color=mute, fontsize=9)
        ax.grid(axis="y", color=grid, lw=0.8); ax.tick_params(colors=mute, labelsize=8.5)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"): ax.spines[sp].set_color(grid)
    axs[1].text(0.0, -0.22, "Filled = clean frame · hollow = fresh-snow flag (excluded from fit) · dashed = linear fit · ±10 m (1 px) per point",
                transform=axs[1].transAxes, fontsize=8, color=mute)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "aksay_metrics.png"), facecolor=fig.get_facecolor())
    return fit

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    g = json.load(open(GEOM))
    frames = load_years()
    sl = crop_slices(g)
    oy, ox = sl[0].start, sl[1].start
    lo, hi = common_stretch(frames, sl)
    for sub in ("frames", "frames_ruler"): os.makedirs(os.path.join(OUT, sub), exist_ok=True)

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
        area = float(m[sl].sum() * PXW * PXH / 1e6)
        rows.append({"year": y, "date": pk["date"], "L_front_m": None if L is None else round(L, 1),
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
        dr.line([P(p) for p in g["centerline"]], fill=(255, 214, 0), width=2)
        for t in trans: dr.line([P(t["a"]), P(t["b"])], fill=(80, 200, 255), width=2)
        if L is not None:
            fx, fy = P(from_m(point_at(cl_m, cl_d, L)[0]))
            dr.ellipse([fx - 7, fy - 7, fx + 7, fy + 7], outline=(255, 40, 40), width=3)
            dr.ellipse([fx - 2, fy - 2, fx + 2, fy + 2], fill=(255, 40, 40))
        ruler.append((y, im2, L))

    # cumulative retreat vs first valid year (front moves up-glacier → L decreases)
    L0 = next((L for _, _, L in ruler if L is not None), None)
    y0 = next((y for y, _, L in ruler if L is not None), None)
    sw, sh = sl[1].stop - sl[1].start, sl[0].stop - sl[0].start
    bar_px = 500 / PXW
    for y, im2, L in ruler:
        sub = f"front: {L - L0:+.0f} m since {y0}" if L is not None else "front: n/a (masked)"
        year_badge(im2, str(y), sub)
        dr = ImageDraw.Draw(im2); fx0, fy0 = sw - 24 - bar_px, sh - 30
        dr.rectangle([fx0 - 10, fy0 - 26, sw - 14, sh - 12], fill=(0, 0, 0, 170))
        dr.rectangle([fx0, fy0, fx0 + bar_px, fy0 + 6], fill=(255, 255, 255))
        dr.text((fx0 + bar_px / 2 - 22, fy0 - 22), "500 m", font=font(FONT_B, 15), fill=(255, 255, 255))
        im2.convert("RGB").save(os.path.join(OUT, "frames_ruler", f"{y}.png"))

    save_gif(clean, os.path.join(OUT, "aksay_timelapse.gif"))
    save_gif([r[1] for r in ruler], os.path.join(OUT, "aksay_timelapse_ruler.gif"))

    med = float(np.median([f["snow_bbox_pct_fetch"] for f in fr_rows]))
    for r, f in zip(rows, fr_rows):
        f["snow_flag"] = r["snow_flag"] = int(f["snow_bbox_pct_fetch"] > SNOW_FLAG_X * med)
    fit = analyse(rows, y0, L0)
    with open(os.path.join(OUT, "aksay_metrics.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT, "aksay_frames.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fr_rows[0])); w.writeheader(); w.writerows(fr_rows)
    json.dump({"fit": fit, "snow_median_pct": med, "stretch_p2_p98": [lo, hi], "gamma": GAMMA, "px_m": [PXW, PXH], "transects": trans,
               "front_ref": {"year": y0, "L_m": L0}},
              open(os.path.join(OUT, "run_meta.json"), "w"), indent=1)
    print(f"stretch p2/p98 = {lo:.0f}/{hi:.0f}  px = {PXW:.2f}×{PXH:.2f} m")
    for r in rows: print(r)

if __name__ == "__main__":
    main()
