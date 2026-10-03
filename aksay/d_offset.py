"""
aksay/d_offset.py — rev4 Part D: optical offset tracking (S2 B08, 10 m) on the Ak-Sai tongue + debris lobe.

Method (all numbers are parameters, see PARAMS):
  NCC template matching, 32×32 px windows, 8 px step, ±16 px search; parabolic sub-pixel peak;
  reject NCC < 0.5 or peak / 2nd-peak < 1.3; 3×3 nan-median filter on dx, dy.
  ROI: ±400 m around the frozen centerline, from mid-polygon down to 1 km below the polygon's down-glacier end
  (the last centerline segment is extrapolated for the ROI only; metrics use the frozen centerline).
  Co-registration check on stable valley-wall rock; L_moving_opt_m = down-glacier end of the connected
  |d| > 2 px run along the centerline (gaps ≤ 100 m bridged).
Usage: python aksay/d_offset.py 2017 2026 [2019 2026] [2025 2026] ...
Outputs: aksay/out/offset/<A>_<B>_field.npz, _map.png, _profile.png, offset_summary.json
"""
import os, sys, json
import numpy as np
from scipy import ndimage
from skimage.feature import match_template
from PIL import Image, ImageDraw, ImageFont

sys.argv, ARGS = sys.argv[:1], sys.argv[1:]
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import process as P                                     # frozen grid, geometry helpers, raw loader

PARAMS = {"win": 32, "step": 8, "search": 16, "ncc_min": 0.5, "peak_ratio_min": 1.3,
          "roi_half_width_m": 400, "roi_below_polygon_m": 1000, "moving_px": 2.0, "gap_m": 100,
          "stable_min_dist_m": 500, "coreg_tol_px": 1.0}
PARAMS.update(json.loads(os.environ.get("AKSAY_D_PARAMS", "{}")))     # sensitivity runs: override parameters
TAG = os.environ.get("AKSAY_D_TAG", "")
OUT = os.path.join(HERE, "out", "offset" + (f"_{TAG}" if TAG else "")); os.makedirs(OUT, exist_ok=True)
FONT = P.FONT_R

def load(y):
    return dict(np.load(os.path.join(P.DATA, "raw", f"{y}.npz")))

def date_of(y):
    return json.load(open(os.path.join(P.DATA, "candidates.json")))["years"][str(y)]["pick"]["date"]

def centerline():
    g = json.load(open(P.GEOM))
    return P.densify([P.to_m(*q) for q in g["centerline"]])

def s_n_grid(cl_m, cl_d, ext_m):
    """Along-centerline s and lateral n for every fetch-grid pixel (centerline extended by ext_m for the ROI)."""
    t = cl_m[-1] - cl_m[-2]; t /= np.linalg.norm(t)
    ext = np.array([cl_m[-1] + t * k for k in np.arange(5, ext_m + 5, 5)])
    cm = np.vstack([cl_m, ext]); cd = np.concatenate([cl_d, cl_d[-1] + np.arange(5, ext_m + 5, 5)])
    yy, xx = np.mgrid[0:P.H, 0:P.W]
    lon = P.LON0 + (xx + 0.5) / P.W * (P.LON1 - P.LON0); lat = P.LAT1 - (yy + 0.5) / P.H * (P.LAT1 - P.LAT0)
    pts = np.stack([(lon - P.LON0) * P.MX, (lat - P.LAT0) * P.MY], -1).reshape(-1, 2)
    S = np.empty(len(pts)); N = np.empty(len(pts)); D = np.empty(len(pts))
    for i in range(0, len(pts), 20000):
        p = pts[i:i + 20000]
        d2 = ((p[:, None, :] - cm[None, ::2, :]) ** 2).sum(-1); j = np.clip(d2.argmin(1) * 2, 1, len(cm) - 1)
        tg = cm[j] - cm[j - 1]; tg /= np.linalg.norm(tg, axis=1)[:, None]; r = p - cm[j]
        S[i:i + 20000] = cd[j]; N[i:i + 20000] = tg[:, 0] * r[:, 1] - tg[:, 1] * r[:, 0]; D[i:i + 20000] = np.sqrt(d2.min(1))
    return S.reshape(P.H, P.W), N.reshape(P.H, P.W), D.reshape(P.H, P.W)

