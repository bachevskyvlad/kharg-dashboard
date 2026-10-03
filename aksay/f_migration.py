"""
aksay/f_migration.py — rev5 Part F: migration of the active-ice limit, S2 B08 offset tracking.
D-sensitivity parameters are FROZEN (16 px window, 4 px step, ±8 px search, NCC ≥ 0.5, peak ratio ≥ 1.3, 3×3 median).
Pairs on the clean frames: 2018→19, 19→21*, 21→22, 22→23, 23→25*, 25→26 (* = 2-year pair across a flagged year).
Per pair: |d| normalised to m/yr; QC = stable-rock median |d| per year ≤ 0.5 px/yr.
L_active_m: along the frozen centerline, the last point (going down-glacier) where the 150 m running median of
|d| (m/yr) is ≥ k × the pair's noise floor (stable-rock median |d|, m/yr); k = 2 and 3 (parameters).
Outputs: aksay/out/f_migration/ (profiles PNG, f_summary.json, zone table).
"""
import os, sys, json
os.environ["AKSAY_D_PARAMS"] = json.dumps({"win": 16, "search": 8, "step": 4})      # frozen D-sensitivity set
os.environ["AKSAY_D_TAG"] = "f_pairs"
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = sys.argv[:1]
import numpy as np
from scipy import stats
import d_offset as DO, process as P

PAIRS = [(2018, 2019), (2019, 2021), (2021, 2022), (2022, 2023), (2023, 2025), (2025, 2026)]
QC_MAX_PX_PER_YR = 0.5
WIN_M = 150
KS = (2.0, 3.0)
ZONES = [(2800, 3350), (3350, 3600), (3600, 3950), (3950, 4470)]
OUT = os.path.join(HERE, "out", "f_migration"); os.makedirs(OUT, exist_ok=True)
assert DO.PARAMS["win"] == 16 and DO.PARAMS["step"] == 4 and DO.PARAMS["search"] == 8 \
    and DO.PARAMS["ncc_min"] == 0.5 and DO.PARAMS["peak_ratio_min"] == 1.3

def running_median(d, v, win):
    out = np.full_like(v, np.nan)
    for i, x in enumerate(d):
        w = v[(d >= x - win / 2) & (d <= x + win / 2)]; w = w[~np.isnan(w)]
        if w.size >= 3: out[i] = np.median(w)
    return out

def l_active(d, rm, thr, s0):
    sel = (d >= s0) & ~np.isnan(rm)
    above = np.flatnonzero(sel & (rm >= thr))
    return round(float(d[above[-1]]), 1) if above.size else None

