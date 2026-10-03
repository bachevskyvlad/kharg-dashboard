"""
aksay/b_pilot_stats.py — rev4 B3/B4 (offline): coherence medians in three zones + stop rule + map.
Usage: python aksay/b_pilot_stats.py 2026-08-06_2026-08-18
Zones (fetch grid, frozen geometry):
  (a) bare ice in the tongue polygon (S2 class 'ice' of the pair's year)
  (b) debris lobe below the polygon: along-centerline s ∈ (polygon end, +1000 m], |n| ≤ 300 m, NDSI ≤ 0.1
  (c) valley-wall rock: > 500 m from the centerline, NDSI < 0, inside the display crop
Stop rule (brief B4, read as absolute contrasts): |b − c| < 0.15 or |b − a| < 0.10 → method does not separate.
"""
import os, sys, json
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TAG = sys.argv[1]; sys.argv = sys.argv[:1]
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import process as P
import d_offset as DO

THR_BC, THR_BA = 0.15, 0.10
year = int(TAG[:4])
src = os.path.join(P.DATA, "s1_pilot", TAG, "coh_s2grid.npz")
z = dict(np.load(src))
coh = z["topophase_cor"].astype(np.float32)
d = DO.load(year); nd = P.ndsi(d); nir = d["b08"] / 1e4
tg = json.load(open(P.TONGUE)); TP = P.poly_mask(tg["polygon"])
cl_m, cl_d = DO.centerline(); S, N, Dd = DO.s_n_grid(cl_m, cl_d, 1200)
send = S[TP].max()
sl = P.crop_slices(json.load(open(P.GEOM))); incrop = np.zeros_like(TP); incrop[sl] = True
zones = {
    "a_bare_ice_polygon": TP & (nd > tg["ndsi_threshold"]) & (nir < P.B08_SNOW),
    "b_debris_lobe": (S > send) & (S <= send + 1000) & (np.abs(N) <= 300) & (nd <= 0.1),
    "c_valley_rock": incrop & (Dd > 500) & (nd < 0),
}
res = {"pair": TAG, "zones": {}}
for k, m in zones.items():
    v = coh[m & np.isfinite(coh)]
    res["zones"][k] = {"px": int(m.sum()), "valid_px": int(v.size),
                       "median": round(float(np.median(v)), 3) if v.size else None,
                       "p25": round(float(np.percentile(v, 25)), 3) if v.size else None,
                       "p75": round(float(np.percentile(v, 75)), 3) if v.size else None}
a, b, c = (res["zones"][k]["median"] for k in zones)
res["b_minus_c"] = round(b - c, 3); res["b_minus_a"] = round(b - a, 3)
res["separates"] = bool(abs(b - c) >= THR_BC and abs(b - a) >= THR_BA)
res["rule"] = f"|b−c| ≥ {THR_BC} and |b−a| ≥ {THR_BA} (parameters)"
if "topophase_cor_band" in z: res["coh_band"] = int(z["topophase_cor_band"])
json.dump(res, open(os.path.join(P.OUT, f"s1_pilot_{TAG}.json"), "w"), indent=1, ensure_ascii=False)
print(json.dumps(res, indent=1, ensure_ascii=False))

# map
y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
cc = np.nan_to_num(coh[y0:y1, x0:x1], nan=0)
ramp = np.array([[13, 54, 107], [28, 92, 171], [57, 135, 229], [134, 182, 239], [205, 226, 251]], float)   # low → high coherence
t = np.clip(cc, 0, 1) * (len(ramp) - 1); k = np.clip(t.astype(int), 0, len(ramp) - 2); f = (t - k)[..., None]
rgb = ramp[k] + (ramp[k + 1] - ramp[k]) * f
K = 2
I = Image.fromarray(rgb.astype(np.uint8)).resize(((x1 - x0) * K, (y1 - y0) * K), Image.NEAREST)
dr = ImageDraw.Draw(I)
def outline(m, col):
    e = m & ~(np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1))
    ys, xs = np.nonzero(e[y0:y1, x0:x1])
    for yy, xx in zip(ys, xs): dr.rectangle([xx * K, yy * K, xx * K + K - 1, yy * K + K - 1], fill=col)
outline(TP, (255, 255, 255)); outline(zones["b_debris_lobe"], (235, 104, 52))
dr.line([((P.ll2px(*q)[0] - x0 + .5) * K, (P.ll2px(*q)[1] - y0 + .5) * K) for q in json.load(open(P.GEOM))["centerline"]], fill=(255, 214, 0), width=2)
W = I.width + 320; can = Image.new("RGB", (W, I.height), (252, 252, 251)); can.paste(I, (0, 0)); dr = ImageDraw.Draw(can)
fb, fr = ImageFont.truetype(P.FONT_B, 16), ImageFont.truetype(P.FONT_R, 13)
dr.text((I.width + 14, 12), f"S1 DES 107 coherence {TAG.replace('_', ' → ')}", font=fb, fill=(11, 11, 11))
for i in range(len(ramp)): dr.rectangle([I.width + 14 + i * 40, 44, I.width + 52 + i * 40, 60], fill=tuple(int(v) for v in ramp[i]))
dr.text((I.width + 14, 64), "coherence 0 → 1 (looks 5×1)", font=fr, fill=(82, 81, 78))
lines = [f"(a) bare ice, polygon: {a}", f"(b) debris lobe: {b}", f"(c) valley rock: {c}", f"b − c = {res['b_minus_c']}, b − a = {res['b_minus_a']}",
         "separates: " + ("YES" if res["separates"] else "NO"), "", "white: tongue polygon (frozen v1)", "orange: zone (b) debris lobe", "yellow: centerline"]
for i, t in enumerate(lines): dr.text((I.width + 14, 94 + 20 * i), t, font=fr, fill=(11, 11, 11) if i < 5 else (82, 81, 78))
can.save(os.path.join(P.OUT, f"s1_pilot_{TAG}_coherence.png"))
