"""ТЗ-12.3 anchorage snapshot re-renderer.

Fetches Sentinel-1 SAR for the two latest anchorage dates via Planetary Computer,
draws 3-class track annotations, uploads previous.png / latest.png + snapshots.json
to Supabase Storage bucket anchorage_snapshots.

3-class rules (by length_m):
  VLCC        >= 250 m   #f87171
  SuezAframax >= 100 m   #fbbf24
  Small       <  100 m   NOT DRAWN

Track matching: ship in previous matches ship in latest if distance < 1500 m.
Label:  "VLCC · new"            if only in latest date
        "VLCC · since {mon d}"  if matched to previous (or earlier history)
Previous panel: unmatched ships (departed) → dashed outline + "left" label.

Header: "Total N · VLCC N · SuezAframax N · Small N"
Numbers = row in anchorage_queue for that date.

Usage:
  python scripts/anchorage/tracks.py              # process 2 latest dates from DB
  python scripts/anchorage/tracks.py --incremental  # only if latest > last snapshot date
"""
import os, sys, json, math, io, argparse
from datetime import datetime, date as date_t

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

SB_URL = os.environ.get('SUPABASE_URL', '')
SB_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
BUCKET = 'anchorage_snapshots'

# Kharg anchorage frame (orbit-35, same as tz12_2_test.py)
BBOX_LON_MIN, BBOX_LAT_MIN = 50.25, 29.08
BBOX_LON_MAX, BBOX_LAT_MAX = 50.52, 29.33

# 3-class colours (must match dashboard.html AQ_COLORS)
CLASS_COLORS = {
    'vlcc':        '#f87171',
    'suezaframax': '#fbbf24',
}
CLASS_LABELS = {
    'vlcc':        'VLCC',
    'suezaframax': 'SuezAframax',
}

MATCH_DIST_M = 1500   # metres — same ship between two passes

IMG_WIDTH = 1200   # output PNG width in pixels
FONT_SIZE  = 18
LABEL_FONT_SIZE = 15
HEADER_FONT_SIZE = 22


def fatal(msg):
    print(f'FATAL: {msg}', flush=True)
    sys.exit(1)


def log(*a):
    print(*a, flush=True)


# ── Supabase helpers ──────────────────────────────────────────────────────────

def sb_headers():
    if not SB_KEY:
        fatal('SUPABASE_SERVICE_KEY is empty')
    return {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
            'Content-Type': 'application/json'}


