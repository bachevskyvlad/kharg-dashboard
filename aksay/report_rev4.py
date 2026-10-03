"""aksay/report_rev4.py — builds AKSAY_BASELINE.md (rev4) from the JSON/CSV outputs. Offline."""
import os, sys, json, csv, glob
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = sys.argv[:1]
OUT = os.path.join(HERE, "out")
J = lambda p: json.load(open(os.path.join(HERE, p)))
S = J("out/rev3_stats.json"); M = J("out/run_meta.json"); H = J("data/hugonnet_aksay.json"); C = J("out/c2017_table.json")
DS = J("out/offset/offset_summary.json"); DA = J("out/offset_win16_annual/offset_summary.json"); FL = J("aksay_flags.json")
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
L += ["# AKSAY_BASELINE — Ak-Sai glacier, Sentinel-2 2016–2026 (rev4)", "",
      "R&D mini-project (`aksay/`), independent of OilRadar. Frozen geometry: `aksay_geometry.json` v1, `aksay_tongue_polygon.json` v1 (untouched). "
      f"**Base year: {BY}** (changed from 2017 by rule C3, see Part C).", "",
      "rev4: Part C (2017 base-year check → 2017 flagged as fresh snow, base → 2018, Part A re-run), Part D (optical offset tracking), Part B-pilot (one Sentinel-1 pair).", "",
      "## Verdict", ""]
L += [f"1. **2017 was fresh snow, not bright ice.** On common clear tongue pixels B08 rose from 0.217 (2017-09-03) to 0.310 (2017-09-16) across a fully overcast 2017-09-11; no B08 ≥ 0.5 area existed on 09-03. → 2017 flagged, base year {BY}.",
      f"2. **With base {BY}, the bare-ice decline no longer depends on the snow/ice split.** Clean years + DOY: B08 0.50 → {fs(SW['0.50']['ice_clean_doy'])}; **0.60 → {fs(SW['0.60']['ice_clean_doy'])}**; 0.70 → {fs(SW['0.70']['ice_clean_doy'])}. The rev3 sensitivity was entirely the 2017 snow.",
      f"3. **Debris expansion:** {fs(F['tongue_debris_km2']['clean_doy'])} (clean + DOY); without DOY {fs(F['tongue_debris_km2']['clean'])}. {W['frac_of_base_ice_lost_to_debris']*100:.0f}% of {BY} bare ice is debris in 2026, concentrated at the lateral margins.",
      "4. **Optical offset tracking (Part D):** the clean, crevassed tongue flows at ~30 m/yr; flow drops to ~4–16 m/yr (pair-dependent) across the clean-ice/debris boundary and fades into the noise (≤ 3–4 m/yr) at ≈ 3.95 km along the centerline. The debris lobe below that is stagnant within noise (< ~0.5 m/yr over 7–8 years). A single L_moving_opt number is **not** stable — reported as a profile, not a rate.",
      f"5. **Surface darkening / debris expansion, consistent with thinning.** External check (Hugonnet 2021, RGI60-13.11414): glacier-wide dh/dt 2000–2019 = {h['dhdt']:+.2f} ± {h['err_dhdt']:.2f} m/yr — not distinguishable from zero; neither confirms nor rules out tongue thinning.",
      "6. **Sentinel-1 B-pilot (DES 107, 2026-08-06 → 08-18):** coherence bare ice 0.33, debris lobe 0.61, valley rock 0.47 → b − c = 0.13 < 0.15 → stop rule triggered, Part B closed. The lobe is as coherent as stable ground, consistent with Part D.", ""]

# ── Part C
L += ["## Part C — base-year check (September 2017)", "",
      "| date | tile CC % | cloud % (bbox, SCL) | snow % (bbox, SCL) | cloud % (polygon) | B08 median, polygon | B08 median, bright px | B08 ≥ 0.5 km² | B08 ≥ 0.6 km² |",
      "|---|---|---|---|---|---|---|---|---|"]
for c in C:
    L.append(f"| {c['date']} | {c['tile_cc']} | {c['cloud_bbox_pct']} | {c['snow_bbox_pct']} | {c['cloud_polygon_pct']} | {c['b08_median_polygon']} | {c['b08_median_bright_polygon']} | {c['b08_ge_050_km2']} | {c['b08_ge_060_km2']} |")
L += ["", "![sept 2017](out/c2017_quicklooks.png)", "",
      "- Only five S2 acquisitions in September 2017 (Sentinel-2B not yet routinely acquiring here). Within ±7 days of 09-16: 09-11 and 09-23 fully clouded over the polygon, 09-18 partly clouded/hazy (33 % of polygon).",
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
              "- Stop rule triggered by b − c = %.3f < 0.15. The lobe is *more* coherent than the valley walls (rock coherence is pulled down by steep, foreshortened slopes and by the 5×1-look estimator floor ≈ 0.3), i.e. the lobe behaves like stable ground. That agrees with Part D (no measurable motion below ≈ 3.95 km) — stagnant debris-covered ice and ice-free moraine look the same to 12-day coherence." % s["b_minus_c"],
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
L += ["", "## Caveats", "",
      f"- n = {F['tongue_ice_km2']['clean']['n']} clean frames; DOY models have df = {F['tongue_ice_km2']['clean_doy']['df']}. p-values assume independent Gaussian residuals — indicative.",
      "- Thresholds are parameters: NDSI 0.1 (tongue), B08 0.60 (snow/ice, swept), offset-tracking NCC/peak-ratio/window/threshold (sensitivity shown), coherence contrasts 0.15/0.10.",
      "- `L_clean_edge_m` = clean-ice/debris boundary on the centerline; not the terminus.",
      "- Hugonnet 2021 via OGGM per-glacier file (`aksay/data/hugonnet_aksay.json`).", "",
      "## Files", "",
      "`aksay/out/aksay_metrics.csv`, `aksay_metrics.png`, `aksay_transition_*_2026.png`, `rev3_stats.json`, `c2017_table.json`, `c2017_quicklooks.png`, "
      "`offset/`, `offset_win16_annual/`, `s1_pilot_*`. Re-run offline: `python aksay/process.py && python aksay/c_check.py && python aksay/d_offset.py 2018 2026 2019 2026 && python aksay/report_rev4.py`."]
open(os.path.join(HERE, "AKSAY_BASELINE.md"), "w").write("\n".join(L) + "\n")
print("ok", len(L))
