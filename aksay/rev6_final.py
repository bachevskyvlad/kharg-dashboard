"""
aksay/rev6_final.py — rev6-final analysis (offline): sensor cross-calibration (I), SLC-off / flags checks (J),
breakpoint test (K, only if series may be joined), final metrics / CSV / chart / GIF (L).
Inputs: aksay/out/h_landsat/aksay_landsat_s2_metrics.csv (from h_process.py), aksay/data/landsat_pairs/ (h_pairs.py),
        aksay/data/raw/<year>.npz (S2), aksay/data/landsat/<year>.npz.
Outputs: aksay/out/h_landsat/{aksay_landsat_s2_metrics.csv, rev6_stats.json, h_metrics.png, aksay_timelapse_1990_2026.gif}
"""
import os, sys, json, csv, glob, datetime as dt
import numpy as np
from scipy import stats
from PIL import Image, ImageDraw
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = sys.argv[:1]
import process as P, h_process as H

OUT = os.path.join(P.OUT, "h_landsat"); PAIRS = os.path.join(P.DATA, "landsat_pairs")
VALID_MIN = 0.60           # J3
I1_MAX_DAYS, I2_MAX_DAYS, I1_MIN_PAIRS = 16, 10, 3
BREAK_GRID = np.arange(2005.0, 2018.01, 0.25)
G = json.load(open(P.GEOM)); TG = json.load(open(P.TONGUE)); TP = P.poly_mask(TG["polygon"])
CL_M, CL_D = P.densify([P.to_m(*q) for q in G["centerline"]])
AREA = TP.sum() * P.PXW * P.PXH / 1e6
FAM = {"L5": "L5/7", "L7": "L5/7", "L8": "L8/9", "L9": "L8/9", "S2": "S2"}

def dec(date):
    d = dt.date.fromisoformat(date); return d.year + (d.timetuple().tm_yday - 1) / 365.25

def ols(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float); X = np.c_[np.ones_like(x), x - 2010]
    b, *_ = np.linalg.lstsq(X, y, rcond=None); df = len(y) - 2; rss = float(((y - X @ b) ** 2).sum())
    se = np.sqrt(rss / df / ((x - x.mean()) ** 2).sum()); t = b[1] / se
    return {"slope_per_yr": round(float(b[1]), 4), "sigma": round(float(se), 4), "t": round(float(t), 2), "df": int(df),
            "p": float(f"{2 * stats.t.sf(abs(t), df):.2g}"), "n": len(y), "intercept_2010": round(float(b[0]), 4), "rss": rss}

# ── frame loaders → (bright mask, valid mask, ice-edge mask) on the 10 m fetch grid ─────────────────────────
def landsat_from_npz(f):
    z = np.load(f); qa = H.up(z["qa_pixel"].astype(np.uint16))
    sr = lambda k: np.clip(H.up(z[k]).astype(np.float32) * 0.275 - 2000.0, 0, 1e4)
    d = {"b02": sr("blue"), "b03": sr("green"), "b04": sr("red"), "b08": sr("nir08"), "b11": sr("swir16"),
         "mask": ((qa & 1) == 0).astype(np.uint8)}
    nd = P.ndsi(d); cloud = (((qa >> 3) & 1) == 1) & (((qa >> 8) & 3) == 3) & (nd <= 0.4) & (d["b02"] > 2000)
    return d, cloud, str(z["platform"]), str(z["date"])

def s2_frame(y):
    d = dict(np.load(os.path.join(P.DATA, "raw", f"{y}.npz"))); return d, np.isin(d["scl"], [3, 8, 9, 10])

def bright_valid(d, cloud):
    ok = TP & (d["mask"] > 0) & ~cloud
    return (P.ndsi(d) > TG["ndsi_threshold"]) & ok, ok

def edge(d, cloud):
    L = H.front_skip_invalid(P.ice_mask(d) & ~cloud, (d["mask"] > 0) & ~cloud, CL_M, CL_D)
    return L if (L is not None and L >= H.L_EDGE_MIN_M) else None

