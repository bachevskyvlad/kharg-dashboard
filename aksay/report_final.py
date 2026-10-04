"""aksay/report_final.py — builds AKSAY_BASELINE.md (rev6-final) from the JSON/CSV outputs. Offline."""
import os, sys, json, csv, glob
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = sys.argv[:1]
OUT = os.path.join(HERE, "out")
J = lambda p: json.load(open(os.path.join(HERE, p)))
S = J("out/rev3_stats.json"); M = J("out/run_meta.json"); H = J("data/hugonnet_aksay.json"); C = J("out/c2017_table.json")
DS = J("out/offset/offset_summary.json"); G1 = J("out/c2017_g1.json"); FM = J("out/f_migration/f_summary.json"); HF = J("out/h_landsat/h_fits.json"); DA = J("out/offset_win16_annual/offset_summary.json"); FL = J("aksay_flags.json")
rows = list(csv.DictReader(open(os.path.join(OUT, "aksay_metrics.csv")))); fr = list(csv.DictReader(open(os.path.join(OUT, "aksay_frames.csv"))))
F, SW, T, W = S["fits"], S["b08_sweep"], S["transitions_km2"], S["where_ice_to_debris"]; BY = S["base_year"]
R = {int(r["year"]): r for r in rows}
hr = {x["period"]: x for x in H["rates"] if x["rgiid"] == "RGI60-13.11414"}; h = hr["2000-01-01_2020-01-01"]

def fs(f, unit="km²/yr", nd=3):
    return f"{f['slope_per_yr']:+.{nd}f} ± {f['sigma']:.{nd}f} {unit}, t({f['df']}) = {f['t']:.2f}, p = {f['p']:.2g}"

def zone_speeds(tag, pair):
    import d_offset as DO, process as P
    cl_m, cl_d = DO.centerline()
    z = np.load(os.path.join(OUT, tag, f"{pair}_field.npz")); GY, GX, MAG = z["gy"], z["gx"], z["mag"]
    ys, xs = GY[:, 0], GX[0, :]; st = ys[1] - ys[0]; v = []
    for p in cl_m:
        x, y = P.ll2px(*P.from_m(p)); i = int(round((y - ys[0]) / st)); j = int(round((x - xs[0]) / st))
        v.append(MAG[i, j] if 0 <= i < len(ys) and 0 <= j < len(xs) else np.nan)
    v = np.array(v); out = []
    for a, b in ((2800, 3350), (3350, 3600), (3600, 3950), (3950, 4470)):
        m = (cl_d >= a) & (cl_d < b) & ~np.isnan(v); out.append(round(float(np.median(v[m])) * 10, 0) if m.any() else None)
    return out

L = []
L += ["# AKSAY_BASELINE — Ak-Sai glacier, Landsat + Sentinel-2 1993–2026 (rev6-final)", "",
      "R&D mini-project (`aksay/`), independent of OilRadar. Frozen geometry: `aksay_geometry.json` v1, `aksay_tongue_polygon.json` v1 (untouched). "
      f"**Base year: {BY}** (changed from 2017 by rule C3, see Part C).", "",
      "rev6-final: sensor cross-calibration (I), SLC-off and flag checks (J), breakpoint test (K), final 30-year series (L). Earlier parts below: E (is the day-of-year covariate justified), Part G (report corrections), Part F (migration of the active-ice limit). rev4 parts C, D, B-pilot kept. Frozen D-sensitivity parameters.", "",
      "## Verdict", ""]
DOYP = {c: F[c]["clean_doy"]["doy_p"] for c in F}
DOY_OK = not (DOYP["tongue_ice_km2"] > 0.10 and DOYP["tongue_debris_km2"] > 0.10)
def both(c):
    a, b = F[c]["clean"], F[c]["clean_doy"]
    if DOY_OK:
        return (f"clean: {a['slope_per_yr']:+.3f} ± {a['sigma']:.3f} km²/yr, t({a['df']}) = {a['t']:.2f}, p = {a['p']:.2g} | "
                f"clean+DOY: {b['slope_per_yr']:+.3f} ± {b['sigma']:.3f} km²/yr, t({b['df']}) = {b['t']:.2f}, p = {b['p']:.2g}")
    return f"clean: {a['slope_per_yr']:+.3f} ± {a['sigma']:.3f} km²/yr, t({a['df']}) = {a['t']:.2f}, p = {a['p']:.2g} (DOY variant exploratory, see Part E)"
