"""
tz6_ring15.py — ТЗ-6 пп.1–3
  1. Heading из SH Catalog (одна сцена Yanbu ASC-14). Если недоступен → az=80°, az_source='const'.
  2. ring15_far_dB, ring15_near_dB для Yanbu o14, все даты, p9_tank_ref координаты.
  3. То же для RT o101, все даты, tank_registry координаты.
  Barrels: не зависят от ring15 маски — см. config.py:BARREL_PER_M3 + формулу
  fill_ratio/h_current в main_script.py. ring15 — диагностическая метрика.
  CSV: p9 формат + az_used, az_source.

CDSE не использовать. 282° запрещены.
"""

import io, math, os, sys, json, gzip, csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/research → repo root
OUT_DIR = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")

# ── Credentials ───────────────────────────────────────────────────────────────
_CID = os.environ.get("CLIENT_ID") or "sh-5215639c-a2e5-47db-8d46-7e28a3150cde"
_CSC = os.environ.get("CLIENT_SECRET") or "UyosmDOgQ4ejiHbrWpDCT0WzLYgvhfloa3VgOLTtLd7c050tvejLgIVCcXfr3eLXT2JWqfrqjQhBgewF3g6nGF"
_ca_path = os.environ.get("REQUESTS_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")
CA = _ca_path if os.path.exists(_ca_path) else True  # True = system CA in GHA

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_BASE        = "https://sh.dataspace.copernicus.eu"

# ── p9_tank_ref — Yanbu (exact radii) ─────────────────────────────────────────
YANBU_TANKS = [
    dict(id="yb_0",  lat=23.959101323, lon=38.244431649, radius_m=61.2, h_max_m=18),
    dict(id="yb_1",  lat=23.962622345, lon=38.247959117, radius_m=57.5, h_max_m=18),
    dict(id="yb_2",  lat=23.964086112, lon=38.246019849, radius_m=58.8, h_max_m=18),
    dict(id="yb_3",  lat=23.965572614, lon=38.244090600, radius_m=58.8, h_max_m=18),
    dict(id="yb_4",  lat=23.965014531, lon=38.250110186, radius_m=57.5, h_max_m=18),
    dict(id="yb_5",  lat=23.966490384, lon=38.248151074, radius_m=56.2, h_max_m=17),
    dict(id="yb_6",  lat=23.967977447, lon=38.246210485, radius_m=57.5, h_max_m=18),
    dict(id="yb_7",  lat=23.967415030, lon=38.252250931, radius_m=56.2, h_max_m=17),
    dict(id="yb_8",  lat=23.968883754, lon=38.250302328, radius_m=58.8, h_max_m=18),
    dict(id="yb_9",  lat=23.970361492, lon=38.248351122, radius_m=57.5, h_max_m=18),
    dict(id="yb_10", lat=23.969808175, lon=38.254390690, radius_m=58.8, h_max_m=18),
    dict(id="yb_11", lat=23.971278217, lon=38.252450233, radius_m=58.8, h_max_m=18),
]

# ── Geometry helpers ───────────────────────────────────────────────────────────

def _crescent_mask(cx, cy, r_px, los_az_deg, width_px, H, W, half_angle=90):
    y, x = np.mgrid[0:H, 0:W]
    dist = np.sqrt((x - cx)**2 + (y - cy)**2)
    ring = (dist >= max(0, r_px - width_px)) & (dist <= r_px)
    los_rad = math.radians(90 - los_az_deg)
    angle   = np.arctan2(-(y - cy), x - cx)
    diff    = np.abs(((angle - los_rad + math.pi) % (2 * math.pi)) - math.pi)
    return ring & (diff <= math.radians(half_angle))

def _safe_db(pixels):
    v = pixels[np.isfinite(pixels) & (pixels > 0)]
    if len(v) == 0:
        return float("nan")
    return float(10 * math.log10(float(np.mean(v))))


# ── SH OAuth token ─────────────────────────────────────────────────────────────

def _get_sh_token():
    import requests
    r = requests.post(CDSE_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": _CID, "client_secret": _CSC,
    }, verify=CA, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