# ── I1 / I2 / I3 ────────────────────────────────────────────────────────────────────────────────────────────
def cross_cal(main_rows):
    idx = json.load(open(os.path.join(PAIRS, "index.json"))) if os.path.exists(os.path.join(PAIRS, "index.json")) else {}
    frames = {}
    for k, v in idx.items():
        f = os.path.join(PAIRS, k + ".npz")
        if v.get("clean") and os.path.exists(f): frames[k] = (v, landsat_from_npz(f))
    res = {"frames_scanned": len(idx), "frames_clean": len(frames), "I1": [], "I2": []}
    # I1: L7 ↔ L8, 2013–2015, |Δ| ≤ 16 d, L7 stripe/cloud mask imposed on L8 (common valid pixels)
    l7 = {k: v for k, v in frames.items() if v[0]["platform"] == "landsat-7" and v[0]["purpose"] == "I1"}
    l8 = {k: v for k, v in frames.items() if v[0]["platform"] == "landsat-8" and v[0]["purpose"] == "I1"}
    for k7, (m7, (d7, c7, _, t7)) in l7.items():
        for k8, (m8, (d8, c8, _, t8)) in l8.items():
            dd = abs((dt.date.fromisoformat(t7) - dt.date.fromisoformat(t8)).days)
            if dd > I1_MAX_DAYS: continue
            b7, v7 = bright_valid(d7, c7); b8, v8 = bright_valid(d8, c8); common = v7 & v8
            if common.sum() < 0.5 * TP.sum(): continue
            a7 = b7[common].mean() * AREA; a8 = b8[common].mean() * AREA
            e7, e8 = edge(d7, c7), edge(d8, c8)
            res["I1"].append({"L7": t7, "L8": t8, "days": dd, "common_valid_frac": round(float(common.sum() / TP.sum()), 3),
                              "bright_L7_km2": round(float(a7), 3), "bright_L8_km2": round(float(a8), 3), "diff_L7_minus_L8": round(float(a7 - a8), 3),
                              "edge_L7_m": e7, "edge_L8_m": e8, "edge_diff_L7_minus_L8": None if (e7 is None or e8 is None) else round(e7 - e8, 1)})
    # I2: L8/9 ↔ S2 (clean S2 years 2017–2026), |Δ| ≤ 10 d; S2 has no gaps → same-pixel comparison on the Landsat valid set
    s2clean = {int(r["year"]): r for r in main_rows if r["sensor"] == "S2" and not r["flag"]}
    for k, (m, (d, c, plat, t)) in frames.items():
        if not m["purpose"].startswith("I2"): continue
        s2date = m["purpose"].split(":")[1]; y = int(s2date[:4])
        if y not in s2clean: continue
        dd = abs((dt.date.fromisoformat(t) - dt.date.fromisoformat(s2date)).days)
        if dd > I2_MAX_DAYS: continue
        ds, cs = s2_frame(y); bl, vl = bright_valid(d, c); bs, vs = bright_valid(ds, cs); common = vl & vs
        al = bl[common].mean() * AREA; as_ = bs[common].mean() * AREA
        el, es = edge(d, c), edge(ds, cs)
        res["I2"].append({"year": y, "landsat": t, "platform": plat, "s2": s2date, "days": dd,
                          "bright_L_km2": round(float(al), 3), "bright_S2_km2": round(float(as_), 3), "diff_L_minus_S2": round(float(al - as_), 3),
                          "edge_L_m": el, "edge_S2_m": es, "edge_diff_L_minus_S2": None if (el is None or es is None) else round(el - es, 1)})
    med = lambda xs: (round(float(np.median(xs)), 3) if xs else None)
    d1 = [r["diff_L7_minus_L8"] for r in res["I1"]]; d2 = [r["diff_L_minus_S2"] for r in res["I2"]]
    e1 = [r["edge_diff_L7_minus_L8"] for r in res["I1"] if r["edge_diff_L7_minus_L8"] is not None]
    e2 = [r["edge_diff_L_minus_S2"] for r in res["I2"] if r["edge_diff_L_minus_S2"] is not None]
    res["offsets"] = {"bright_L7_minus_L8_km2": med(d1), "n_I1": len(d1), "I1_accepted": len(d1) >= I1_MIN_PAIRS,
                      "bright_L8_minus_S2_km2": med(d2), "n_I2": len(d2),
                      "edge_L7_minus_L8_m": med(e1), "n_edge_I1": len(e1), "edge_L8_minus_S2_m": med(e2), "n_edge_I2": len(e2)}
    return res