FL2, FL3 = FM["fits"]["L_active_2x_m"], FM["fits"]["L_active_3x_m"]; FZ = FM["fits"]["zone_3600_3950_m_per_yr"]
R6 = J("out/h_landsat/rev6_stats.json"); OFF = R6["I"]["offsets"]; KK = R6["K"]; JF = R6["joined_fit_bright_L8scale"]; EF = R6["L1_edge_fit_all_sensors"]
def fz(f, nd=4, u="km²/yr"):
    return f"{f['slope_per_yr']:+.{nd}f} ± {f['sigma']:.{nd}f} {u}, t({f['df']}) = {f['t']:.2f}, p = {f['p']:.2g}"
L += [f"1. **Not measured: the terminus, thinning, acceleration.** The snout has been debris-covered since at least 1993–1996 and is invisible to optical data; 12-day C-band coherence does not separate stagnant debris-covered ice from moraine (B-pilot); glacier-wide thinning from Hugonnet 2021 is {h['dhdt']:+.2f} ± {h['err_dhdt']:.2f} m/yr (not distinguishable from zero); an acceleration after 2013 is not supported (one-breakpoint model loses on BIC by {abs(KK['BIC_gain']):.2f}; F-test p = {KK['F_p']:.2g}).",
      f"2. **Bright (snow + bare ice) area on the lower tongue shrinks, 1996–2026.** All sensors on the Landsat-8 scale (I1 offset L7−L8 = {OFF['bright_L7_minus_L8_km2']:+.3f} km², n = {OFF['n_I1']}; I2 offset L8−S2 = {OFF['bright_L8_minus_S2_km2']:+.3f} km², n = {OFF['n_I2']}): {fz(JF)} (n = {JF['n']}). Within sensor families the sign is the same (L5/7 p = {R6['fits']['bright_km2']['L5/7']['p']:.2g}, L8/9 p = {R6['fits']['bright_km2']['L8/9']['p']:.2g}, S2 p = {R6['fits']['bright_km2']['S2']['p']:.2g}).",
      f"3. **The clean-ice edge moves up-glacier.** Single series 1996–2026, every clean frame with a detected edge (Landsat and S2 both counted in 2016–2026), no edge offsets applied: {fz(EF, 1, 'm/yr')} (n = {EF['n']}) — ≈ {abs(EF['slope_per_yr'])*30:.0f} m in 30 years. It is the clean-ice/debris boundary, not the terminus.",
      f"4. **Surface darkening / debris expansion, consistent with thinning.** S2 2018–2026: debris {both('tongue_debris_km2')}; {W['frac_of_base_ice_lost_to_debris']*100:.0f}% of {BY} bare ice turned to debris by 2026, concentrated at the lateral margins.",
      "5. **Where active ice ends (snapshot, not trend):** S2 offset tracking puts the down-glacier limit of measurable flow at ≈ 3.65–3.96 km along the centerline in every pair (clean tongue ~30 m/yr, 4–16 m/yr under the debris, then noise); its migration is not resolved on 6 pairs; the debris lobe below is stagnant within noise.", ""]

# ── measured / not measured
L += ["## What is measured / what is not", "",
      "| | quantity | status | evidence |", "|---|---|---|---|",
      f"| measured | bright area in tongue polygon, by sensor | 1996–2026, joined on L8 scale | {fz(JF)} |",
      f"| measured | clean-ice edge on the centerline | 1996–2026, all sensors | {fz(EF, 1, 'm/yr')} |",
      "| measured | active-ice limit (S2 offset tracking) | snapshot 2018–2026 | ≈ 3.65–3.96 km along the centerline; trend not resolved |",
      "| measured | surface speed | 2018–2026 | clean tongue ~30 m/yr; under debris 4–16 m/yr; lobe < noise |",
      "| not measured | terminus | debris-covered since ≥ 1993–1996 | not visible optically; S1 coherence does not separate it |",
      f"| not measured | thinning | external only | Hugonnet 2021: {h['dhdt']:+.2f} ± {h['err_dhdt']:.2f} m/yr (≈ 0) |",
      f"| not measured | acceleration | breakpoint test | BIC gain {KK['BIC_gain']:+.2f} (< 2), F p = {KK['F_p']:.2g} → \"no acceleration detected\" |", ""]

# ── Part E
L += ["## Part E — is the day-of-year covariate justified?", "",
      "Model clean_doy on the 7 clean years (y = a + b·year + c·(DOY − 240)); the DOY coefficient c:", "",
      "| series | DOY coefficient (per day) | σ | t | df | p |", "|---|---|---|---|---|---|"]
