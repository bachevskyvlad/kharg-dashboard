"""
jask_esri_measure.py — ESRI z=18 mosaic crop + 16-beam overlay for all 20 Jask tanks.

Outputs
-------
  diagnostics/jask/esri/  jk_0.png … jk_19.png    individual crops
  diagnostics/jask/esri/  uncertain_atlas.png       4-column grid of jk_8–jk_19
  diagnostics/jask/esri/  confirmed_atlas.png       4-column grid of jk_0–jk_7
  diagnostics/jask/       JASK_ESRI_MEASURE.md      measurement table (radii + flags TBD)

The 16-beam overlay (radial lines at 22.5°) lets the reviewer measure radii visually.
Yellow ring = current registry radius (35m / r_fit if set).
Flags that must be assigned by the reviewer for uncertain tanks:
  ok                — tank visible, operational or complete
  no_tank           — no structure at coordinates
  under_construction — pad or partial structure visible, not yet operational

Usage:
  cd scripts
  python jask_esri_measure.py
"""

import math, io, time
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

ZOOM       = 18
CROP_DIAMS = 5     # canvas half-width = CROP_DIAMS × diameter
TILE_SIZE  = 256
ESRI_URL   = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
N_BEAMS    = 16

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "jask" / "esri"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# All 20 Jask tanks
CONFIRMED = [
    {"id": "jk_0", "lat": 25.854547959796683, "lon": 57.30425500256653, "r_reg": 35},
    {"id": "jk_1", "lat": 25.854285897427793, "lon": 57.30285809909571, "r_reg": 35},
    {"id": "jk_2", "lat": 25.853004240288325, "lon": 57.303153860416884,"r_reg": 35},
    {"id": "jk_3", "lat": 25.853270400262147, "lon": 57.30455531406189, "r_reg": 35},
    {"id": "jk_4", "lat": 25.861242540725943, "lon": 57.30140013397687, "r_reg": 35},
    {"id": "jk_5", "lat": 25.859992238216282, "lon": 57.301668785557,   "r_reg": 35},
    {"id": "jk_6", "lat": 25.860219021790332, "lon": 57.30306060943653, "r_reg": 35},
    {"id": "jk_7", "lat": 25.861503877842335, "lon": 57.30277881775879, "r_reg": 35},
]
UNCERTAIN = [
    {"id": "jk_8",  "lat": 25.853687716748603, "lon": 57.30698588912281, "r_reg": 35},
    {"id": "jk_9",  "lat": 25.853928243776394, "lon": 57.30841264827629, "r_reg": 35},
    {"id": "jk_10", "lat": 25.855236751552415, "lon": 57.30810298608718, "r_reg": 35},
    {"id": "jk_11", "lat": 25.854958644693784, "lon": 57.30673002892039, "r_reg": 35},
    {"id": "jk_12", "lat": 25.861916665340250, "lon": 57.305243449053705,"r_reg": 35},
    {"id": "jk_13", "lat": 25.862202369924490, "lon": 57.30664059485674, "r_reg": 35},
    {"id": "jk_14", "lat": 25.860673586711076, "lon": 57.305524427309486,"r_reg": 35},
    {"id": "jk_15", "lat": 25.860904481636084, "lon": 57.30691674803369, "r_reg": 35},
    {"id": "jk_16", "lat": 25.858441643613652, "lon": 57.30597802808436, "r_reg": 35},
    {"id": "jk_17", "lat": 25.858705470577710, "lon": 57.30737158935134, "r_reg": 35},
    {"id": "jk_18", "lat": 25.857166423062460, "lon": 57.306251980766476,"r_reg": 35},
    {"id": "jk_19", "lat": 25.857398072650852, "lon": 57.30766780088818, "r_reg": 35},
]
ALL_TANKS = CONFIRMED + UNCERTAIN


# ── tile helpers ──────────────────────────────────────────────────────────────

def deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lr = math.radians(lat)
    y = int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n)
    return x, y


def tile2deg_nw(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lr = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lr), lon