def subpix(c, i):
    if 0 < i < len(c) - 1:
        den = c[i - 1] - 2 * c[i] + c[i + 1]
        if den < 0: return i + 0.5 * (c[i - 1] - c[i + 1]) / den
    return float(i)

def track(a, b, centers):
    w, s, h = PARAMS["win"], PARAMS["search"], PARAMS["win"] // 2
    res = []
    for (yc, xc) in centers:
        tpl = a[yc - h:yc + h, xc - h:xc + h]; srch = b[yc - h - s:yc + h + s, xc - h - s:xc + h + s]
        if tpl.shape != (w, w) or srch.shape != (w + 2 * s, w + 2 * s) or tpl.std() < 1e-6:
            res.append((np.nan, np.nan, np.nan, np.nan)); continue
        c = match_template(srch, tpl)                     # (2s+1)²: NCC for shifts −s..+s
        iy, ix = np.unravel_index(np.argmax(c), c.shape); pk = c[iy, ix]
        c2 = c.copy(); c2[max(0, iy - 2):iy + 3, max(0, ix - 2):ix + 3] = -np.inf
        second = c2.max()
        ratio = pk / second if second > 0 else np.inf
        dy = subpix(c[:, ix], iy) - s; dx = subpix(c[iy, :], ix) - s
        ok = pk >= PARAMS["ncc_min"] and ratio >= PARAMS["peak_ratio_min"] and abs(dx) < s and abs(dy) < s
        res.append((dx if ok else np.nan, dy if ok else np.nan, pk, ratio))
    return np.array(res)

def nanmed3(g):
    return ndimage.generic_filter(g, lambda v: np.nan if np.isnan(v).all() else np.nanmedian(v), size=3, mode="nearest")

