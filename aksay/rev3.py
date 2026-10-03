"""
aksay/rev3.py — revision 3 analysis (called from process.py):
  A1  snow / ice / debris classes inside the frozen tongue polygon (B08 split, sweep 0.50/0.60/0.70)
  A3  fits with t-tests: clean years, clean + day-of-year; ice with and without 2021
  A4  2017→2026 class-transition map + area table + where ice→debris happens
  A5  slope, σ (standard error), t, df = n − p, two-sided p-value
Outputs into OUT: aksay_metrics.png, aksay_transition_2017_2026.png, rev3_stats.json
"""
import os, json, datetime as dt
import numpy as np
from scipy import stats, ndimage
from PIL import Image, ImageDraw, ImageFont

SWEEP = (0.50, 0.60, 0.70)
BASE_YEAR, END_YEAR = 2017, 2026
SNOW, ICE, DEBRIS = 1, 2, 3
NAMES = {SNOW: "snow", ICE: "ice", DEBRIS: "debris"}

def doy(date):
    return dt.date.fromisoformat(date).timetuple().tm_yday

def dec(r):
    return r["year"] + (doy(r["date"]) - 1) / 365.25

def ols(rr, col, with_doy=False):
    """OLS of col on year (± DOY). Returns slope/yr, σ, t, df, p (two-sided) and DOY term if used."""
    y = np.array([r[col] for r in rr], float)
    X = [np.ones(len(rr)), np.array([dec(r) for r in rr]) - 2021]
    if with_doy: X.append(np.array([doy(r["date"]) for r in rr], float) - 240)
    X = np.array(X).T
    n, k = X.shape
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    df = n - k
    s2 = ((y - X @ b) ** 2).sum() / df
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    t = b[1] / se[1]
    out = {"slope_per_yr": round(float(b[1]), 4), "sigma": round(float(se[1]), 4), "t": round(float(t), 2),
           "df": int(df), "p": float(f"{2 * stats.t.sf(abs(t), df):.2g}"), "n": int(n),
           "years": [r["year"] for r in rr]}
    if with_doy:
        out["doy_per_day"] = round(float(b[2]), 4); out["doy_sigma"] = round(float(se[2]), 4)
        out["doy_p"] = float(f"{2 * stats.t.sf(abs(b[2] / se[2]), df):.2g}")
    return out

def classes(d, TP, ok_extra, b08_t, ndsi_t, P):
    nd = P.ndsi(d); nir = d["b08"] / 1e4
    ok = TP & ok_extra
    c = np.zeros(TP.shape, np.uint8)
    bright = (nd > ndsi_t) & ok
    c[bright & (nir >= b08_t)] = SNOW
    c[bright & (nir < b08_t)] = ICE
    c[ok & ~bright] = DEBRIS
    return c

def run(rows, P):
    OUT = P.OUT
    tg = json.load(open(P.TONGUE)); TP = P.poly_mask(tg["polygon"]); ndsi_t = tg["ndsi_threshold"]
    px_km2 = P.PXW * P.PXH / 1e6
    frames = {y: (meta, d) for y, meta, d in P.load_years()}
    clean = [r for r in rows if not r["snow_flag"]]
    res = {"b08_snow_default": P.B08_SNOW, "ndsi_threshold": ndsi_t}

    # ── A3/A5 fits ──────────────────────────────────────────────────────────
    fits = {}
    for col in ("tongue_ice_km2", "tongue_snow_km2", "tongue_debris_km2", "tongue_bright_km2", "L_clean_edge_m"):
        rr = [r for r in clean if r[col] is not None]
        fits[col] = {"clean": ols(rr, col), "clean_doy": ols(rr, col, True)}
    no21 = [r for r in clean if r["year"] != 2021]
    fits["tongue_ice_km2"]["clean_no2021"] = ols(no21, "tongue_ice_km2")
    fits["tongue_ice_km2"]["clean_doy_no2021"] = ols(no21, "tongue_ice_km2", True)
    res["fits"] = fits
    r21 = next(r for r in rows if r["year"] == 2021)
    res["y2021"] = {k: r21[k] for k in ("date", "tongue_snow_km2", "tongue_ice_km2", "tongue_debris_km2", "tongue_bright_km2")}

    # ── A1 sweep of the B08 snow/ice split ─────────────────────────────────
    sweep = {}
    for t in SWEEP:
        sr = []
        for r in rows:
            meta, d = frames[r["year"]]
            c = classes(d, TP, (d["mask"] > 0) & ~P.cloud_px(d, meta["collection"]), t, ndsi_t, P)
            sr.append({"year": r["year"], "date": r["date"], "snow_flag": r["snow_flag"],
                       "ice": round(float((c == ICE).sum() * px_km2), 3),
                       "snow": round(float((c == SNOW).sum() * px_km2), 3)})
        cs = [x for x in sr if not x["snow_flag"]]
        sweep[f"{t:.2f}"] = {"per_year": sr, "ice_clean": ols(cs, "ice"), "ice_clean_doy": ols(cs, "ice", True),
                             "snow_clean": ols(cs, "snow")}
    res["b08_sweep"] = sweep

    # ── A4 transition map 2017 → 2026 ──────────────────────────────────────
    cmap = {}
    for y in (BASE_YEAR, END_YEAR):
        meta, d = frames[y]
        cmap[y] = classes(d, TP, (d["mask"] > 0) & ~P.cloud_px(d, meta["collection"]), P.B08_SNOW, ndsi_t, P)
    a, b = cmap[BASE_YEAR], cmap[END_YEAR]
    valid = TP & (a > 0) & (b > 0)
    trans = {}
    for ca in (SNOW, ICE, DEBRIS):
        for cb in (SNOW, ICE, DEBRIS):
            k = f"{NAMES[ca]}→{NAMES[cb]}" if ca != cb else f"{NAMES[ca]} (unchanged)"
            trans[k] = round(float(((a == ca) & (b == cb) & valid).sum() * px_km2), 4)
    res["transitions_km2"] = trans
    res["where_ice_to_debris"] = locate(P, TP, a, b, valid)
    draw_transition(P, TP, a, b, valid, frames[END_YEAR][1], os.path.join(OUT, "aksay_transition_2017_2026.png"))

    plot(rows, fits, os.path.join(OUT, "aksay_metrics.png"))
    json.dump(res, open(os.path.join(OUT, "rev3_stats.json"), "w"), indent=1, ensure_ascii=False)
    return res