def meters_per_pixel(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def fetch_tile(z, x, y, session):
    url = ESRI_URL.format(z=z, x=x, y=y)
    for _ in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            time.sleep(1.5)
    return Image.new("RGB", (TILE_SIZE, TILE_SIZE), (30, 30, 30))


# ── crop + overlay ────────────────────────────────────────────────────────────

def make_crop(tank, session, uncertain=False):
    lat, lon, r_reg = tank["lat"], tank["lon"], tank["r_reg"]
    tid = tank["id"]

    mpp      = meters_per_pixel(lat, ZOOM)
    r_px     = r_reg / mpp
    half_px  = int(CROP_DIAMS * r_reg / mpp)
    window   = 2 * half_px

    tx_c, ty_c = deg2tile(lat, lon, ZOOM)
    lat_nw, lon_nw = tile2deg_nw(tx_c, ty_c, ZOOM)

    cy_in = int((lat_nw - lat) * 111320.0 / mpp)
    cx_in = int((lon - lon_nw) * 111320.0 * math.cos(math.radians(lat)) / mpp)

    tiles_n = math.ceil((half_px + TILE_SIZE) / TILE_SIZE)
    cs = (2 * tiles_n + 1) * TILE_SIZE
    canvas = Image.new("RGB", (cs, cs))
    for dy in range(-tiles_n, tiles_n + 1):
        for dx in range(-tiles_n, tiles_n + 1):
            t = fetch_tile(ZOOM, tx_c + dx, ty_c + dy, session)
            canvas.paste(t, ((dx + tiles_n) * TILE_SIZE, (dy + tiles_n) * TILE_SIZE))

    cx_abs = tiles_n * TILE_SIZE + cx_in
    cy_abs = tiles_n * TILE_SIZE + cy_in
    crop = canvas.crop((cx_abs - half_px, cy_abs - half_px,
                        cx_abs + half_px, cy_abs + half_px))
    if crop.size != (window, window):
        crop = crop.resize((window, window))

    draw = ImageDraw.Draw(crop)
    H = W = window
    cx = cy = H // 2
    lw = max(2, int(r_px * 0.06))

    # registry circle (yellow)
    draw.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px],
                 outline=(255, 220, 0), width=lw)

    # 16 radial beams (cyan, short tick at rim)
    beam_inner = r_px * 0.65
    beam_outer = r_px * 1.35
    for i in range(N_BEAMS):
        a = math.radians(i * 360 / N_BEAMS)
        xi = cx + beam_inner * math.sin(a)
        yi = cy - beam_inner * math.cos(a)
        xo = cx + beam_outer * math.sin(a)
        yo = cy - beam_outer * math.cos(a)
        draw.line([xi, yi, xo, yo], fill=(0, 220, 255), width=max(1, lw // 2))

    # scale bar: 20m
    bar_px = int(20 / mpp)
    bx0, by0 = 12, H - 20
    draw.rectangle([bx0, by0 - 3, bx0 + bar_px, by0 + 3], fill=(255, 255, 255))

    # label
    label = tid + ("  [UNCERTAIN]" if uncertain else "  [confirmed]")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 8), label, fill=(255, 220, 0), font=font)
    draw.text((10, 30), f"r_reg={r_reg}m  z=18  mpp={mpp:.2f}m", fill=(200, 200, 200), font=font)

    return crop


def make_atlas(tanks, session, uncertain_flags, cols=4, label_prefix=""):
    """Compose a grid of crops."""
    if not tanks:
        return None
    crops = []
    for t in tanks:
        unc = t.get("id", "").startswith("jk_") and int(t["id"].split("_")[1]) >= 8
        c = make_crop(t, session, uncertain=unc)
        crops.append(c)

    tile_w, tile_h = crops[0].size
    rows = math.ceil(len(crops) / cols)
    atlas = Image.new("RGB", (cols * tile_w, rows * tile_h), (20, 20, 20))
    for i, c in enumerate(crops):
        r, col = divmod(i, cols)
        atlas.paste(c, (col * tile_w, r * tile_h))
    return atlas


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-diagnostic"

    print(f"Fetching ESRI z={ZOOM} crops for {len(ALL_TANKS)} Jask tanks …")

    # individual crops
    for t in ALL_TANKS:
        tid = t["id"]
        unc = int(tid.split("_")[1]) >= 8
        print(f"  {tid} ({'uncertain' if unc else 'confirmed'}) … ", end="", flush=True)
        crop = make_crop(t, session, uncertain=unc)
        out = OUT_DIR / f"{tid}.png"
        crop.save(str(out))
        print(f"saved ({crop.size[0]}×{crop.size[1]}px)")

    # atlases
    print("\nBuilding confirmed atlas (jk_0–jk_7) …")
    atlas_conf = make_atlas(CONFIRMED, session, {})
    if atlas_conf:
        atlas_conf.save(str(OUT_DIR / "confirmed_atlas.png"))
        print(f"  saved: confirmed_atlas.png  {atlas_conf.size}")

    print("Building uncertain atlas (jk_8–jk_19) …")
    atlas_unc = make_atlas(UNCERTAIN, session, {})
    if atlas_unc:
        atlas_unc.save(str(OUT_DIR / "uncertain_atlas.png"))
        print(f"  saved: uncertain_atlas.png  {atlas_unc.size}")

    # measurement table (flags = TBD, user fills after visual inspection)
    md_lines = [
        "# Jask ESRI z=18 Measurement — Radius + Flag Table",
        "",
        "Run: " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "",
        "## Confirmed tanks (jk_0–jk_7)",
        "",
        "| Tank | lat | lon | r_reg (m) | r_fit (m) | flag |",
        "|---|---|---|---|---|---|",
    ]
    for t in CONFIRMED:
        md_lines.append(f"| {t['id']} | {t['lat']:.6f} | {t['lon']:.6f} | {t['r_reg']} | TBD | ok |")

    md_lines += [
        "",
        "## Uncertain tanks (jk_8–jk_19)",
        "",
        "| Tank | lat | lon | r_reg (m) | r_fit (m) | flag |",
        "|---|---|---|---|---|---|",
        "| *(r_fit and flag filled after visual inspection of crops)* | | | | | |",
    ]
    for t in UNCERTAIN:
        md_lines.append(f"| {t['id']} | {t['lat']:.6f} | {t['lon']:.6f} | {t['r_reg']} | TBD | TBD |")

    md_lines += [
        "",
        "**Flag values**: `ok` / `no_tank` / `under_construction`",
        "",
        "Images: `diagnostics/jask/esri/`",
        "Atlas (uncertain): `diagnostics/jask/esri/uncertain_atlas.png`",
        "Atlas (confirmed): `diagnostics/jask/esri/confirmed_atlas.png`",
    ]

    md_path = OUT_DIR.parent / "JASK_ESRI_MEASURE.md"
    md_path.write_text("\n".join(md_lines))
    print(f"\nWritten: {md_path}")
    print("Done.")


if __name__ == "__main__":
    main()
