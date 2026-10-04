"""aksay/report_rev6.py — builds AKSAY_BASELINE.md (rev6) from the JSON/CSV outputs. Offline."""
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
L += ["# AKSAY_BASELINE — Ak-Sai glacier, Sentinel-2 2016–2026 (rev6)", "",
      "R&D mini-project (`aksay/`), independent of OilRadar. Frozen geometry: `aksay_geometry.json` v1, `aksay_tongue_polygon.json` v1 (untouched). "
      f"**Base year: {BY}** (changed from 2017 by rule C3, see Part C).", "",
      "rev6: Part H — Landsat 1993–2026 joined to the S2 series (30-year baseline). rev5: Part E (is the day-of-year covariate justified), Part G (report corrections), Part F (migration of the active-ice limit). rev4 parts C, D, B-pilot kept. Frozen D-sensitivity parameters.", "",
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
L += [f"1. **2017 was fresh snow, not bright ice.** On common clear tongue pixels B08 rose from 0.217 (2017-09-03) to 0.310 (2017-09-16) across a fully overcast 2017-09-11; no B08 ≥ 0.5 area existed on 09-03. → 2017 flagged, base year {BY}. The flag rests on an independent signal (dark before the cloudy spell, bright after), not on its effect on the fit. Caveat: the brightening is confined to ice pixels (B08 {G1['b08_median_by_2018_class']['ice']['2017-09-03']} → {G1['b08_median_by_2018_class']['ice']['2017-09-16']}); debris did not brighten ({G1['b08_median_by_2018_class']['debris']['2017-09-03']} → {G1['b08_median_by_2018_class']['debris']['2017-09-16']}) — consistent with a thin snowfall that melted off warm debris, but not proof of it.",
      f"2. **Bare-ice decline (base {BY}, B08 0.60):** {both('tongue_ice_km2')}. Without the DOY term the evidence is marginal (p ≈ 0.05); with it, strong. B08-split sweep (clean+DOY): 0.50 p = {SW['0.50']['ice_clean_doy']['p']:.2g}, 0.70 p = {SW['0.70']['ice_clean_doy']['p']:.2g} — no longer split-dependent once 2017 is out.",
      f"3. **Debris expansion:** {both('tongue_debris_km2')}. Without DOY p ≈ 0.07. {W['frac_of_base_ice_lost_to_debris']*100:.0f}% of {BY} bare ice is debris in 2026, concentrated at the lateral margins.",
      "4. **Optical offset tracking (Part D) — a snapshot of where active ice ends, not a trend (trend: Part F):** the clean, crevassed tongue flows at ~30 m/yr; flow drops to ~4–16 m/yr (pair-dependent) across the clean-ice/debris boundary and fades into the noise at ≈ 3.65–3.96 km along the centerline depending on the pair (Part F). The debris lobe below that is stagnant within noise (< ~0.5 m/yr over 7–8 years). A single L_moving_opt number is **not** stable — reported as a profile, not a rate.",
      f"5. **Surface darkening / debris expansion, consistent with thinning.** External check (Hugonnet 2021, RGI60-13.11414): glacier-wide dh/dt 2000–2019 = {h['dhdt']:+.2f} ± {h['err_dhdt']:.2f} m/yr — not distinguishable from zero; neither confirms nor rules out tongue thinning.",
      "6. **Sentinel-1 B-pilot (DES 107, 2026-08-06 → 08-18):** coherence bare ice 0.33, debris lobe 0.61, valley rock 0.47 → b − c = 0.13 < 0.15 → stop rule triggered, Part B closed. The lobe is coherent; the rock reference is itself degraded by steep terrain and the 5×1 estimator floor, so b − c understates the contrast to truly stable ground.",
      f"7. **30-year baseline (Part H, Landsat 5/7/8/9, one sensor family 1996–2026):** bare ice in the tongue polygon {HF['fits']['ice_km2']['landsat_all']['slope_per_yr']:+.4f} ± {HF['fits']['ice_km2']['landsat_all']['sigma']:.4f} km²/yr, t({HF['fits']['ice_km2']['landsat_all']['df']}) = {HF['fits']['ice_km2']['landsat_all']['t']:.2f}, p = {HF['fits']['ice_km2']['landsat_all']['p']:.2g}; clean-ice edge {HF['fits']['L_clean_edge_m']['landsat_all']['slope_per_yr']:+.1f} ± {HF['fits']['L_clean_edge_m']['landsat_all']['sigma']:.1f} m/yr, t({HF['fits']['L_clean_edge_m']['landsat_all']['df']}) = {HF['fits']['L_clean_edge_m']['landsat_all']['t']:.2f}, p = {HF['fits']['L_clean_edge_m']['landsat_all']['p']:.2g} — the same direction and size as the S2-only fits, now on 30 years. The tongue was already debris-covered in 1993–1996 (no clean-ice terminus to digitise, H7 not applicable).",
      f"8. **Migration of the active-ice limit (Part F): not resolved on 6 pairs.** L_active (2× noise) ranges {FL2['range_m']} m across pairs with residual SD {FL2['resid_sd']} m vs half the fitted change {FL2['half_fitted_change_m']} m (fit {FL2['slope_per_yr']:+.1f} ± {FL2['sigma']:.1f} m/yr, t({FL2['df']}) = {FL2['t']:.2f}, p = {FL2['p']:.2g} — not used). Speed under the debris (3600–3950 m) shows no trend: {FZ['slope_per_yr']:+.2f} ± {FZ['sigma']:.2f} m/yr per yr, t({FZ['df']}) = {FZ['t']:.2f}, p = {FZ['p']:.2g}.", ""]

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

# ── Part H
def hf(f, nd=4, unit="km²/yr"):
    return f"{f['slope_per_yr']:+.{nd}f} ± {f['sigma']:.{nd}f} {unit}, t({f['df']}) = {f['t']:.2f}, p = {f['p']:.2g} (n = {f['n']})"
HR = list(csv.DictReader(open(os.path.join(OUT, "h_landsat", "aksay_landsat_s2_metrics.csv"))))
L += ["## Part H — Landsat 1993–2026 (30-year baseline; first clean frame 1996)", "",
      "![timelapse](out/h_landsat/aksay_timelapse_1990_2026.gif)", "", "![metrics](out/h_landsat/h_metrics.png)", "",
      "**Data.** USGS Landsat Collection-2 Level-2 (Tier 1 only) via Microsoft Planetary Computer, anonymous access. Window Aug–Sep (widened to Jul 15–Oct 10 if needed), scene cloud < 70 %. "
      "Scoring inside the bbox: CFMask flags glacier ice as cloud in this terrain, so cloud = high-confidence CFMask cloud with NDSI ≤ 0.4; frames with NDSI > 0.4 on > 60 % of pixels rejected (fresh snow / saturation); "
      "same pick rule as S2, non-SLC-off L7 preferred. One frame per year on the S2 bbox at 30 m, upsampled ×3 (nearest) onto the 10 m grid → the frozen polygon and centerline are used unchanged.",
      "", "**Gaps.** 1990–1992, 1994–1995, 1997, 2000: no usable Tier-1 frame in the window (archive gaps / cloud). Visual QA removed 1998, 2003, 2004, 2007, 2011 (cloud over the tongue). "
      "L7 after 2003 has SLC-off stripes: areas are computed as fractions of valid polygon pixels × polygon area; the clean-ice edge skips no-data samples.", "",
      f"**Clean-ice edge QA (parameter):** a 30 m run that breaks before {3000} m along the centerline is a mixed-pixel/stripe failure (every S2 frame has clean ice ≥ 3.3 km) → 'edge not detected'. "
      f"Detected in {sum(1 for r in HR if r['sensor'] != 'S2' and r['L_clean_edge_m'])} of {sum(1 for r in HR if r['sensor'] != 'S2')} Landsat frames.", "",
      "| year | sensor | date | valid polygon | bright km² | ice km² | debris km² | clean-ice edge (m) | raw edge (m) | flag |", "|---|---|---|---|---|---|---|---|---|---|"]
for r in HR:
    L.append(f"| {r['year']} | {r['sensor']} | {r['date']} | {r['valid_poly_frac']} | {r['bright_km2']} | {r['ice_km2']} | {r['debris_km2']} | {r['L_clean_edge_m'] or '—'} | {r.get('L_clean_edge_raw_m') or '—'} | {r['flag']} |")
L += ["", "### Fits (clean frames; OLS on decimal year; two-sided t-test)", "",
      "| series | Landsat 1996–2015 | Landsat 1996–2026 | S2 2018–2026 | Landsat ≤ 2015 + S2 | same, with sensor offset |", "|---|---|---|---|---|---|"]
for c, nd, u in (("ice_km2", 4, "km²/yr"), ("bright_km2", 4, "km²/yr"), ("debris_km2", 4, "km²/yr"), ("L_clean_edge_m", 1, "m/yr")):
    f = HF["fits"][c]
    L.append(f"| {c} | {hf(f['landsat_1990_2015'], nd, u)} | **{hf(f['landsat_all'], nd, u)}** | {hf(f['s2_2018_2026'], nd, u)} | {hf(f['combined_landsat_le2015_plus_s2'], nd, u)} | {hf(f['combined_with_sensor_offset'], nd, u)}; offset {f['combined_with_sensor_offset']['sensor_offset']:+.3f} |")
cal = HF["calibration"]
L += ["", f"**Sensor calibration (H5)** — same-year clean pairs Landsat 8/9 vs S2, years {cal['ice_km2']['years']}: "
      f"ice L − S2 = {cal['ice_km2']['mean_L_minus_S2']:+.3f} ± {cal['ice_km2']['sd']:.3f} km² (SD), bright {cal['bright_km2']['mean_L_minus_S2']:+.3f} ± {cal['bright_km2']['sd']:.3f} km², "
      f"clean-ice edge {cal['L_clean_edge_m']['mean_L_minus_S2']:+.0f} ± {cal['L_clean_edge_m']['sd']:.0f} m (n = {cal['L_clean_edge_m']['n']}). Landsat reads ~0.1 km² more ice (mixed 30 m pixels at strip edges, different acquisition dates). "
      "There is no L8–S2 overlap before 2016, so the calibration uses 2018–2026.", "",
      "**Reading.** The headline is the single-sensor-family Landsat fit 1996–2026 (first clean frame 1996) (no cross-sensor splice): bare ice in the polygon declines and the clean-ice edge moves up-glacier at ≈ 8 m/yr (≈ 240 m over 30 years, 8 pixels at 30 m — resolvable over 25+ years as expected, not year-to-year). "
      "Naive Landsat + S2 splices inflate the trend because S2 reads less ice than Landsat; with a sensor-offset term the combined fits agree with Landsat-only. "
      "Landsat ≤ 2015 alone is weaker (fewer, noisier frames: ice p = %.2g, edge p = %.2g)." % (HF["fits"]["ice_km2"]["landsat_1990_2015"]["p"], HF["fits"]["L_clean_edge_m"]["landsat_1990_2015"]["p"]), "",
      "- **H7:** already in 1993–1996 the lower tongue is debris-covered with the same lobe below; the terminus was not clean ice in the 1990s, so there is no clean-ice front to digitise. The measurable signal is the clean-ice edge and the bare-ice area, as above.",
      "- Not a terminus or volume measurement; DOY not modelled for Landsat (acquisition dates spread Jul–Oct).", ""]

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
      "`offset/`, `offset_win16_annual/`, `f_migration/`, `h_landsat/`, `c2017_g1.json`, `s1_pilot_*`. Re-run offline: `python aksay/process.py && python aksay/c_check.py && python aksay/d_offset.py 2018 2026 2019 2026 && python aksay/f_migration.py && python aksay/h_process.py && python aksay/report_rev6.py`."]
open(os.path.join(HERE, "AKSAY_BASELINE.md"), "w").write("\n".join(L) + "\n")
print("ok", len(L))