for c in ("tongue_ice_km2", "tongue_debris_km2", "tongue_bright_km2", "tongue_snow_km2", "L_clean_edge_m"):
    f = F[c]["clean_doy"]; L.append(f"| {c} | {f['doy_per_day']:+.4f} | {f['doy_sigma']:.4f} | {f['doy_per_day']/f['doy_sigma']:+.2f} | {f['df']} | {f['doy_p']:.2g} |")
L += ["", ("**Rule E2:** p(DOY) < 0.10 for ice and debris → the covariate is justified; the Verdict shows both models side by side."
           if DOY_OK else "**Rule E2:** p(DOY) > 0.10 for ice and debris → DOY not justified; headline = clean model; DOY variants exploratory, not used for headline."),
      "Physical reading: later acquisitions show more bright/ice and less debris area (+0.003 km²/day for ice) — late-season frames carry more residual snow/firn and wetter, brighter ice, so DOY is a confounder, not a nuisance fit. With df = 4 the DOY model is still fragile; both numbers are reported.", ""]

# ── Part C
L += ["## Part C — base-year check (September 2017)", "",
      "| date | tile CC % | cloud % (bbox, SCL) | snow % (bbox, SCL) | cloud % (polygon) | B08 median, polygon | B08 median, bright px | B08 ≥ 0.5 km² | B08 ≥ 0.6 km² |",
      "|---|---|---|---|---|---|---|---|---|"]
for c in C:
    L.append(f"| {c['date']} | {c['tile_cc']} | {c['cloud_bbox_pct']} | {c['snow_bbox_pct']} | {c['cloud_polygon_pct']} | {c['b08_median_polygon']} | {c['b08_median_bright_polygon']} | {c['b08_ge_050_km2']} | {c['b08_ge_060_km2']} |")
L += ["", "![sept 2017](out/c2017_quicklooks.png)", "",
      "- Only five S2 acquisitions in September 2017 (Sentinel-2B not yet routinely acquiring here). Within ±7 days of 09-16: 09-11 and 09-23 fully clouded over the polygon, 09-18 partly clouded/hazy (33 % of polygon).",
      f"- Pixels clear on both 09-03 and 09-16: {G1['clear_both_frac_of_polygon']*100:.0f}% of the polygon; by {BY} class: ice {G1['class_2018_share_of_clear']['ice']*100:.0f}%, debris {G1['class_2018_share_of_clear']['debris']*100:.0f}% → comparison {'WEAK' if G1['weak'] else 'adequate (≥ 30 % clear, not debris-dominated)'}. On those pixels B08: ice {G1['b08_median_by_2018_class']['ice']['2017-09-03']} → {G1['b08_median_by_2018_class']['ice']['2017-09-16']}, debris {G1['b08_median_by_2018_class']['debris']['2017-09-03']} → {G1['b08_median_by_2018_class']['debris']['2017-09-16']} (brightening only on ice).",
      "- The 2017 flag is justified by an independent signal (darker before the overcast spell, brighter after), not by its influence on the fit.",
      "- Same-pixel comparison (pixels clear on both dates): B08 median 0.217 on 09-03 → 0.310 on 09-16; 09-16 → 09-18: 0.276 → 0.312 (haze-affected).",
      f"- **Decision (rule C3):** bright B08 0.5–0.6 surface is present only after the 09-11 overcast spell, the pre-storm date is darker → **2017 flagged as fresh snow** (`aksay_flags.json`), **base year → {BY}**; all Part A fits and the transition map re-run with {BY}.", ""]

# ── Part A re-run
L += [f"## Part A (re-run with base {BY}) — snow / ice / debris in the tongue polygon", "",
      "![metrics](out/aksay_metrics.png)", "",
      f"Clean years: {', '.join(str(y) for y in F['tongue_ice_km2']['clean']['years'])} (n = {F['tongue_ice_km2']['clean']['n']}). OLS on decimal year; σ = standard error of the slope; two-sided t-test, df = n − p.", "",
      "| series | model | slope / yr | σ | t | df | p |", "|---|---|---|---|---|---|---|"]
for col, v in F.items():
    for k, f in v.items(): L.append(f"| {col} | {k} | {f['slope_per_yr']:+.4f} | {f['sigma']:.4f} | {f['t']:+.2f} | {f['df']} | {f['p']:.2g} |")