def run_pair(ya, yb, geo):
    S, N, D, cl_m, cl_d, smid, send, TP = geo
    A, B = load(ya), load(yb)
    a, b = A["b08"].astype(np.float32) / 1e4, B["b08"].astype(np.float32) / 1e4
    step, h, s = PARAMS["step"], PARAMS["win"] // 2, PARAMS["search"]
    ys = np.arange(h + s, P.H - h - s, step); xs = np.arange(h + s, P.W - h - s, step)
    GY, GX = np.meshgrid(ys, xs, indexing="ij")
    roi = (np.abs(N[GY, GX]) <= PARAMS["roi_half_width_m"]) & (S[GY, GX] >= smid) & (S[GY, GX] <= send)
    # stable rock: far from the glacier axis, NDSI < 0 in both frames over the whole window
    rockA, rockB = P.ndsi(A) < 0, P.ndsi(B) < 0
    rock_frac = ndimage.uniform_filter(((rockA & rockB) * 1.0), size=PARAMS["win"])
    stable = (D[GY, GX] > PARAMS["stable_min_dist_m"]) & (rock_frac[GY, GX] > 0.95)
    crop = P.crop_slices(json.load(open(P.GEOM)))
    stable &= (GY >= crop[0].start) & (GY < crop[0].stop) & (GX >= crop[1].start) & (GX < crop[1].stop)

    out = {}
    for name, m in (("stable", stable), ("roi", roi)):
        pts = list(zip(GY[m], GX[m])); r = track(a, b, pts)
        out[name] = (m, r)
    # co-registration
    rs = out["stable"][1]; v = ~np.isnan(rs[:, 0])
    med_dx, med_dy = (float(np.median(rs[v, 0])), float(np.median(rs[v, 1]))) if v.any() else (0.0, 0.0)
    med_mag = float(np.median(np.hypot(rs[v, 0], rs[v, 1]))) if v.any() else np.nan
    corr = abs(complex(med_dx, med_dy)) >= PARAMS["coreg_tol_px"] or med_mag >= PARAMS["coreg_tol_px"]
    coreg = {"stable_windows": int(stable.sum()), "stable_valid": int(v.sum()), "median_dx_px": round(med_dx, 2),
             "median_dy_px": round(med_dy, 2), "median_abs_d_px": round(med_mag, 2), "shift_applied": bool(corr)}
    # assemble ROI field on the window grid
    m, r = out["roi"]
    FX = np.full(GY.shape, np.nan); FY = FX.copy(); NC = FX.copy(); PR = FX.copy()
    FX[m], FY[m], NC[m], PR[m] = r[:, 0], r[:, 1], r[:, 2], r[:, 3]
    if corr: FX -= med_dx; FY -= med_dy
    FX, FY = nanmed3(FX), nanmed3(FY)
    FX[~m] = np.nan; FY[~m] = np.nan
    MAG = np.hypot(FX, FY)
    np.savez_compressed(os.path.join(OUT, f"{ya}_{yb}_field.npz"), gy=GY, gx=GX, dx=FX, dy=FY, mag=MAG, ncc=NC, peak_ratio=PR,
                        roi=m, stable=stable)
    # profile along the frozen centerline (nearest window centre, ≤ step/2 away)
    prof = []
    for p, dist in zip(cl_m, cl_d):
        x, y = P.ll2px(*P.from_m(p)); i = int(round((y - ys[0]) / step)); j = int(round((x - xs[0]) / step))
        val = MAG[i, j] if 0 <= i < GY.shape[0] and 0 <= j < GY.shape[1] else np.nan
        prof.append((dist, val))
    prof = np.array(prof)
    L, run = moving_end(prof, smid)
    sweep = {f"{t:g}px": moving_end(prof, smid, t)[0] for t in (0.5, 1.0, 2.0)}
    sweep_skip = {f"{t:g}px": moving_end_skipnan(prof, smid, t) for t in (0.5, 1.0, 2.0)}
    years = (np.datetime64(date_of(yb)) - np.datetime64(date_of(ya))).astype(int) / 365.25
    vin = m & ~np.isnan(MAG)
    summ = {"pair": [ya, yb], "dates": [date_of(ya), date_of(yb)], "years": round(years, 2), "coreg": coreg,
            "roi_windows": int(m.sum()), "roi_valid": int(vin.sum()), "valid_frac": round(float(vin.sum() / max(1, m.sum())), 3),
            "L_moving_opt_m": L, "moving_run_start_m": run, "L_moving_opt_sweep_m": sweep, "L_moving_opt_sweep_nanskip_m": sweep_skip,
            "moving_px_per_yr_equiv": round(PARAMS["moving_px"] / years, 2),
            "tongue_poly_median_d_px": med_in(MAG, GY, GX, TP), "median_ncc_roi": round(float(np.nanmedian(NC[m])), 3)}
    draw_map(ya, yb, B, GY, GX, MAG, m, L, cl_m, cl_d, summ)
    return summ, prof

def med_in(MAG, GY, GX, TP):
    v = MAG[TP[GY, GX]]; v = v[~np.isnan(v)]
    return round(float(np.median(v)), 2) if v.size else None

def moving_end(prof, smid, thr=None):
    thr = PARAMS["moving_px"] if thr is None else thr
    d, v = prof[:, 0], prof[:, 1]
    sel = d >= smid; d, v = d[sel], v[sel]
    first = np.flatnonzero(~np.isnan(v))                # start = first valid match below mid-polygon
    if first.size == 0: return None, None
    d, v = d[first[0]:], v[first[0]:]
    mov = np.nan_to_num(v, nan=0) > thr
    gap = int(PARAMS["gap_m"] / P.STEP_M)
    idx = np.flatnonzero(mov)
    if idx.size == 0: return None, None
    runs, s0, e0 = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - e0 - 1 <= gap: e0 = i
        else: runs.append((s0, e0)); s0 = e0 = i
    runs.append((s0, e0))
    s0, e0 = runs[0]                                   # run connected to the up-glacier start of the ROI
    if d[s0] - d[0] > PARAMS["gap_m"]: return None, round(float(d[s0]), 1)
    return round(float(d[e0]), 1), round(float(d[s0]), 1)