# ── SH Catalog: get scene dates + heading for one orbit ───────────────────────

def _make_sh_config():
    from sentinelhub import SHConfig
    cfg = SHConfig()
    cfg.sh_client_id     = _CID
    cfg.sh_client_secret = _CSC
    cfg.sh_token_url     = CDSE_TOKEN_URL
    cfg.sh_base_url      = SH_BASE
    return cfg


def sh_catalog_search(bbox_list, rel_orbit, direction, limit=500):
    """
    Returns list of dicts: {date, scene_id, platform, heading (or None), props}.
    bbox_list: [lon_min, lat_min, lon_max, lat_max]
    """
    from sentinelhub import SentinelHubCatalog, BBox, CRS, DataCollection
    cfg = _make_sh_config()
    try:
        S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
            "s1iw_cdse_tz6", service_url=SH_BASE)
    except ValueError:
        S1_CDSE = DataCollection["s1iw_cdse_tz6"]

    catalog = SentinelHubCatalog(config=cfg)
    bbox = BBox(bbox_list, crs=CRS.WGS84)
    raw = list(catalog.search(
        collection=S1_CDSE,
        bbox=bbox,
        time=("2018-01-01", "2026-12-31"),
        fields={"include": ["id", "properties"]},
        limit=200,
    ))
    results = []
    for feat in raw:
        props = feat.get("properties", {})
        dt_str = (props.get("datetime") or "")
        date = dt_str[:10] if dt_str else ""
        ro   = props.get("sat:relative_orbit")
        dir_ = (props.get("sat:orbit_state") or "").upper()
        if ro != rel_orbit or dir_ != direction.upper():
            continue
        scene_id = feat.get("id", "")
        platform = props.get("platform", "")
        # Try to find heading under various field names
        heading = None
        for k in ("platform_heading", "sat:platform_heading",
                  "s1:platformHeading", "platformHeading"):
            v = props.get(k)
            if v is not None:
                try:
                    heading = float(v)
                except (ValueError, TypeError):
                    pass
                break
        results.append(dict(date=date, scene_id=scene_id, platform=platform,
                            heading=heading, props=props))
        if len(results) >= limit:
            break
    return results


# ── SH Process API: fetch image for one date ──────────────────────────────────

def sh_fetch_image(bbox_wgs84, date_str, rel_orbit, direction, W, H, token):
    """
    Returns float32 numpy array (H, W) in linear power or None.
    bbox_wgs84: [lon_min, lat_min, lon_max, lat_max]
    """
    import requests
    EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""
    d0 = date_str
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    body = {
        "input": {
            "bounds": {
                "bbox": bbox_wgs84,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {"from": f"{d0}T00:00:00Z", "to": f"{d1}T00:00:00Z"},
                    "orbitDirection": direction.upper(),
                    "relativeOrbitNumber": rel_orbit,
                },
                "processing": {
                    "orthorectify": True,
                    "backCoeff": "GAMMA0_TERRAIN",
                    "demInstance": "COPERNICUS_30",
                },
            }]
        },
        "output": {
            "width": W, "height": H,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]
        },
        "evalscript": EVALSCRIPT,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{SH_BASE}/api/v1/process"
    try:
        r = requests.post(url, json=body, headers=headers, verify=CA, timeout=60)
        if r.status_code == 204:
            return None  # no data
        r.raise_for_status()
        from PIL import Image
        img = Image.open(io.BytesIO(r.content))
        return np.array(img, dtype=np.float32)
    except Exception as e:
        print(f"    SH fetch error {date_str}: {e}")
        return None


# ── Per-terminal ring15 computation ───────────────────────────────────────────