# ── J1: SLC-off sensitivity of the L5/7 bright-area fit ─────────────────────────────────────────────────────
def j1(rows):
    a = [r for r in rows if r["family"] == "L5/7" and r["year"] <= 2012 and r["clean_area"]]
    b = [r for r in a if not r["slc_off"]]
    fa = ols([r["dec"] for r in a], [r["bright_km2"] for r in a]); fb = ols([r["dec"] for r in b], [r["bright_km2"] for r in b])
    drop = abs(fa["slope_per_yr"] - fb["slope_per_yr"]) > max(fa["sigma"], fb["sigma"])
    return {"all_clean_1996_2012": fa, "without_slc_off": fb, "drop_slc_off_from_area": bool(drop)}

# ── K: one-breakpoint (discontinuous two-segment) vs linear ─────────────────────────────────────────────────
def k_break(x, y, l7_l8_offset):
    x, y = np.asarray(x, float), np.asarray(y, float); n = len(y)
    lin = ols(x, y); rss0 = lin["rss"]
    best = None
    for b in BREAK_GRID:
        m1 = x < b
        if m1.sum() < 3 or (~m1).sum() < 3: continue
        rss = 0.0; seg = []
        for m in (m1, ~m1):
            X = np.c_[np.ones(m.sum()), x[m] - b]; c, *_ = np.linalg.lstsq(X, y[m], rcond=None)
            rss += float(((y[m] - X @ c) ** 2).sum()); seg.append(c)
        if best is None or rss < best[1]: best = (b, rss, seg)
    b, rss1, seg = best
    k0, k1 = 3, 6                                  # linear: a, b, σ ; segmented: 2×(a, b) + breakpoint + σ
    aic = lambda rss, k: n * np.log(rss / n) + 2 * k
    bic = lambda rss, k: n * np.log(rss / n) + k * np.log(n)
    F = ((rss0 - rss1) / (k1 - k0)) / (rss1 / (n - (k1 - 1))); pF = float(stats.f.sf(F, k1 - k0, n - (k1 - 1)))
    jump = float(seg[1][0] - seg[0][0])               # level jump at the breakpoint (right − left), km²
    near_sensor = abs(b - 2013.5) <= 1.5
    bic_gain = bic(rss0, k0) - bic(rss1, k1)
    declared = bool(bic_gain >= 2 and (not near_sensor or (l7_l8_offset is not None and abs(l7_l8_offset) < abs(jump) / 2)))
    return {"breakpoint": round(float(b), 2), "slope_before": round(float(seg[0][1]), 4), "slope_after": round(float(seg[1][1]), 4),
            "jump_at_break_km2": round(jump, 3), "rss_linear": round(rss0, 4), "rss_segmented": round(rss1, 4),
            "AIC_linear": round(float(aic(rss0, k0)), 2), "AIC_segmented": round(float(aic(rss1, k1)), 2),
            "BIC_linear": round(float(bic(rss0, k0)), 2), "BIC_segmented": round(float(bic(rss1, k1)), 2), "BIC_gain": round(float(bic_gain), 2),
            "F": round(float(F), 2), "F_df": [k1 - k0, n - (k1 - 1)], "F_p": float(f"{pF:.2g}"),
            "break_within_2012_2015": bool(near_sensor), "declared": declared}