def moving_end_skipnan(prof, smid, thr):
    """Variant: rejected matches (NaN) are unknown, not 'stagnant'; the run breaks only on > gap_m of valid
    samples below threshold."""
    d, v = prof[:, 0], prof[:, 1]
    ok = (d >= smid) & ~np.isnan(v); d, v = d[ok], v[ok]
    if d.size == 0 or v[0] <= thr: return None
    end, still = d[0], None
    for di, vi in zip(d, v):
        if vi > thr: end, still = di, None
        else:
            still = di if still is None else still
            if di - still > PARAMS["gap_m"]: break
    return round(float(end), 1)

def draw_map(ya, yb, B, GY, GX, MAG, m, L, cl_m, cl_d, summ):
    g = json.load(open(P.GEOM)); tg = json.load(open(P.TONGUE))
    ys, xs = GY[m], GX[m]; y0, y1 = max(0, ys.min() - 30), min(P.H, ys.max() + 30); x0, x1 = max(0, xs.min() - 30), min(P.W, xs.max() + 30)
    base = (np.clip((B["b04"] + B["b03"] + B["b02"]) / 3 / 6000, 0, 1) ** 0.7 * 255)[y0:y1, x0:x1]
    im = np.stack([base] * 3, -1) * 0.6
    vmax = max(6.0, float(np.nanpercentile(MAG[m], 95)) if np.isfinite(MAG[m]).any() else 6.0)
    ramp = np.array([[205, 226, 251], [134, 182, 239], [57, 135, 229], [28, 92, 171], [13, 54, 107]], float)  # one-hue sequential
    step = PARAMS["step"]
    for (yy, xx), v in zip(zip(GY[m], GX[m]), MAG[m]):
        if np.isnan(v): continue
        t = min(1.0, v / vmax) * (len(ramp) - 1); k = int(min(len(ramp) - 2, t)); c = ramp[k] + (ramp[k + 1] - ramp[k]) * (t - k)
        im[yy - y0 - step // 2:yy - y0 + step // 2, xx - x0 - step // 2:xx - x0 + step // 2] = c
    K = 3
    I = Image.fromarray(im.astype(np.uint8)).resize(((x1 - x0) * K, (y1 - y0) * K), Image.NEAREST); dr = ImageDraw.Draw(I)
    Q = lambda ll: ((P.ll2px(*ll)[0] - x0 + .5) * K, (P.ll2px(*ll)[1] - y0 + .5) * K)
    dr.polygon([Q(q) for q in tg["polygon"]], outline=(255, 255, 255))
    dr.line([Q(q) for q in g["centerline"]], fill=(255, 214, 0), width=2)
    if L is not None:
        fx, fy = Q(P.from_m(P.point_at(cl_m, cl_d, L)[0])); dr.ellipse([fx - 9, fy - 9, fx + 9, fy + 9], outline=(227, 73, 72), width=3)
    W = I.width + 330; can = Image.new("RGB", (W, max(I.height, 420)), (252, 252, 251)); can.paste(I, (0, 0)); dr = ImageDraw.Draw(can)
    fb, fr = ImageFont.truetype(P.FONT_B, 16), ImageFont.truetype(FONT, 13)
    dr.text((I.width + 14, 12), f"S2 B08 offset {ya} → {yb}", font=fb, fill=(11, 11, 11))
    for i in range(len(ramp)):
        dr.rectangle([I.width + 14 + i * 40, 44, I.width + 52 + i * 40, 60], fill=tuple(int(c) for c in ramp[i]))
    dr.text((I.width + 14, 64), f"|d| 0 → {vmax:.0f}+ px  (1 px = 10 m)", font=fr, fill=(82, 81, 78))
    c = summ["coreg"]
    txt = [f"period: {summ['dates'][0]} → {summ['dates'][1]} ({summ['years']} yr)",
           f"valid windows: {summ['roi_valid']}/{summ['roi_windows']}",
           f"stable rock median d: ({c['median_dx_px']}, {c['median_dy_px']}) px",
           f"stable rock median |d|: {c['median_abs_d_px']} px" + (" → shift removed" if c["shift_applied"] else ""),
           f"L_moving_opt: {summ['L_moving_opt_m']} m" if summ["L_moving_opt_m"] is not None else "L_moving_opt: none (no connected |d|>2 px)",
           "", "yellow: centerline (frozen v1)", "white: tongue polygon (frozen v1)", "red ring: L_moving_opt", "grey: no valid match"]
    for i, t in enumerate(txt): dr.text((I.width + 14, 94 + 20 * i), t, font=fr, fill=(11, 11, 11) if i < 5 else (82, 81, 78))
    can.save(os.path.join(OUT, f"{ya}_{yb}_map.png"))

def plot_profiles(profiles, summs, edge):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    cols = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    fig, ax = plt.subplots(figsize=(8.4, 4.2), dpi=130); surf = "#fcfcfb"; fig.patch.set_facecolor(surf); ax.set_facecolor(surf)
    for (k, prof), c, sm in zip(profiles.items(), cols, summs):
        ax.plot(prof[:, 0], prof[:, 1], color=c, lw=2, label=f"{k} ({sm['years']} yr)")
        if sm["L_moving_opt_m"] is not None: ax.axvline(sm["L_moving_opt_m"], color=c, lw=1, ls=":")
    ax.axhline(PARAMS["moving_px"], color="#52514e", lw=1, ls="--"); ax.text(ax.get_xlim()[0], PARAMS["moving_px"], " moving threshold (parameter)", fontsize=7.5, color="#52514e", va="bottom")
    for y, L in edge.items(): ax.axvline(L, color="#0b0b0b", lw=0.8, alpha=0.5)
    ax.text(0, -0.2, "dotted = L_moving_opt per pair · thin black = L_clean_edge_m (2018, 2026) · gaps = rejected matches",
            transform=ax.transAxes, fontsize=7.5, color="#52514e")
    ax.set_xlabel("distance along centerline from top (m)", fontsize=9, color="#52514e"); ax.set_ylabel("|d| (px, 10 m)", fontsize=9, color="#52514e")
    ax.set_title("Ak-Sai: S2 B08 feature displacement along the centerline", loc="left", fontsize=10.5)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    ax.grid(axis="y", color="#e4e3df", lw=0.8); ax.legend(frameon=False, fontsize=8.5)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "offset_profile.png"), facecolor=surf)