def sb_get(path, params=''):
    r = requests.get(f'{SB_URL}/rest/v1/{path}?{params}', headers=sb_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def sb_upload(name, data, ctype='image/png'):
    r = requests.post(f'{SB_URL}/storage/v1/object/{BUCKET}/{name}',
                      data=data,
                      headers={'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
                               'Content-Type': ctype, 'x-upsert': 'true',
                               'Cache-Control': 'max-age=300'},
                      timeout=120)
    if r.status_code >= 300:
        fatal(f'storage upload {name}: {r.status_code} {r.text[:300]}')


def get_latest_snapshot_date():
    """Return date string of current latest snapshot, or None."""
    try:
        r = requests.get(f'{SB_URL}/storage/v1/object/public/{BUCKET}/snapshots.json',
                         timeout=15)
        if r.status_code == 200:
            return r.json().get('latest', {}).get('date')
    except Exception:
        pass
    return None


def get_latest_dates(n=2):
    """Return the n most recent dates from anchorage_daily (3-class counts from anchorage_queue if available)."""
    rows = sb_get('anchorage_daily', 'select=date&order=date.desc&limit=5')
    dates = [r['date'] for r in rows][:n]
    if len(dates) < 2:
        fatal(f'Need at least 2 dates in anchorage_daily, got {len(dates)}')
    return sorted(dates)   # [previous, latest]


def get_queue_counts(d):
    """Return 3-class counts dict for date d from anchorage_queue, or fall back to anchorage_daily."""
    rows = sb_get('anchorage_queue', f'select=date,total,vlcc,suezaframax,small&date=eq.{d}&limit=1')
    if rows:
        r = rows[0]
        return {k: int(r.get(k) or 0) for k in ('total', 'vlcc', 'suezaframax', 'small')}
    # Fallback: map 5-class anchorage_daily to 3-class
    rows = sb_get('anchorage_daily', f'select=total,vlcc,suezmax,aframax,mr,small&date=eq.{d}&limit=1')
    if not rows:
        return {'total': 0, 'vlcc': 0, 'suezaframax': 0, 'small': 0}
    r = rows[0]
    vlcc = int(r.get('vlcc') or 0)
    saf  = int(r.get('suezmax') or 0) + int(r.get('aframax') or 0)
    sml  = int(r.get('mr') or 0) + int(r.get('small') or 0)
    return {'total': vlcc + saf + sml, 'vlcc': vlcc, 'suezaframax': saf, 'small': sml}


def get_ships(d):
    """Return list of ship dicts for date d from anchorage_ships."""
    rows = sb_get('anchorage_ships', f'select=lat,lon,length_m,class&date=eq.{d}')
    return rows


def classify(length_m):
    l = int(length_m or 0)
    if l >= 250:
        return 'vlcc'
    if l >= 100:
        return 'suezaframax'
    return 'small'


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def match_ships(prev_ships, latest_ships):
    """Return (matched_pairs, only_prev, only_latest).
    matched_pairs: list of (prev_ship, latest_ship)
    only_prev:    ships in previous not in latest (departed)
    only_latest:  ships in latest not in previous (new arrivals)
    """
    used_prev = set()
    used_lat  = set()
    pairs = []
    for li, ls in enumerate(latest_ships):
        best_d, best_pi = 1e9, -1
        for pi, ps in enumerate(prev_ships):
            if pi in used_prev:
                continue
            d = haversine_m(float(ps['lat']), float(ps['lon']),
                            float(ls['lat']), float(ls['lon']))
            if d < best_d:
                best_d, best_pi = d, pi
        if best_d <= MATCH_DIST_M:
            pairs.append((best_pi, li))
            used_prev.add(best_pi)
            used_lat.add(li)
    only_prev   = [prev_ships[i]   for i in range(len(prev_ships))   if i not in used_prev]
    only_latest = [latest_ships[i] for i in range(len(latest_ships)) if i not in used_lat]
    matched_latest = [(latest_ships[li], prev_ships[pi]) for pi, li in pairs]
    return matched_latest, only_prev, only_latest


# ── Planetary Computer SAR fetch ──────────────────────────────────────────────

def fetch_sar(target_date):
    """Fetch Sentinel-1 GRD VV band for Kharg anchorage closest to target_date.
    Returns numpy float32 array normalised to [0,1] and (lon_min, lat_min, lon_max, lat_max) of actual extent.
    """
    import pystac_client, planetary_computer, rasterio
    from rasterio.transform import from_bounds
    from rasterio.enums import Resampling
    import rasterio.warp

    pc_key = os.environ.get('PC_SDK_SUBSCRIPTION_KEY', '')
    catalog = pystac_client.Client.open(
        'https://planetarycomputer.microsoft.com/api/stac/v1',
        modifier=planetary_computer.sign_inplace,
        headers={'Ocp-Apim-Subscription-Key': pc_key} if pc_key else {}
    )

    # Search ±12 days around target date
    d = date_t.fromisoformat(target_date)
    d0 = (d.replace(day=1) if False else d).__class__.fromordinal(d.toordinal() - 12).isoformat()
    d1 = date_t.fromordinal(d.toordinal() + 12).isoformat()

    bbox = [BBOX_LON_MIN, BBOX_LAT_MIN, BBOX_LON_MAX, BBOX_LAT_MAX]
    items = list(catalog.search(
        collections=['sentinel-1-grd'],
        bbox=bbox,
        datetime=f'{d0}/{d1}',
        query={'s1:orbit_state': {'eq': 'descending'}, 's1:relative_orbit': {'eq': 35}},
    ).items())

    if not items:
        # Retry without relative orbit filter
        items = list(catalog.search(
            collections=['sentinel-1-grd'],
            bbox=bbox,
            datetime=f'{d0}/{d1}',
            query={'s1:orbit_state': {'eq': 'descending'}},
        ).items())

    if not items:
        fatal(f'No Sentinel-1 scene found near {target_date}')

    # Pick closest to target date
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    items.sort(key=lambda x: abs((datetime.strptime(x.datetime.strftime('%Y-%m-%d'), '%Y-%m-%d') - target_dt).days))
    item = items[0]
    log(f'  SAR scene: {item.id}  date={item.datetime.strftime("%Y-%m-%d")}')

    # Find VV asset
    asset_key = None
    for k in ('vv', 'VV', 'vh', 'VH'):
        if k in item.assets:
            asset_key = k; break
    if not asset_key:
        for k in item.assets:
            if 'vv' in k.lower():
                asset_key = k; break
    if not asset_key:
        asset_key = list(item.assets.keys())[0]

    href = item.assets[asset_key].href
    with rasterio.open(href) as src:
        win = rasterio.windows.from_bounds(
            BBOX_LON_MIN, BBOX_LAT_MIN, BBOX_LON_MAX, BBOX_LAT_MAX,
            transform=src.transform
        ) if src.crs.to_epsg() == 4326 else None

        if win is None:
            # Reproject bbox to source CRS
            import pyproj
            from pyproj import Transformer
            tr = Transformer.from_crs('EPSG:4326', src.crs.to_epsg(), always_xy=True)
            x0, y0 = tr.transform(BBOX_LON_MIN, BBOX_LAT_MIN)
            x1, y1 = tr.transform(BBOX_LON_MAX, BBOX_LAT_MAX)
            win = rasterio.windows.from_bounds(x0, y0, x1, y1, transform=src.transform)

        # Read at reduced resolution for fast processing
        out_shape = (1, 2048, int(2048 * (BBOX_LON_MAX - BBOX_LON_MIN) / (BBOX_LAT_MAX - BBOX_LAT_MIN) * 0.9))
        arr = src.read(1, window=win, out_shape=out_shape[1:], resampling=Resampling.bilinear).astype(np.float32)

    # Convert linear to dB, then normalise
    arr = np.where(arr > 0, 10 * np.log10(arr + 1e-10), -30.0)
    p2, p98 = np.percentile(arr, 2), np.percentile(arr, 98)
    arr = np.clip((arr - p2) / max(p98 - p2, 1e-6), 0, 1)
    return arr


# ── Coordinate mapping ─────────────────────────────────────────────────────────

def latlon_to_px(lat, lon, arr_shape):
    """Map (lat, lon) to (col, row) pixel in the SAR image."""
    h, w = arr_shape
    col = int((lon - BBOX_LON_MIN) / (BBOX_LON_MAX - BBOX_LON_MIN) * w)
    row = int((BBOX_LAT_MAX - lat) / (BBOX_LAT_MAX - BBOX_LAT_MIN) * h)
    return col, row


def ship_bbox_px(lat, lon, length_m, arr_shape):
    """Return (x0, y0, x1, y1) bounding box for a ship in image pixels."""
    h, w = arr_shape
    px_per_m_lon = w / ((BBOX_LON_MAX - BBOX_LON_MIN) * 97000)   # ~97 km/deg lon at lat 29°
    px_per_m_lat = h / ((BBOX_LAT_MAX - BBOX_LAT_MIN) * 111000)  # ~111 km/deg lat

    half_len_px = max(length_m / 2 * px_per_m_lat, 8)   # azimuth (N-S for descending)
    width_m = max(length_m * 0.15, 30)
    half_wid_px = max(width_m / 2 * px_per_m_lon, 5)

    cx, cy = latlon_to_px(lat, lon, arr_shape)
    return (int(cx - half_wid_px), int(cy - half_len_px),
            int(cx + half_wid_px), int(cy + half_len_px))


# ── Font helper ───────────────────────────────────────────────────────────────

def load_font(size):
    for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                 '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
                 '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf']:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── Hex colour → RGBA ────────────────────────────────────────────────────────