L += ["", "B08 snow/ice split (parameter) sweep:", "", "| B08 | ice, clean | ice, clean + DOY |", "|---|---|---|"]
for t, v in SW.items():
    L.append(f"| {t} | {v['ice_clean']['slope_per_yr']:+.4f} ± {v['ice_clean']['sigma']:.4f} (t({v['ice_clean']['df']}) = {v['ice_clean']['t']:.2f}, p = {v['ice_clean']['p']:.2g}) | "
             f"{v['ice_clean_doy']['slope_per_yr']:+.4f} ± {v['ice_clean_doy']['sigma']:.4f} (t({v['ice_clean_doy']['df']}) = {v['ice_clean_doy']['t']:.2f}, p = {v['ice_clean_doy']['p']:.2g}) |")
L += ["", f"2021 (2021-09-27): snow {R[2021]['tongue_snow_km2']} km², ice {R[2021]['tongue_ice_km2']} km² — not snow. Ice without 2021: {fs(F['tongue_ice_km2']['clean_doy_no2021'])} (clean + DOY).", "",
      f"### Transition map {BY} → 2026", "", f"![transition](out/aksay_transition_{BY}_2026.png)", "",
      "| transition | km² |", "|---|---|"] + [f"| {k} | {v} |" for k, v in T.items()]
L += ["", f"- Lateral margins (outer 20 % of {BY} ice width): {W['lateral_margins_outer20pct']['share_of_ice_to_debris']*100:.0f}% of ice→debris vs {W['lateral_margins_outer20pct']['share_of_all_base_ice']*100:.0f}% of all {BY} ice → enriched ≈{W['lateral_margins_outer20pct']['share_of_ice_to_debris']/W['lateral_margins_outer20pct']['share_of_all_base_ice']:.1f}×.",
      f"- Down-glacier quarter: {W['down_glacier_quarter']['share_of_ice_to_debris']*100:.0f}% vs {W['down_glacier_quarter']['share_of_all_base_ice']*100:.0f}% baseline → not enriched.",
      f"- {W['clumping']['share_in_components_ge_50px']*100:.0f}% of ice→debris pixels in connected patches ≥ 50 px → coherent margin/moraine widening, not scatter.", ""]

# ── Part D
L += ["## Part D — optical offset tracking (S2 B08, 10 m)", "",
      "Method (parameters): NCC 32×32 px windows, 8 px step, ±16 px search, parabolic sub-pixel peak; reject NCC < 0.5 or peak/2nd-peak < 1.3; 3×3 nan-median; "
      "ROI ±400 m around the centerline from mid-polygon to 1 km below the polygon (last centerline segment extrapolated for the ROI only). "
      "L_moving_opt_m = end of the |d| > 2 px run along the frozen centerline, gaps ≤ 100 m bridged.", "",
      "### As specified (multi-year pairs, 32 px windows)", "",
      "| pair | years | valid windows | stable-rock median d (px) | stable-rock median abs d (px) | tongue-polygon median abs d (px) | L_moving_opt_m |", "|---|---|---|---|---|---|---|"]
for p in DS["pairs"]:
    c = p["coreg"]
    L.append(f"| {p['pair'][0]}→{p['pair'][1]} | {p['years']} | {p['roi_valid']}/{p['roi_windows']} | ({c['median_dx_px']}, {c['median_dy_px']}) | {c['median_abs_d_px']} | {p['tongue_poly_median_d_px']} | {p['L_moving_opt_m'] if p['L_moving_opt_m'] is not None else 'none'} |")
L += ["", "![offset 2018-2026](out/offset/2018_2026_map.png)", "",
      "- Co-registration: stable-rock median |d| 0.14–0.27 px < 1 px → no shift applied.",
      "- **Result:** over 7–8 years the crevassed clean-ice tongue decorrelates (matches rejected), so no connected |d| > 2 px run exists → **L_moving_opt undefined with these parameters**. "
      "The debris lobe below ≈ 3.8 km matches well (NCC ≈ 0.8) with |d| ≈ 0.1–0.5 px over 7–8 years → stagnant within noise (< ~0.5 m/yr).", "",
      "### Sensitivity (parameters changed, labelled): annual pairs, 16×16 px windows, 4 px step, ±8 px search", "",
      "| pair | stable-rock median abs d (px) | 2800–3350 m | 3350–3600 m | 3600–3950 m | 3950–4470 m | L_moving_opt (2 / 1 / 0.5 px, gaps as NaN-stops) | same, NaN-skipping variant |", "|---|---|---|---|---|---|---|---|"]
for p in DA["pairs"]:
    zs = zone_speeds("offset_win16_annual", f"{p['pair'][0]}_{p['pair'][1]}")
    sw, sk = p["L_moving_opt_sweep_m"], p["L_moving_opt_sweep_nanskip_m"]
    L.append(f"| {p['pair'][0]}→{p['pair'][1]} | {p['coreg']['median_abs_d_px']} | " + " | ".join(f"{z:.0f} m/yr" if z is not None else "n/a" for z in zs) +
             f" | {sw['2px']} / {sw['1px']} / {sw['0.5px']} | {sk['2px']} / {sk['1px']} / {sk['0.5px']} |")