# ── L4: GIF with one display stretch for all sensors ────────────────────────────────────────────────────
def gif(rows, s2lo, s2hi):
    """One S2 display stretch (p2–p98, gamma) for every frame. Each Landsat family is first mapped band-by-band onto the
    S2 radiometry by matching its p5 and p60 quantiles (crop pixels, clean frames) to those of the clean S2 frames —
    p60 rather than p98 because TM/ETM+ visible bands saturate on snow. Saturated Landsat pixels are drawn white."""
    sl = P.crop_slices(G); oy, ox = sl[0].start, sl[1].start; sw, sh = sl[1].stop - ox, sl[0].stop - oy
    bands = ("b04", "b03", "b02")
    def q(ds, qs):
        out = {}
        for b in bands:
            v = np.concatenate([d[b][sl][d["mask"][sl] > 0].ravel()[::3] for d in ds]).astype(np.float32)
            out[b] = [float(np.percentile(v, x)) for x in qs]
        return out
    s2c = [s2_frame(r["year"])[0] for r in rows if r["sensor"] == "S2" and not r["flag"]]
    ref = q(s2c, (5, 60))
    fam_q = {}
    for fam in ("L5/7", "L8/9"):
        ds = [H.load_landsat(r["year"])[0] for r in rows if r["family"] == fam and not r["flag"]]
        fam_q[fam] = q(ds, (5, 60))
    def render(d, fam):
        a = np.stack([d[b][sl] for b in bands], -1).astype(np.float32)
        if fam != "S2":
            sat = (a >= 0.98 * a.max(axis=(0, 1), keepdims=True)).any(-1) if fam == "L5/7" else np.zeros(a.shape[:2], bool)
            for i, b in enumerate(bands):
                (l5, l60), (r5, r60) = fam_q[fam][b], ref[b]
                a[..., i] = (a[..., i] - l5) / max(1e-6, l60 - l5) * (r60 - r5) + r5
            a[sat] = s2hi
        return (np.clip((a - s2lo) / (s2hi - s2lo), 0, 1) ** P.GAMMA * 255).astype(np.uint8)
    frames = []; Pp = lambda ll: (P.ll2px(*ll)[0] - ox, P.ll2px(*ll)[1] - oy)
    s2years = {r["year"] for r in rows if r["sensor"] == "S2"}
    seq = [r for r in rows if r["sensor"] != "S2" and r["year"] < min(s2years)] + [r for r in rows if r["sensor"] == "S2"]
    for r in seq:
        d = s2_frame(r["year"])[0] if r["sensor"] == "S2" else H.load_landsat(r["year"])[0]
        im = Image.fromarray(render(d, r["family"])).convert("RGBA"); dr = ImageDraw.Draw(im)
        dr.polygon([Pp(q_) for q_ in TG["polygon"]], outline=(255, 255, 255)); dr.line([Pp(q_) for q_ in G["centerline"]], fill=(255, 214, 0), width=2)
        bar = 500 / P.PXW; fx0, fy0 = sw - 24 - bar, 22
        dr.rectangle([fx0 - 10, 8, sw - 14, fy0 + 14], fill=(0, 0, 0, 170)); dr.rectangle([fx0, fy0, fx0 + bar, fy0 + 6], fill=(255, 255, 255))
        dr.text((fx0 + bar / 2 - 22, fy0 - 14), "500 m", font=P.font(P.FONT_B, 13), fill=(255, 255, 255))
        sens = "S2 10 m" if r["sensor"] == "S2" else f"{r['sensor']} 30 m"
        fl = (" · ⚠ cloud" if "cloud" in r["flag"] else " · ⚠ snow") if r["flag"] else ""
        P.year_badge(im, str(r["year"]), f"{sens} · tongue bright {r['bright_km2']:.2f} km²{fl}")
        im.convert("RGB").save(os.path.join(OUT, "frames", f"{r['year']}.png")); frames.append(im)
    P.save_gif(frames, os.path.join(OUT, "aksay_timelapse_1990_2026.gif"), ms=900)
    return {"display_stretch_S2_p2_p98": [round(s2lo), round(s2hi)], "quantile_match_p5_p60": {"S2": ref, **fam_q}}