def hex_rgba(h, alpha=255):
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)


# ── Draw helpers ──────────────────────────────────────────────────────────────

def draw_dashed_rect(draw, bbox, color, width=2, dash=8):
    x0, y0, x1, y1 = bbox
    for edge in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
        (ax, ay), (bx, by) = edge
        total = math.hypot(bx - ax, by - ay)
        steps = max(1, int(total / dash))
        for s in range(steps):
            if s % 2 == 0:
                t0, t1 = s / steps, (s + 0.5) / steps
                draw.line([(int(ax + (bx - ax) * t0), int(ay + (by - ay) * t0)),
                           (int(ax + (bx - ax) * t1), int(ay + (by - ay) * t1))],
                          fill=color, width=width)


def draw_label_bg(draw, xy, text, font, color_rgba, padding=3):
    tx, ty = xy
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([tx - padding, ty - padding,
                    tx + tw + padding, ty + th + padding],
                   fill=(0, 0, 0, 160))
    draw.text(xy, text, font=font, fill=color_rgba)


def fmt_since(date_str):
    """'2026-08-01' → 'since 1 Aug'"""
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return f'since {d.day} {d.strftime("%b")}'
    except Exception:
        return f'since {date_str}'


# ── Render one panel ─────────────────────────────────────────────────────────

