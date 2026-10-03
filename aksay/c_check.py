"""aksay/c_check.py — rev4 Part C (offline): September 2017 base-year check from aksay/data/c2017/."""
import os, sys, json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = sys.argv[:1]
import process as P

D = os.path.join(P.DATA, "c2017"); idx = json.load(open(os.path.join(D, "index.json")))
x0, y0, x1, y1 = idx["crop_px_fetchgrid"]
TP = P.poly_mask(json.load(open(P.TONGUE))["polygon"])[y0:y1, x0:x1]
lo, hi = json.load(open(os.path.join(P.OUT, "run_meta.json")))["stretch_p2_p98"]
rows, tiles = [], []
for r in idx["dates"]:
    f = os.path.join(D, f"{r['date']}.npz")
    if not os.path.exists(f): continue
    d = dict(np.load(f)); n = d["b08"] / 1e4; nd = P.ndsi(d)
    cl = np.isin(d["scl"], [3, 8, 9, 10]); ok = TP & (d["mask"] > 0) & ~cl
    br = ok & (nd > 0.1)
    row = {"date": r["date"], "tile_cc": round(r["tile_cc"], 1),
           "cloud_bbox_pct": round(100 * r["cloud"], 1) if "cloud" in r else None,
           "snow_bbox_pct": round(100 * r["snow"], 1) if "snow" in r else None,
           "cloud_polygon_pct": round(100 * float((cl & TP).sum() / TP.sum()), 1),
           "b08_median_polygon": round(float(np.median(n[ok])), 3) if ok.any() else None,
           "b08_median_bright_polygon": round(float(np.median(n[br])), 3) if br.any() else None,
           "b08_ge_050_km2": round(float((br & (n >= 0.5)).sum() * P.PXW * P.PXH / 1e6), 3),
           "b08_ge_060_km2": round(float((br & (n >= 0.6)).sum() * P.PXW * P.PXH / 1e6), 3),
           "scl_snow_polygon_pct": round(100 * float(((d["scl"] == 11) & ok).sum() / max(1, ok.sum())), 1)}
    rows.append(row); print(row)
    if abs((np.datetime64(r["date"]) - np.datetime64("2017-09-16")).astype(int)) <= 7:
        a = np.stack([d["b04"], d["b03"], d["b02"]], -1).astype(np.float32)
        a = (np.clip((a - lo) / (hi - lo), 0, 1) ** 0.6 * 255).astype(np.uint8)
        im = Image.fromarray(a); dr = ImageDraw.Draw(im)
        ys, xs = np.nonzero(TP)
        dr.text((8, 8), r["date"], fill=(255, 255, 0), font=ImageFont.truetype(P.FONT_B, 20))
        dr.text((8, 32), f"B08 med (polygon) {row['b08_median_polygon']}", fill=(255, 255, 0), font=ImageFont.truetype(P.FONT_R, 13))
        tiles.append(im)
W = sum(t.width for t in tiles) + 8 * (len(tiles) - 1); can = Image.new("RGB", (W, tiles[0].height), (0, 0, 0)); x = 0
for t in tiles: can.paste(t, (x, 0)); x += t.width + 8
can.save(os.path.join(P.OUT, "c2017_quicklooks.png"))
json.dump(rows, open(os.path.join(P.OUT, "c2017_table.json"), "w"), indent=1)