def plot(rows, fits, joined, edge_fit):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    surf, ink, mute, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    col = {"L5/7": "#2a78d6", "L8/9": "#1baf7a", "S2": "#eb6834"}
    fig, axs = plt.subplots(2, 1, figsize=(9.2, 7), sharex=True, dpi=130); fig.patch.set_facecolor(surf)
    for ax, key, title, unit in ((axs[0], "bright_km2", "Bright (NDSI > 0.1) in the frozen tongue polygon — separate fits per sensor family", "km²"),
                                 (axs[1], "L_clean_edge_m", "Clean-ice edge along the centerline (not the terminus)", "m from top")):
        ax.set_facecolor(surf)
        for fam, c in col.items():
            rr = [r for r in rows if r["family"] == fam and r[key] is not None]
            for r in rr:
                use = (r["clean_area"] if key == "bright_km2" else not r["flag"])
                ax.scatter(r["dec"], r[key], s=40, color=c if use else surf, edgecolor=c, linewidth=1.8, zorder=3)
            ax.scatter([], [], s=40, color=c, label=fam)
            f = fits.get(key, {}).get(fam)
            if f:
                xx = np.array([min(r["dec"] for r in rr if (r["clean_area"] if key == "bright_km2" else not r["flag"])),
                               max(r["dec"] for r in rr if (r["clean_area"] if key == "bright_km2" else not r["flag"]))])
                ax.plot(xx, f["intercept_2010"] + f["slope_per_yr"] * (xx - 2010), color=c, lw=1.6)
        if key == "L_clean_edge_m" and edge_fit:
            xx = np.array([1996, 2026.7]); ax.plot(xx, edge_fit["intercept_2010"] + edge_fit["slope_per_yr"] * (xx - 2010), color=mute, lw=1.2, ls="--")
        ax.set_title(title, loc="left", fontsize=10, color=ink); ax.set_ylabel(unit, fontsize=9, color=mute)
        ax.grid(axis="y", color=grid, lw=0.8); ax.tick_params(colors=mute, labelsize=8.5)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"): ax.spines[sp].set_color(grid)
    axs[0].legend(frameon=False, fontsize=8.5, loc="upper right")
    axs[1].text(0, -0.2, "filled = used in fit · hollow = flagged / excluded · solid lines = per-family OLS · dashed = single edge fit 1996–2026 (all sensors)",
                transform=axs[1].transAxes, fontsize=7.5, color=mute)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "h_metrics.png"), facecolor=surf)