def render_panel(sar_arr, ships_to_draw, departed_ships, counts, date_str):
    """
    sar_arr:        float32 [0,1] 2D array
    ships_to_draw:  list of dicts with keys: lat, lon, length_m, since_label (str or None)
    departed_ships: list of ship dicts (shown as dashed 'left' on previous panel)
    counts:         dict total/vlcc/suezaframax/small from anchorage_queue
    date_str:       e.g. '2026-11-17'
    Returns PIL Image (RGBA).
    """
    h, w = sar_arr.shape
    # Scale to IMG_WIDTH
    scale = IMG_WIDTH / w
    nw, nh = IMG_WIDTH, int(h * scale)
    gray = (sar_arr * 255).astype(np.uint8)
    img = Image.fromarray(gray, 'L').resize((nw, nh), Image.LANCZOS).convert('RGBA')
    overlay = Image.new('RGBA', (nw, nh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_lbl  = load_font(LABEL_FONT_SIZE)
    font_hdr  = load_font(HEADER_FONT_SIZE)

    s = scale  # pixel scale factor

    # Draw departed ships (dashed, on previous panel)
    for ship in departed_ships:
        cls = classify(ship.get('length_m', 0))
        if cls == 'small':
            continue
        color = hex_rgba(CLASS_COLORS[cls], 180)
        bbox = ship_bbox_px(float(ship['lat']), float(ship['lon']),
                            int(ship.get('length_m') or 100), sar_arr.shape)
        bbox_s = (int(bbox[0]*s), int(bbox[1]*s), int(bbox[2]*s), int(bbox[3]*s))
        draw_dashed_rect(draw, bbox_s, color, width=2)
        label = f'{CLASS_LABELS[cls]} · left'
        draw_label_bg(draw, (bbox_s[0], max(0, bbox_s[1] - LABEL_FONT_SIZE - 4)),
                      label, font_lbl, color)

    # Draw present ships
    for ship in ships_to_draw:
        lat = float(ship['lat']); lon = float(ship['lon'])
        cls = classify(ship.get('length_m', 0))
        if cls == 'small':
            continue
        color  = hex_rgba(CLASS_COLORS[cls], 220)
        colorw = hex_rgba(CLASS_COLORS[cls], 255)
        bbox = ship_bbox_px(lat, lon, int(ship.get('length_m') or 100), sar_arr.shape)
        bbox_s = (int(bbox[0]*s), int(bbox[1]*s), int(bbox[2]*s), int(bbox[3]*s))
        draw.rectangle(bbox_s, outline=colorw, width=2)
        since = ship.get('since_label')
        label = f'{CLASS_LABELS[cls]} · {since}' if since else f'{CLASS_LABELS[cls]} · new'
        draw_label_bg(draw, (bbox_s[0], max(0, bbox_s[1] - LABEL_FONT_SIZE - 4)),
                      label, font_lbl, color)

    img = Image.alpha_composite(img, overlay)

    # Header strip
    total = counts['total']; vlcc = counts['vlcc']
    saf = counts['suezaframax']; sml = counts['small']
    hdr = f'Total {total} · VLCC {vlcc} · SuezAframax {saf} · Small {sml}'
    draw2 = ImageDraw.Draw(img)
    bbox_hdr = font_hdr.getbbox(hdr)
    hw = bbox_hdr[2] - bbox_hdr[0]
    hh = bbox_hdr[3] - bbox_hdr[1]
    draw2.rectangle([0, 0, nw, hh + 16], fill=(0, 0, 0, 200))
    draw2.text(((nw - hw) // 2, 8), hdr, font=font_hdr, fill=(255, 255, 255, 255))

    return img.convert('RGB')


# ── Main ───────────────────────────────────────────────────────────────────────

def run(incremental=False):
    if not SB_URL:
        fatal('SUPABASE_URL is empty')

    log(f'tracks.py start  incremental={incremental}')
    dates = get_latest_dates(2)   # [previous_date, latest_date]
    prev_d, lat_d = dates[0], dates[1]
    log(f'  dates: previous={prev_d}  latest={lat_d}')

    if incremental:
        snap_date = get_latest_snapshot_date()
        if snap_date and snap_date >= lat_d:
            log(f'  snapshots up to date ({snap_date}), skip')
            return
        log(f'  current snapshot={snap_date}, proceeding to re-render')

    # Get counts from anchorage_queue (3-class totals)
    counts_prev = get_queue_counts(prev_d)
    counts_lat  = get_queue_counts(lat_d)
    log(f'  counts prev={counts_prev}  latest={counts_lat}')

    # Get per-ship data
    ships_prev = get_ships(prev_d)
    ships_lat  = get_ships(lat_d)
    log(f'  ships prev={len(ships_prev)}  latest={len(ships_lat)}')

    # Track matching
    matched_latest, departed, new_arrivals = match_ships(ships_prev, ships_lat)
    log(f'  matched={len(matched_latest)}  departed={len(departed)}  new={len(new_arrivals)}')

    # Build annotated ship lists
    # For latest panel
    latest_draw = []
    for ls, ps in matched_latest:
        latest_draw.append({**ls, 'since_label': fmt_since(prev_d)})
    for s in new_arrivals:
        latest_draw.append({**s, 'since_label': None})   # "new"

    # For previous panel
    prev_draw = []
    for ls, ps in matched_latest:
        prev_draw.append({**ps, 'since_label': None})    # just present, no "new"/"since"
    for s in ships_prev:
        if s not in [ps for _, ps in matched_latest] and s not in departed:
            prev_draw.append({**s, 'since_label': None})
    # rebuild prev_draw from full ships_prev minus departed
    prev_matched_ships = {id(ps) for _, ps in matched_latest}
    prev_draw = []
    for ps in ships_prev:
        if ps not in departed:
            prev_draw.append({**ps, 'since_label': None})

    # Fetch SAR images
    log(f'  fetching SAR for previous={prev_d}')
    sar_prev = fetch_sar(prev_d)
    log(f'  fetching SAR for latest={lat_d}')
    sar_lat  = fetch_sar(lat_d)

    # Render panels
    log('  rendering previous panel')
    img_prev = render_panel(sar_prev, prev_draw, departed, counts_prev, prev_d)
    log('  rendering latest panel')
    img_lat  = render_panel(sar_lat, latest_draw, [], counts_lat, lat_d)

    # Encode to PNG bytes
    def to_png(img):
        buf = io.BytesIO()
        img.save(buf, 'PNG', optimize=True)
        return buf.getvalue()

    png_prev = to_png(img_prev)
    png_lat  = to_png(img_lat)
    log(f'  PNG sizes: previous={len(png_prev)//1024}KB  latest={len(png_lat)//1024}KB')

    # Upload
    log('  uploading previous.png')
    sb_upload('previous.png', png_prev, 'image/png')
    log('  uploading latest.png')
    sb_upload('latest.png', png_lat, 'image/png')

    meta = {
        'previous': {'date': prev_d, 'file': 'previous.png'},
        'latest':   {'date': lat_d,  'file': 'latest.png'},
        'updated':  datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    sb_upload('snapshots.json', json.dumps(meta).encode(), 'application/json')
    log(f'tracks.py done  previous={prev_d}  latest={lat_d}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--incremental', action='store_true',
                    help='skip if snapshots.json already reflects the latest date')
    args = ap.parse_args()
    run(incremental=args.incremental)