def locate(P, TP, a, b, valid):
    """Where does ice(2017) → debris(2026) sit: down-glacier end, lateral margins, or scattered?"""
    g = json.load(open(P.GEOM))
    cl_m, cl_d = P.densify([P.to_m(*q) for q in g["centerline"]])
    ys, xs = np.nonzero(TP)
    pts = np.array([P.to_m(*P.px2ll(x, y)) for x, y in zip(xs, ys)])
    # along-flow coordinate s (nearest centerline sample) and signed lateral offset n
    dd = ((pts[:, None, :] - cl_m[None, ::4, :]) ** 2).sum(-1)
    j = dd.argmin(1) * 4
    j = np.clip(j, 1, len(cl_m) - 1)
    tang = cl_m[j] - cl_m[j - 1]; tang /= np.linalg.norm(tang, axis=1)[:, None]
    rel = pts - cl_m[j]
    S = np.zeros(TP.shape); N = np.zeros(TP.shape)
    S[ys, xs] = cl_d[j]; N[ys, xs] = tang[:, 0] * rel[:, 1] - tang[:, 1] * rel[:, 0]

    ice17 = (a == ICE) & valid
    i2d = ice17 & (b == DEBRIS)
    smin, smax = S[ice17].min(), S[ice17].max()
    low = S >= smax - 0.25 * (smax - smin)                         # down-glacier quarter of 2017 ice
    margin = np.zeros(TP.shape, bool)                              # outer 20 % of 2017 ice width per 50 m bin
    for s0 in np.arange(smin, smax + 50, 50):
        band = ice17 & (S >= s0) & (S < s0 + 50)
        if band.sum() < 5: continue
        lo, hi = np.percentile(N[band], [2, 98]); w = hi - lo
        sel = TP & (S >= s0) & (S < s0 + 50)
        margin |= sel & ((N < lo + 0.2 * w) | (N > hi - 0.2 * w))
    lab, nlab = ndimage.label(i2d, structure=np.ones((3, 3)))
    sizes = ndimage.sum(i2d, lab, range(1, nlab + 1))
    big = sum(s for s in sizes if s >= 50)
    n_i2d, n_ice = int(i2d.sum()), int(ice17.sum())
    share = lambda m, base: round(float((m & base).sum() / max(1, base.sum())), 3)
    return {
        "ice2017_px": n_ice, "ice_to_debris_px": n_i2d,
        "frac_of_2017_ice_lost_to_debris": round(n_i2d / max(1, n_ice), 3),
        "down_glacier_quarter": {"share_of_ice_to_debris": share(low, i2d), "share_of_all_2017_ice": share(low, ice17)},
        "lateral_margins_outer20pct": {"share_of_ice_to_debris": share(margin, i2d), "share_of_all_2017_ice": share(margin, ice17)},
        "clumping": {"components": int(nlab), "share_in_components_ge_50px": round(big / max(1, n_i2d), 3),
                     "largest_component_px": int(max(sizes) if nlab else 0)},
    }