def main():
    rows = []
    for r in csv.DictReader(open(os.path.join(OUT, "aksay_landsat_s2_metrics.csv"))):
        r = dict(r); r["year"] = int(r["year"])
        for k in ("valid_poly_frac", "bright_km2", "snow_km2", "ice_km2", "debris_km2"): r[k] = float(r[k])
        r["L_clean_edge_m"] = float(r["L_clean_edge_m"]) if r["L_clean_edge_m"] else None
        r["family"] = FAM[r["sensor"]]; r["dec"] = dec(r["date"])
        r["slc_off"] = r["sensor"] == "L7" and r["date"] >= "2003-05-31"
        rows.append(r)
    # J2: 1999 checked (h_process: bbox NDSI-snow 30.2 % < flag 32.8 %, polygon QA-snow 32 % like 1996/2001, no NIR ≥ 0.6) → not flagged
    for r in rows: r["clean_area"] = (not r["flag"]) and r["valid_poly_frac"] >= VALID_MIN            # J3
    J = j1(rows)
    if J["drop_slc_off_from_area"]:
        for r in rows:
            if r["slc_off"]: r["clean_area"] = False
    I = cross_cal(rows); off = I["offsets"]
    joined = bool(off["I1_accepted"])
    fits = {"bright_km2": {}, "L_clean_edge_m": {}}
    for fam in ("L5/7", "L8/9", "S2"):
        rr = [r for r in rows if r["family"] == fam and r["clean_area"] and (fam != "L8/9" or r["year"] >= 2013)]
        if len(rr) >= 3: fits["bright_km2"][fam] = ols([r["dec"] for r in rr], [r["bright_km2"] for r in rr])
        re = [r for r in rows if r["family"] == fam and not r["flag"] and r["L_clean_edge_m"] is not None]
        if len(re) >= 3: fits["L_clean_edge_m"][fam] = ols([r["dec"] for r in re], [r["L_clean_edge_m"] for r in re])
    # K3 fallback: L5/7 1996–2012 and L8/9+S2 2014–2026 separately
    l89s2 = [r for r in rows if r["family"] in ("L8/9", "S2") and r["year"] >= 2014 and r["clean_area"]]
    fits["bright_km2"]["L8/9+S2 2014–2026 (S2 shifted by L8−S2 offset)"] = ols(
        [r["dec"] for r in l89s2], [r["bright_km2"] + (off["bright_L8_minus_S2_km2"] or 0) * (r["family"] == "S2") for r in l89s2])
    K = None; joined_fit = None
    if joined:
        # put everything on the L8 scale: L5/7 − (L7−L8), S2 + (L8−S2)
        def adj(r):
            return r["bright_km2"] - (off["bright_L7_minus_L8_km2"] if r["family"] == "L5/7" else 0) + (off["bright_L8_minus_S2_km2"] if r["family"] == "S2" else 0)
        jr = sorted([r for r in rows if r["clean_area"] and (r["family"] != "S2" or r["year"] >= 2016)], key=lambda r: r["dec"])
        # one value per year: Landsat for ≤ 2015, S2 thereafter (S2 is the denser, 10 m series)
        by = {}
        for r in jr:
            if r["year"] <= 2015 and r["family"] != "S2": by[r["year"]] = r
            if r["year"] >= 2016 and r["family"] == "S2": by[r["year"]] = r
        jr = [by[y] for y in sorted(by)]
        for r in rows: r["offset_applied_km2"] = 0.0
        for r in jr: r["offset_applied_km2"] = round(adj(r) - r["bright_km2"], 3)
        joined_fit = ols([r["dec"] for r in jr], [adj(r) for r in jr])
        K = k_break([r["dec"] for r in jr], [adj(r) for r in jr], off["bright_L7_minus_L8_km2"])
    else:
        for r in rows: r["offset_applied_km2"] = None
    # L1: single clean-ice edge series 1996–2026, all sensors (edge offsets from I3 NOT applied: |offset| within the 30 m quantum is checked)
    er = [r for r in rows if not r["flag"] and r["L_clean_edge_m"] is not None]
    edge_fit = ols([r["dec"] for r in er], [r["L_clean_edge_m"] for r in er])
    edge_fit["sensors"] = sorted({r["sensor"] for r in er})
    s2st = json.load(open(os.path.join(P.OUT, "run_meta.json")))["stretch_p2_p98"]
    GI = gif(rows, *s2st)
    plot(rows, fits, joined, edge_fit)
    # L5: CSV with sensor, valid_fraction, flag, offset_applied
    cols = ["year", "sensor", "family", "date", "valid_poly_frac", "slc_off", "bright_km2", "ice_km2", "snow_km2", "debris_km2", "L_clean_edge_m",
            "flag", "clean_area", "offset_applied_km2"]
    with open(os.path.join(OUT, "aksay_landsat_s2_metrics.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow({**r, "valid_poly_frac": r["valid_poly_frac"]})
    out = {"J1": J, "J2": "1999 not flagged (bbox NDSI-snow 30.2 % < 32.8 % flag; polygon QA-snow 32 % ≈ 1996/2001; no NIR ≥ 0.6)",
           "J3": {"valid_min": VALID_MIN, "excluded_years": [r["year"] for r in rows if r["sensor"] != "S2" and not r["flag"] and r["valid_poly_frac"] < VALID_MIN]},
           "I": I, "joined": joined, "joined_fit_bright_L8scale": joined_fit, "K": K, "fits": fits, "L1_edge_fit_all_sensors": edge_fit,
           "L1_edge_offsets_applied": False, "gif": GI}
    json.dump(out, open(os.path.join(OUT, "rev6_stats.json"), "w"), indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("J1", "J3", "joined", "K")}, indent=1, default=str)[:3000])
    print(json.dumps(I["offsets"], indent=1)); print(json.dumps({k: {f: (v['slope_per_yr'], v['sigma'], v['p'], v['n']) for f, v in d.items()} for k, d in fits.items()}, indent=1, ensure_ascii=False))
    print("edge all", edge_fit["slope_per_yr"], edge_fit["sigma"], edge_fit["p"], edge_fit["n"])

if __name__ == "__main__":
    main()