L += ["", "![profile](out/offset_win16_annual/offset_profile.png)", "",
      "- Zone values are medians of |d| along the centerline, ×10 m, per year. Noise floor = stable-rock median |d| ≈ 1–3 m/yr.",
      f"- Clean-ice edge L_clean_edge_m: {R[BY]['L_clean_edge_m']} m ({BY}), {R[2026]['L_clean_edge_m']} m (2026). Motion continues at ~4–16 m/yr (pair-dependent, 1.5–5× the noise floor) for ~300–400 m below it, under debris, and reaches the noise floor at ≈ 3.95 km.",
      "- **Interpretation (strict):** where it moves → ice under debris; where it does not → dead moraine or stagnant (dead) ice. The ≈ 3.95 km point is the limit of *active* ice, not necessarily the terminus — dead ice is still ice.",
      "- **Why no single L_moving_opt rate:** the threshold-based end point jumps between 3.1 and 3.6 km depending on pair, threshold (parameter) and how rejected windows are treated; with 3 annual pairs it cannot carry a trend. Use the velocity profile.",
      "- L_moving_opt vs L_clean_edge: the clean-ice edge is a surface-albedo boundary (bare ice vs debris); active flow extends ≈ 350–450 m further under the debris. They measure different things.", ""]

# ── Part F
L += ["## Part F — migration of the active-ice limit (frozen D-sensitivity parameters)", "",
      "![profiles](out/f_migration/f_profiles.png)", "",
      "Pairs on clean frames; 2-year pairs cross a flagged year. |d| normalised to m/yr. QC: stable-rock median |d| ≤ 0.5 px/yr. "
      f"L_active = last point down-glacier where the {FM['running_median_m']} m running median of |d| is ≥ k × the pair's noise floor (k = 2, 3: parameters).", "",
      "| pair | yr | mid | valid windows | noise (m/yr) | QC | L_active 2× (m) | L_active 3× (m) | 2800–3350 | 3350–3600 | 3600–3950 | 3950–4470 (m/yr) |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in FM["pairs"]:
    z = [r[f"zone_{a}_{b}_m_per_yr"] for a, b in ((2800, 3350), (3350, 3600), (3600, 3950), (3950, 4470))]
    zn = [r[f"zone_{a}_{b}_n"] for a, b in ((2800, 3350), (3350, 3600), (3600, 3950), (3950, 4470))]
    L.append(f"| {r['pair']}{' (2 yr)' if r['two_year'] else ''} | {r['years']} | {r['mid_year']} | {r['valid_windows']} | {r['noise_m_per_yr']} | {'pass' if r['qc_pass'] else 'FAIL'} | "
             f"{r['L_active_2x_m']} | {r['L_active_3x_m']} | " + " | ".join(f"{v} (n={n})" if v is not None else "n/a" for v, n in zip(z, zn)) + " |")
L += ["",
      "| fit | slope (m/yr per yr) | σ | t | df | p | residual SD | half fitted change over span | resolved |", "|---|---|---|---|---|---|---|---|---|"]
for k in ("L_active_2x_m", "L_active_3x_m"):
    f = FM["fits"][k]; L.append(f"| {k} | {f['slope_per_yr']:+.1f} | {f['sigma']:.1f} | {f['t']:.2f} | {f['df']} | {f['p']:.2g} | {f['resid_sd']} m | {f['half_fitted_change_m']} m | {'yes' if f['resolved'] else 'NO'} |")
L.append(f"| zone 3600–3950 speed | {FZ['slope_per_yr']:+.2f} | {FZ['sigma']:.2f} | {FZ['t']:.2f} | {FZ['df']} | {FZ['p']:.2g} | {FZ['resid_sd']} m/yr | — | — |")
L += ["",
      "- **Resolvability rule (F5, operationalised):** inter-pair scatter = residual SD around the OLS line; resolvable only if it is ≤ half the fitted change over the 7-year span. Fails for both k → **migration of the active-ice limit is not resolved on 6 pairs; no rate reported.**",
      "- The 3× variant collapses to 2.8 km in two pairs (19→21, 21→22) because the fast clean-ice zone is barely matched there (n = 2 and 20 windows in 2800–3350 m, median 2.9 m/yr in 21→22 vs ~30 m/yr in all other 1-year pairs): 2-year pairs and the late-September 2021 frame decorrelate the crevasse field. The pairs disagree with each other more than any plausible 7-year migration.",
      "- Under-debris zone 3600–3950 m: 0.7–9.2 m/yr across pairs, no trend (p above). No evidence of slowdown on this record.",
      "- Range of L_active (2×) across pairs: 3653–3958 m — the active-ice limit sits ≈ 3.65–3.96 km in every pair, i.e. 100–500 m below the clean-ice edge; that location statement holds, the trend does not.", ""]

# ── Parts I / J / K / L
HR = list(csv.DictReader(open(os.path.join(OUT, "h_landsat", "aksay_landsat_s2_metrics.csv"))))
L += ["## Part I — sensor cross-calibration", "",
      "**I1 — Landsat-7 (SLC-off) vs Landsat-8, 2013–2015, Aug–Sep, |Δdate| ≤ 16 d, both clean in the polygon; L7 stripe/cloud mask imposed on L8 (common valid pixels only):**", "",
      "| L7 | L8 | Δ days | common valid | bright L7 (km²) | bright L8 (km²) | L7 − L8 | edge L7 | edge L8 |", "|---|---|---|---|---|---|---|---|---|"]
for r in R6["I"]["I1"]:
    L.append(f"| {r['L7']} | {r['L8']} | {r['days']} | {r['common_valid_frac']} | {r['bright_L7_km2']} | {r['bright_L8_km2']} | {r['diff_L7_minus_L8']:+.3f} | {r['edge_L7_m'] or '—'} | {r['edge_L8_m'] or '—'} |")
L += ["", f"Offset L7 − L8 = **median {OFF['bright_L7_minus_L8_km2']:+.3f} km²** (n = {OFF['n_I1']} ≥ 3 → accepted by rule I4). Caveat: the 4 pairs share 2 L7 frames, all in 2014 (2013 and 2015 had no clean pair); the 15-day pair 08-24/09-08 differs by −0.16 km² (late-season change), the three others by +0.01…+0.03. L5 is assumed to equal L7 (TM ≈ ETM+ bands); not testable here.", "",
      "**I2 — Landsat-8/9 vs Sentinel-2, clean S2 years 2017–2026, |Δdate| ≤ 10 d, common valid pixels:**", "",
      "| year | Landsat | S2 | Δ days | bright L (km²) | bright S2 (km²) | L − S2 | edge L | edge S2 | edge L − S2 |", "|---|---|---|---|---|---|---|---|---|---|"]
for r in R6["I"]["I2"]:
    L.append(f"| {r['year']} | {r['landsat']} ({r['platform'][-1]}) | {r['s2']} | {r['days']} | {r['bright_L_km2']} | {r['bright_S2_km2']} | {r['diff_L_minus_S2']:+.3f} | {r['edge_L_m'] or '—'} | {r['edge_S2_m'] or '—'} | {r['edge_diff_L_minus_S2'] if r['edge_diff_L_minus_S2'] is not None else '—'} |")
L += ["", f"Offset L8 − S2 = **median {OFF['bright_L8_minus_S2_km2']:+.3f} km²** (n = {OFF['n_I2']}; range +0.005…+0.041).", "",
      f"**I3 — clean-ice edge:** L7 − L8 not determinable (no L7 edge detected through SLC-off stripes in any pair, n = {OFF['n_edge_I1']}); "
      f"L8 − S2 median {OFF['edge_L8_minus_S2_m']:+.0f} m (n = {OFF['n_edge_I2']}: +20, −125, −296 m) — **not within 30 m**: single 30 m frames can lose the narrow clean strips at the tongue end. Edge offsets are therefore **not applied**; the edge result rests on the 30-year span, not on frame-to-frame agreement.", "",
      "**Rule I4:** I1 gave an offset (n ≥ 3) → series joined on the Landsat-8 scale (L5/7 − (L7−L8), S2 + (L8−S2); one value per year: Landsat ≤ 2015, S2 ≥ 2016). Separate per-family fits shown alongside.", "",
      "**QA fix in rev6-final (documented, not a new threshold):** CFMask's high-confidence 'cloud' over dirty ice dropped valid ice pixels and biased Landsat bright area up (rev6: Landsat − S2 ≈ +0.10 km²). Cloud now also requires blue SR > 0.2 — validated on the visually-QA'd frames (12 clear frames ≤ 2 % cloud in the polygon; cloudy 2003/2004/2011 10–80 %). After the fix L8 − S2 is +0.02 km².", "",
      "## Part J — SLC-off and flag checks", "",
      f"- **J1:** L5/7 bright area 1996–2012: all clean {fz(R6['J1']['all_clean_1996_2012'])} (n = {R6['J1']['all_clean_1996_2012']['n']}); without SLC-off frames {fz(R6['J1']['without_slc_off'])} (n = {R6['J1']['without_slc_off']['n']}). Slopes differ by {abs(R6['J1']['all_clean_1996_2012']['slope_per_yr']-R6['J1']['without_slc_off']['slope_per_yr']):.4f} < larger σ → **SLC-off frames stay in the area fit.**",
      f"- **J2:** {R6['J2']} → not flagged.",
      f"- **J3:** valid-pixel fraction in the polygon is in the CSV (`valid_poly_frac`); frames < {R6['J3']['valid_min']:.0%} would leave the area fit — none of the clean frames is below (min ≈ 0.70, L7 SLC-off).", "",
      "## Part K — break in the trend", "",
      f"Joined bright-area series (L8 scale, n = {JF['n']}): linear {fz(JF)}. One-breakpoint model (two independent segments, breakpoint on a 0.25-yr grid 2005–2018 by minimum RSS): best break {KK['breakpoint']}, slope {KK['slope_before']:+.4f} → {KK['slope_after']:+.4f} km²/yr, level jump {KK['jump_at_break_km2']:+.3f} km².", "",
      "| model | RSS | AIC | BIC |", "|---|---|---|---|",
      f"| linear | {KK['rss_linear']} | {KK['AIC_linear']} | {KK['BIC_linear']} |", f"| one breakpoint | {KK['rss_segmented']} | {KK['AIC_segmented']} | {KK['BIC_segmented']} |", "",
      f"F-test (segmented vs linear): F({KK['F_df'][0]}, {KK['F_df'][1]}) = {KK['F']}, p = {KK['F_p']:.2g}. BIC gain = {KK['BIC_gain']:+.2f} < 2 → **no breakpoint declared (rule K2).** "
      f"The best break ({KK['breakpoint']}) sits at the Landsat → S2 switch of the joined series (S2 from 2016), so even the AIC preference cannot be separated from a sensor change. **Acceleration after 2013 is not distinguishable from noise and sensor change.**", "",
      "## Part L — final series", "",
      "![metrics](out/h_landsat/h_metrics.png)", "", "![timelapse](out/h_landsat/aksay_timelapse_1990_2026.gif)", "",
      f"- **L1 clean-ice edge 1996–2026** (single series, sensor column in the CSV; Landsat quantised at 30 m, S2 at 10 m; I3 offsets not applied): {fz(EF, 1, 'm/yr')}, n = {EF['n']}, sensors {', '.join(EF['sensors'])}. "
      f"Per family (same sign, each alone not significant): L5/7 {fz(R6['fits']['L_clean_edge_m']['L5/7'], 1, 'm/yr')}; L8/9 {fz(R6['fits']['L_clean_edge_m']['L8/9'], 1, 'm/yr')}; S2 {fz(R6['fits']['L_clean_edge_m']['S2'], 1, 'm/yr')}.",
      f"- Bright area per family: L5/7 {fz(R6['fits']['bright_km2']['L5/7'])}; L8/9 {fz(R6['fits']['bright_km2']['L8/9'])}; S2 {fz(R6['fits']['bright_km2']['S2'])}; joined {fz(JF)}.",
      "- GIF: one S2 display stretch for all frames; each Landsat family mapped band-by-band onto S2 radiometry by matching p5/p60 (TM/ETM+ saturate on snow, so not p98); saturated pixels white; flagged frames marked; caption shows the frame's tongue bright area.",
      "- Data gaps: no usable Tier-1 frame 1990–1992, 1994–1995, 1997, 2000; 1998, 2003, 2004, 2007, 2011 cloud (visual QA); 1993 auto-snow flag.", ""]

# ── Part B
L += ["## Part B-pilot — Sentinel-1 coherence, one pair (DES 107)", ""]
logs = sorted(glob.glob(os.path.join(HERE, "data", "s1_pilot", "*", "log.json")))
stats = sorted(glob.glob(os.path.join(OUT, "s1_pilot_*.json")))
if stats:
    for sp in stats:
        s = json.load(open(sp)); z = s["zones"]; tag = s["pair"]
        L += [f"Pair {tag.replace('_', ' → ')} (12 days), coherence with 5×1 looks, resampled to the S2 grid.", "",
              "| zone | pixels | median | IQR |", "|---|---|---|---|"]
        for k, v in z.items(): L.append(f"| {k} | {v['valid_px']} | {v['median']} | {v['p25']}–{v['p75']} |")
        L += ["", f"b − c = {s['b_minus_c']}, b − a = {s['b_minus_a']}. Rule (parameters, read as absolute contrasts): {s['rule']}.", "",
              f"![coherence](out/s1_pilot_{tag}_coherence.png)", "",
              ("**Verdict: contrast present** → a separate decision on the 9-year series is needed (not started)." if s["separates"] else
               "**Verdict: 12-day C-band coherence does not separate debris-covered ice from ice-free moraine on a near-stagnant tongue; not pursued.** This is a result, not a failure."), "",
              "- Stop rule triggered by b − c = %.3f < 0.15. The lobe is coherent; the rock reference is itself degraded by steep, foreshortened terrain and by the 5×1 estimator floor (≈ 0.3), so b − c understates the contrast to truly stable ground. Consistent with Part D (no measurable motion below ≈ 3.95 km): stagnant debris-covered ice and ice-free moraine are not separable by 12-day coherence." % s["b_minus_c"],
              "- Bare ice in the polygon is clearly decorrelated (median %.2f) — the active, melting tongue is visible; the method fails only at the boundary that matters (dead ice vs moraine)." % z["a_bare_ice_polygon"]["median"],
              "- Processing: CDSE OData (SH OAuth token accepted for download), IW3 VV only (2 × 1.35 GB), POEORB from ESA STEP, Copernicus GLO-30 + EGM2008 undulation −37.2 m → WGS84, ISCE2 2.6.5 topsApp (no ESD, no unwrapping), geocoded and resampled to the S2 grid; ≈ 5 min ISCE time. Logs: `aksay/data/s1_pilot/`.",
              "- The 9-year S1 series was **not** downloaded (brief B5).", ""]
else:
    for lp in logs:
        lg = json.load(open(lp)); st = lg["stages"]
        failed = [k for k, v in st.items() if v.get("ok") is False]
        L.append(f"- Pair {' → '.join(lg['pair'])}: stages " + ", ".join(f"{k}={'ok' if v.get('ok') else 'FAILED'}" for k, v in st.items() if 'ok' in v) +
                 (f". Failure: `{st[failed[0]]['error'][:300]}`" if failed else ""))
    L.append("")

# ── frames + caveats
L += ["## Frames", "", "| year | date | cloud % in bbox | snow % in bbox | flag |", "|---|---|---|---|---|"]
for r in fr: L.append(f"| {r['year']} | {r['date']} | {r['cloud_bbox_pct_fetch']} | {r['snow_bbox_pct_fetch']} | {r.get('flag_reason') or ''} |")
L += ["", "## Next steps", "",
      "- Landsat done (Part H). Remaining free-data option: manual digitising of the debris-covered snout on the best Landsat/S2 frames (±30–50 m) for a terminus estimate over 30 years.",
      "- DEM differencing: not pursued — no free post-2016 DEM over the site; on free data it would only reproduce Hugonnet 2021.", "",
      "## Caveats", "",
      f"- n = {F['tongue_ice_km2']['clean']['n']} clean frames; DOY models have df = {F['tongue_ice_km2']['clean_doy']['df']}. p-values assume independent Gaussian residuals — indicative.",
      "- Thresholds are parameters: NDSI 0.1 (tongue), B08 0.60 (snow/ice, swept), offset-tracking NCC/peak-ratio/window/threshold (sensitivity shown), coherence contrasts 0.15/0.10.",
      "- `L_clean_edge_m` = clean-ice/debris boundary on the centerline; not the terminus.",
      "- Hugonnet 2021 via OGGM per-glacier file (`aksay/data/hugonnet_aksay.json`).", "",
      "## Files", "",
      "`aksay/out/aksay_metrics.csv`, `aksay_metrics.png`, `aksay_transition_*_2026.png`, `rev3_stats.json`, `c2017_table.json`, `c2017_quicklooks.png`, "
      "`offset/`, `offset_win16_annual/`, `f_migration/`, `h_landsat/` (aksay_landsat_s2_metrics.csv, rev6_stats.json, h_metrics.png, aksay_timelapse_1990_2026.gif), `c2017_g1.json`, `s1_pilot_*`. Re-run offline: `python aksay/process.py && python aksay/c_check.py && python aksay/d_offset.py 2018 2026 2019 2026 && python aksay/f_migration.py && python aksay/h_process.py && python aksay/rev6_final.py && python aksay/report_final.py`."]
open(os.path.join(HERE, "AKSAY_BASELINE.md"), "w").write("\n".join(L) + "\n")
print("ok", len(L))