def ols(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    X = np.c_[np.ones_like(x), x - x.mean()]; b, *_ = np.linalg.lstsq(X, y, rcond=None)
    df = len(x) - 2; s2 = ((y - X @ b) ** 2).sum() / df; se = np.sqrt(s2 / ((x - x.mean()) ** 2).sum())
    t = b[1] / se
    return {"slope_per_yr": round(float(b[1]), 2), "sigma": round(float(se), 2), "t": round(float(t), 2), "df": int(df),
            "p": float(f"{2 * stats.t.sf(abs(t), df):.2g}"), "resid_sd": round(float(np.sqrt(s2)), 1), "n": len(x)}

def main():
    cl_m, cl_d = DO.centerline(); TP = DO.P.poly_mask(json.load(open(P.TONGUE))["polygon"])
    S, N, D = DO.s_n_grid(cl_m, cl_d, 1200); sp = S[TP]
    smid, send = float((sp.min() + sp.max()) / 2), float(sp.max() + DO.PARAMS["roi_below_polygon_m"])
    geo = (S, N, D, cl_m, cl_d, smid, send, TP)
    rows, profs = [], {}
    for ya, yb in PAIRS:
        sm, prof = DO.run_pair(ya, yb, geo)
        yrs = sm["years"]; noise_px_yr = sm["coreg"]["median_abs_d_px"] / yrs
        d, v = prof[:, 0], prof[:, 1] * 10.0 / yrs                       # m/yr
        rm = running_median(d, v, WIN_M); profs[(ya, yb)] = (d, v, rm)
        noise = noise_px_yr * 10.0
        r = {"pair": f"{ya}→{yb}", "dates": sm["dates"], "years": yrs, "two_year": yb - ya > 1,
             "mid_year": round((np.datetime64(sm["dates"][0]).astype("datetime64[D]").astype(float) +
                                np.datetime64(sm["dates"][1]).astype("datetime64[D]").astype(float)) / 2 / 365.25 + 1970, 2),
             "valid_windows": f"{sm['roi_valid']}/{sm['roi_windows']}",
             "stable_rock_median_abs_d_px": sm["coreg"]["median_abs_d_px"], "noise_px_per_yr": round(noise_px_yr, 3),
             "noise_m_per_yr": round(noise, 2), "qc_pass": bool(noise_px_yr <= QC_MAX_PX_PER_YR)}
        for k in KS: r[f"L_active_{k:g}x_m"] = l_active(d, rm, k * noise, smid) if r["qc_pass"] else None
        for a, b in ZONES:
            m = (d >= a) & (d < b) & ~np.isnan(v)
            r[f"zone_{a}_{b}_m_per_yr"] = round(float(np.median(v[m])), 1) if m.any() else None
            r[f"zone_{a}_{b}_n"] = int(m.sum())
        rows.append(r); print(json.dumps(r))
    ok = [r for r in rows if r["qc_pass"]]
    res = {"params_frozen": DO.PARAMS, "qc_max_px_per_yr": QC_MAX_PX_PER_YR, "running_median_m": WIN_M, "k": KS,
           "roi_s_range_m": [smid, send], "pairs": rows, "fits": {}}
    # F5: fit L_active vs mid-year; resolvability = residual SD vs half the fitted change over the span
    for k in KS:
        key = f"L_active_{k:g}x_m"; pts = [(r["mid_year"], r[key]) for r in ok if r[key] is not None]
        if len(pts) < 3: res["fits"][key] = {"resolved": False, "reason": "fewer than 3 pairs with a value"}; continue
        x, y = zip(*pts); f = ols(x, y)
        span = max(x) - min(x); half_change = abs(f["slope_per_yr"]) * span / 2
        f["range_m"] = round(max(y) - min(y), 1); f["half_fitted_change_m"] = round(half_change, 1)
        f["resolved"] = bool(f["resid_sd"] <= half_change)
        res["fits"][key] = f
    # F6: zone 3600–3950 trend
    pts = [(r["mid_year"], r["zone_3600_3950_m_per_yr"]) for r in ok if r["zone_3600_3950_m_per_yr"] is not None]
    if len(pts) >= 3: res["fits"]["zone_3600_3950_m_per_yr"] = ols(*zip(*pts))
    json.dump(res, open(os.path.join(OUT, "f_summary.json"), "w"), indent=1, ensure_ascii=False)
    plot(profs, rows, res)

def plot(profs, rows, res):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    cols = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=130); surf = "#fcfcfb"; fig.patch.set_facecolor(surf); ax.set_facecolor(surf)
    for (k, (d, v, rm)), c, r in zip(profs.items(), cols, rows):
        lab = f"{r['pair']}" + (" (2 yr)" if r["two_year"] else "") + ("" if r["qc_pass"] else " QC FAIL")
        ax.plot(d, rm, color=c, lw=2, label=lab, ls="-" if r["qc_pass"] else ":")
        ax.scatter(d[::6], v[::6], s=4, color=c, alpha=0.35)
        if r["L_active_2x_m"]: ax.axvline(r["L_active_2x_m"], color=c, lw=0.8, ls="--")
    for e, lab in ((float(P_EDGE[0]), "clean-ice edge 2018"), (float(P_EDGE[1]), "2026")):
        ax.axvline(e, color="#0b0b0b", lw=0.8, alpha=0.5)
    ax.set_xlabel("distance along centerline from top (m)", fontsize=9, color="#52514e"); ax.set_ylabel("|d| (m/yr)", fontsize=9, color="#52514e")
    ax.set_title("Ak-Sai: surface speed along the centerline, all S2 pairs (150 m running median; dots = raw)", loc="left", fontsize=10)
    ax.text(0, -0.17, "dashed coloured = L_active (2× noise floor) per pair · thin black = clean-ice edge 2018 / 2026 · frozen D-sensitivity parameters",
            transform=ax.transAxes, fontsize=7.5, color="#52514e")
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    ax.grid(axis="y", color="#e4e3df", lw=0.8); ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "f_profiles.png"), facecolor=surf)

import csv
_R = {int(r["year"]): r for r in csv.DictReader(open(os.path.join(P.OUT, "aksay_metrics.csv")))}
P_EDGE = (_R[2018]["L_clean_edge_m"], _R[2026]["L_clean_edge_m"])

if __name__ == "__main__":
    main()