def draw_transition(P, TP, a, b, valid, d_end, path):
    ys, xs = np.nonzero(TP); y0, y1, x0, x1 = ys.min() - 8, ys.max() + 9, xs.min() - 8, xs.max() + 9
    g = (np.clip((d_end["b04"] + d_end["b03"] + d_end["b02"]) / 3 / 6000, 0, 1) ** 0.7 * 255).astype(np.uint8)
    im = np.stack([g] * 3, -1).astype(float) * 0.55
    col = {("ice", "debris"): (235, 104, 52), ("ice", "snow"): (27, 175, 122), ("snow", "ice"): (237, 161, 0),
           ("ice", "ice"): (90, 150, 230), ("debris", "ice"): (232, 123, 164), ("snow", "debris"): (227, 73, 72)}
    for (fa, fb), c in col.items():
        ca = {v: k for k, v in NAMES.items()}[fa]; cb = {v: k for k, v in NAMES.items()}[fb]
        m = (a == ca) & (b == cb) & valid
        im[m] = 0.25 * im[m] + 0.75 * np.array(c)
    I = Image.fromarray(im[y0:y1, x0:x1].astype(np.uint8)).resize(((x1 - x0) * 3, (y1 - y0) * 3), Image.NEAREST)
    dr = ImageDraw.Draw(I)
    dr.polygon([((xx - x0 + .5) * 3, (yy - y0 + .5) * 3) for xx, yy in (P.ll2px(*q) for q in json.load(open(P.TONGUE))["polygon"])],
               outline=(255, 255, 255))
    W = I.width + 340
    canvas = Image.new("RGB", (W, max(I.height, 360)), (252, 252, 251)); canvas.paste(I, (0, 0))
    dr = ImageDraw.Draw(canvas)
    try: fb = ImageFont.truetype(P.FONT_B, 17); fr = ImageFont.truetype(P.FONT_R, 14)
    except Exception: fb = fr = ImageFont.load_default()
    dr.text((I.width + 16, 14), "2017 → 2026, tongue polygon", font=fb, fill=(11, 11, 11))
    yy = 48
    labels = [(("ice", "debris"), "ice → debris"), (("ice", "ice"), "ice unchanged"), (("ice", "snow"), "ice → snow"),
              (("snow", "ice"), "snow → ice"), (("snow", "debris"), "snow → debris"), (("debris", "ice"), "debris → ice")]
    for k, t in labels:
        dr.rectangle([I.width + 16, yy, I.width + 34, yy + 16], fill=col[k]); dr.text((I.width + 42, yy - 1), t, font=fr, fill=(11, 11, 11)); yy += 26
    dr.text((I.width + 16, yy + 8), "grey = debris unchanged\nbackground: 2026, Sentinel-2\nwhite outline: frozen polygon v1\nup = north, 10 m px (×3)", font=fr, fill=(82, 81, 78))
    canvas.save(path)

def plot(rows, fits, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    ink, mute, grid, surf = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    series = [("tongue_ice_km2", "bare ice", "#2a78d6"), ("tongue_debris_km2", "debris", "#eb6834"),
              ("tongue_snow_km2", "snow", "#1baf7a")]
    fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=130); fig.patch.set_facecolor(surf); ax.set_facecolor(surf)
    xs = [dec(r) for r in rows]
    for col, lab, c in series:
        ys = [r[col] for r in rows]
        ax.plot(xs, ys, color=c, lw=2, zorder=2, label=lab)
        for r, x, y in zip(rows, xs, ys):
            ax.scatter(x, y, s=46, zorder=3, color=surf if r["snow_flag"] else c, edgecolor=c, linewidth=2)
        ax.annotate(lab, (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points", va="center", fontsize=9, color=ink)
    f, g = fits["tongue_ice_km2"]["clean_doy"], fits["tongue_debris_km2"]["clean_doy"]
    ax.set_title("Ak-Sai tongue polygon (1.29 km²): area by surface class · fits = clean years, DOY-adjusted\n"
                 f"bare ice {f['slope_per_yr']:+.3f} ± {f['sigma']:.3f} km²/yr (t({f['df']}) = {f['t']:.2f}, p = {f['p']:.2g})   ·   "
                 f"debris {g['slope_per_yr']:+.3f} ± {g['sigma']:.3f} km²/yr (t({g['df']}) = {g['t']:.2f}, p = {g['p']:.2g})",
                 loc="left", fontsize=9.5, color=ink)
    ax.set_ylabel("km²", color=mute, fontsize=9); ax.set_xlim(min(xs) - 0.3, max(xs) + 0.9)
    ax.grid(axis="y", color=grid, lw=0.8); ax.tick_params(colors=mute, labelsize=8.5)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"): ax.spines[sp].set_color(grid)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=3)
    ax.text(0, -0.2, "Filled = clean frame · hollow = fresh-snow flag (excluded from fits)\n"
            "snow = NDSI>0.1 & B08≥0.60 · ice = NDSI>0.1 & B08<0.60 · debris = NDSI≤0.1 · ice/snow split is sensitive to the B08 threshold (see report)", transform=ax.transAxes, fontsize=7.5, color=mute)
    fig.tight_layout(); fig.savefig(path, facecolor=surf)