def compute_ring15(terminal, tanks, dates_scenes, rel_orbit, direction, az_deg, token):
    """
    Returns list of row dicts.
    dates_scenes: list of {date, scene_id, platform}
    """
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    RES = 10  # m/px
    BUFFER = 300

    lon_min = min(lons) - BUFFER / (111320 * math.cos(math.radians(min(lats))))
    lon_max = max(lons) + BUFFER / (111320 * math.cos(math.radians(max(lats))))
    lat_min = min(lats) - BUFFER / 111320
    lat_max = max(lats) + BUFFER / 111320
    bbox = [lon_min, lat_min, lon_max, lat_max]

    W = max(1, int((lon_max - lon_min) * 111320 * math.cos(math.radians((lat_min+lat_max)/2)) / RES))
    H = max(1, int((lat_max - lat_min) * 111320 / RES))
    print(f"  {terminal} o{rel_orbit} {direction}: bbox={W}×{H}px, {len(dates_scenes)} dates, az={az_deg}°")

    w15 = 15 / RES  # 15m width in pixels

    # Pre-compute tank pixel coords and masks
    tank_masks = {}
    for tank in tanks:
        cx = (tank["lon"] - lon_min) / (lon_max - lon_min) * W
        cy = (lat_max - tank["lat"]) / (lat_max - lat_min) * H
        r  = tank["radius_m"] / RES
        cx, cy = int(round(cx)), int(round(cy))
        if not (0 <= cx < W and 0 <= cy < H):
            tank_masks[tank["id"]] = None
            continue
        far_mask  = _crescent_mask(cx, cy, r, az_deg,             w15, H, W)
        near_mask = _crescent_mask(cx, cy, r, (az_deg+180)%360,   w15, H, W)
        tank_masks[tank["id"]] = {"far": far_mask, "near": near_mask, "cx": cx, "cy": cy}

    rows = []
    for item in sorted(dates_scenes, key=lambda x: x["date"]):
        d_str    = item["date"]
        scene_id = item.get("scene_id", "")
        platform = item.get("platform", "")
        img = sh_fetch_image(bbox, d_str, rel_orbit, direction, W, H, token)
        if img is None:
            print(f"    {d_str}: no data")
            continue
        # Ensure 2D
        if img.ndim == 3:
            img = img[:, :, 0]
        H_img, W_img = img.shape
        for tank in tanks:
            m = tank_masks.get(tank["id"])
            if m is None:
                continue
            def _pix(mask):
                mh = min(mask.shape[0], H_img)
                mw = min(mask.shape[1], W_img)
                return img[:mh, :mw][mask[:mh, :mw]]
            rows.append({
                "date":           d_str,
                "scene_id":       scene_id,
                "rel_orbit":      rel_orbit,
                "platform":       platform,
                "terminal":       terminal,
                "tank_id":        tank["id"],
                "az_used":        az_deg,
                "az_source":      AZ_SOURCE,
                "ring15_far_dB":  _safe_db(_pix(m["far"])),
                "ring15_near_dB": _safe_db(_pix(m["near"])),
            })
        print(f"    {d_str}: {len(tanks)} tanks ok")
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global AZ_SOURCE

    print(f"=== tz6_ring15.py  {datetime.now(timezone.utc).isoformat()} ===")
    print()

    # ── 1. Get SH token ────────────────────────────────────────────────────────
    print("1. Getting SH token...")
    token = _get_sh_token()
    print("   OK")
    print()

    # ── 2. Get heading from SH Catalog — Yanbu ASC-14 ─────────────────────────
    print("2. SH Catalog: Yanbu ASC-14 scenes (heading + dates)...")
    yanbu_bbox = [38.20, 23.92, 38.30, 24.00]
    yanbu_scenes = sh_catalog_search(yanbu_bbox, 14, "ascending")
    print(f"   found {len(yanbu_scenes)} scenes")

    # Try to extract heading
    heading = None
    heading_source = None
    heading_scene = None
    for sc in yanbu_scenes[:5]:  # check first few scenes
        # Print all property keys to find heading
        props = sc.get("props", {})
        for k, v in props.items():
            if "head" in k.lower() or "azimuth" in k.lower() or "bearing" in k.lower():
                print(f"   scene {sc['scene_id'][:40]}: {k}={v}")
                if "head" in k.lower() and v is not None:
                    heading = float(v)
                    heading_source = f"sh_catalog:{k}"
                    heading_scene  = sc["scene_id"]
                    break
        if heading is not None:
            break

    if heading is not None:
        az_used = (heading + 90) % 360
        AZ_SOURCE = heading_source
        print(f"   heading={heading:.2f}° (source: {AZ_SOURCE}, scene: {heading_scene[:40]})")
        print(f"   LOS = heading + 90 = {az_used:.2f}°")
        if abs(az_used - 282) < 5:
            print("   FATAL: az_used ≈ 282° (DESC LOS) — wrong orbit or wrong heading. Falling back to const 80°.")
            az_used = 80.0
            AZ_SOURCE = "const"
    else:
        # Print sample props for diagnostic
        if yanbu_scenes:
            sc = yanbu_scenes[0]
            print(f"   sample scene props keys: {list(sc.get('props', {}).keys())[:20]}")
        az_used = 80.0
        AZ_SOURCE = "const"
        print(f"   heading not found in SH Catalog → az_used=80°, az_source='const'")

    print(f"\n   FINAL: az_used={az_used}°  az_source={AZ_SOURCE}")
    print()

    # ── 3. Yanbu o14 ring15 ───────────────────────────────────────────────────
    print("3. Yanbu o14 ring15 computation...")
    # De-duplicate by date (one scene per date)
    seen = {}
    for sc in yanbu_scenes:
        d = sc["date"]
        if d and d not in seen:
            seen[d] = sc
    yanbu_dates_scenes = list(seen.values())
    print(f"   {len(yanbu_dates_scenes)} unique dates")
    if not yanbu_dates_scenes:
        print("   FATAL: no Yanbu ASC-14 dates from SH Catalog")
        sys.exit(1)

    yanbu_rows = compute_ring15("yanbu", YANBU_TANKS, yanbu_dates_scenes,
                                14, "ASCENDING", az_used, token)
    print(f"   {len(yanbu_rows)} rows")
    print()

    # ── 4. RT o101 ring15 ─────────────────────────────────────────────────────
    print("4. RT o101 ring15 computation...")
    import csv as csv_mod
    rt_obs_path = REPO_ROOT / "backups" / "asc_backup_2026-10-01.csv"
    rt_rows_all = []
    with open(rt_obs_path) as f:
        for row in csv_mod.DictReader(f):
            rt_rows_all.append(row)

    # De-duplicate by date (one entry per date, take first)
    rt_seen = {}
    for row in rt_rows_all:
        d = row["date"]
        if d not in rt_seen:
            rt_seen[d] = dict(date=d, scene_id="", platform="")
    rt_dates_scenes = list(rt_seen.values())
    print(f"   {len(rt_dates_scenes)} unique dates from backup")

    # Load RT tanks from tank_registry
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from tank_registry import LOCATIONS
    rt_tanks = next(loc["tanks"] for loc in LOCATIONS if loc["id"] == "ras_tanura")
    rt_tanks_enabled = [t for t in rt_tanks if t.get("enabled", True)]
    print(f"   RT tanks: {len(rt_tanks)} total, {len(rt_tanks_enabled)} enabled")

    # Get RT heading from SH Catalog too
    print("   SH Catalog: RT ASC-101 heading check...")
    rt_bbox = [50.10, 26.59, 50.22, 26.69]
    rt_scenes_cat = sh_catalog_search(rt_bbox, 101, "ascending", limit=5)
    print(f"   found {len(rt_scenes_cat)} scenes")
    rt_heading = None
    for sc in rt_scenes_cat[:3]:
        props = sc.get("props", {})
        for k, v in props.items():
            if "head" in k.lower() and v is not None:
                try:
                    rt_heading = float(v)
                    print(f"   RT heading={rt_heading:.2f}° from {k}")
                except:
                    pass
                break
        if rt_heading is not None:
            break
    if rt_heading is not None:
        rt_az = (rt_heading + 90) % 360
        rt_az_source = "sh_catalog"
    else:
        rt_az = az_used  # same const if catalog doesn't provide
        rt_az_source = AZ_SOURCE
        print(f"   RT heading not found → az={rt_az}°, source={rt_az_source}")
    print(f"   RT FINAL: az_used={rt_az}°  az_source={rt_az_source}")

    rt_ring_rows = compute_ring15("ras_tanura", rt_tanks_enabled, rt_dates_scenes,
                                  101, "ASCENDING", rt_az, token)
    print(f"   {len(rt_ring_rows)} rows")
    print()

    # ── 5. Combine and pre-delivery checks ────────────────────────────────────
    all_rows = yanbu_rows + rt_ring_rows
    if not all_rows:
        print("FATAL: 0 rows total"); sys.exit(1)

    print("=== PRE-DELIVERY: доля непустых ring15_far_dB по terminal × rel_orbit ===")
    from collections import defaultdict
    stats = defaultdict(lambda: [0, 0])
    for r in all_rows:
        k = (r["terminal"], r["rel_orbit"])
        stats[k][0] += 1
        if not math.isnan(r["ring15_far_dB"]):
            stats[k][1] += 1

    ok = True
    for (term, orb), (n, nn) in sorted(stats.items()):
        frac = nn / n if n else 0
        print(f"  {term:12s}  o{orb:3d}  n={n:5d}  nn={nn:5d}  frac={frac:.3f}")
        if frac == 0:
            print(f"  FATAL: {term} o{orb} ring15_far_dB = 0% non-null")
            ok = False

    print()
    print("=== VERIFY: az_used ≠ 282 in all rows ===")
    bad = [r for r in all_rows if abs(r["az_used"] - 282) < 1]
    if bad:
        print(f"  FATAL: {len(bad)} rows have az_used ≈ 282°")
        ok = False
    else:
        print(f"  OK: all {len(all_rows)} rows have az_used ≠ 282°")
    print()

    if not ok:
        sys.exit(1)

    # ── 6. Write CSV ──────────────────────────────────────────────────────────
    COLS = ["date","scene_id","rel_orbit","platform","terminal","tank_id",
            "az_used","az_source","ring15_far_dB","ring15_near_dB"]
    out_path = OUT_DIR / f"tz6_ring15_{TS}.csv"
    with open(out_path, "w", newline="") as f:
        w = csv_mod.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in sorted(all_rows, key=lambda x: (x["terminal"], x["date"], x["tank_id"])):
            w.writerow({c: r.get(c, "") for c in COLS})

    print(f"WRITTEN: {out_path}")
    print(f"  total rows: {len(all_rows)}")
    print()

    # Summary stats from CSV
    print("=== SUMMARY ===")
    for (term, orb), (n, nn) in sorted(stats.items()):
        rows_t = [r for r in all_rows if r["terminal"] == term]
        far_vals = [r["ring15_far_dB"] for r in rows_t if not math.isnan(r["ring15_far_dB"])]
        near_vals = [r["ring15_near_dB"] for r in rows_t if not math.isnan(r["ring15_near_dB"])]
        print(f"  {term} o{orb}:")
        print(f"    rows={n}  non-null far={nn}/{n} ({nn/n*100:.1f}%)")
        if far_vals:
            print(f"    ring15_far_dB  mean={np.mean(far_vals):.2f}  "
                  f"min={np.min(far_vals):.2f}  max={np.max(far_vals):.2f}")
        if near_vals:
            print(f"    ring15_near_dB mean={np.mean(near_vals):.2f}  "
                  f"min={np.min(near_vals):.2f}  max={np.max(near_vals):.2f}")
    print()
    print("NOTE: barrels не пересчитаны — они зависят от db_mean (апертура целиком),")
    print("      не от ring15 маски. Формула: fill_ratio = (dB_max - db_mean_clipped)/")
    print("      (dB_max - dB_min), h_current = h_min + fill_ratio*(h_max-h_min),")
    print("      vol_bbl = π*r²*h_current*6.2898.")
    print("      Источник: scripts/config.py (BARREL_PER_M3) + scripts/main_script.py.")
    print(f"\nDone. Output: {out_path}")


if __name__ == "__main__":
    main()