def main():
    pairs = [(int(ARGS[i]), int(ARGS[i + 1])) for i in range(0, len(ARGS), 2)] or [(2017, 2026)]
    cl_m, cl_d = centerline()
    tg = json.load(open(P.TONGUE)); TP = P.poly_mask(tg["polygon"])
    S, N, D = s_n_grid(cl_m, cl_d, 1200)
    sp = S[TP]; smid, send = float((sp.min() + sp.max()) / 2), float(sp.max() + PARAMS["roi_below_polygon_m"])
    geo = (S, N, D, cl_m, cl_d, smid, send, TP)
    summs, profiles = [], {}
    for ya, yb in pairs:
        sm, pr = run_pair(ya, yb, geo); summs.append(sm); profiles[f"{ya}→{yb}"] = pr
        print(json.dumps(sm))
    rows = {int(r["year"]): r for r in __import__("csv").DictReader(open(os.path.join(P.OUT, "aksay_metrics.csv")))}
    edge = {y: float(rows[y]["L_clean_edge_m"]) for y in (2018, 2026) if rows[y]["L_clean_edge_m"]}
    plot_profiles(profiles, summs, edge)
    json.dump({"params": PARAMS, "roi_s_range_m": [smid, send], "centerline_end_m": float(cl_d[-1]), "pairs": summs,
               "L_clean_edge_m": edge}, open(os.path.join(OUT, "offset_summary.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
