"""
berth_v4_history.py — v4 detection on all S1 scenes 2024-01-01 → today.

v4 config (fixed per berth):
  sea_island:     BG_V3  pier=20 water=20-80 len≥120 aspect≥2.0 merge=40
  east_t_head:    BG_V4  pier=20 water=20-80 len≥150 aspect≥3.0 merge=0
  southeast_pier: BG_V3  pier=20 water=20-80 len≥150 aspect≥3.0 merge=0

Output (v5.0):
  diagnostics/berth_v4/history_v4/berth_v4_history.csv  (hull-level rows)
  data/berth_occupancy.json  (site widget, v5.0 format)

DB write: if SUPABASE_SERVICE_KEY is set, upserts rows into berth_occupancy table.
"""

import os, sys, csv, json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent))

from berth_detect_core import (
    RESOLUTION, CELL_KM, CORRIDOR_END_M, RATIO_THR, struct8,
    BERTH_COORDS,
    metres_per_deg_lat, metres_per_deg_lon, berth_center,
    cell_bbox_for, build_geom, find_blobs,
)

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
import shapely

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url  = "https://sh.dataspace.copernicus.eu"
if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID / CLIENT_SECRET not set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_v4hist", service_url="https://sh.dataspace.copernicus.eu")

ROOT    = Path(__file__).parent.parent
BG_V3   = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4   = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "history_v4"
JSON_OUT = ROOT / "data" / "berth_occupancy.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_START   = "2024-01-01"
ORBITS          = [35, 101]

BERTHS = BERTH_COORDS  # alias for local use

V4_CONFIG = {
    "sea_island":     {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 120, "aspect_thr": 2.0, "merge_gap": 40, "bg_dir": BG_V3},
    "east_t_head":    {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "aspect_thr": 3.0, "merge_gap":  0, "bg_dir": BG_V4},
    "southeast_pier": {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "aspect_thr": 3.0, "merge_gap":  0, "bg_dir": BG_V3},
}

AOI_BBOX = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

EVALSCRIPT_S1 = """
//VERSION=3
function setup() { return { input: ["VV"], output: { bands:1, sampleType:"FLOAT32" } }; }
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

# ── Catalog & fetch ────────────────────────────────────────────────────────────

def discover_dates(date_from, date_to):
    catalog = SentinelHubCatalog(config=config)
    today = date_to
    print(f"Discovering S1 catalog {date_from} → {today} ...")
    all_scenes = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(date_from+"T00:00:00Z", today+"T23:59:59Z"),
        fields={"include":["properties.datetime","properties.sat:relative_orbit"]},
    ))
    print(f"  total scenes: {len(all_scenes)}")
    grouped = {}
    for s in all_scenes:
        p = s.get("properties", {})
        orb = p.get("sat:relative_orbit")
        if orb not in ORBITS: continue
        dt_raw = p.get("datetime", "")
        try:
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.strptime(dt_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        key = (date_str, orb)
        if key not in grouped or dt_raw < grouped[key]["dt_raw"]:
            grouped[key] = {"date_str": date_str, "orbit": orb,
                            "scene_dt": dt_raw, "dt_raw": dt_raw}
    result = sorted(grouped.values(), key=lambda x: (x["date_str"], x["orbit"]))
    print(f"  unique (date, orbit) pairs: {len(result)}")
    return result

def fetch_s1_linear(scene_dt, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(scene_dt[:19].replace("T"," "), scene_dt[:19].replace("T"," ")),
            other_args={"processing":{"backCoeff":"SIGMA0_ELLIPSOID","orthorectify":True,
                                      "demInstance":"COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d = req.get_data(); a = d[0]
        if a.ndim == 3: a = a[:,:,0]
        return a.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch err: {e}"); return None

# ── Hull classification ────────────────────────────────────────────────────────

def classify_hull(len_m):
    if len_m >= 300:
        return "VLCC"
    return "SuezAframax"

# ── Loading event detection ────────────────────────────────────────────────────

def is_new_load(pos_m, len_m, prev_hulls):
    """Return True if no hull in prev_hulls matches this position/length."""
    return not any(
        abs(pos_m - ph["pos_m"]) <= 150 and
        abs(len_m - ph["len_m"]) / max(ph["len_m"], 1) <= 0.20
        for ph in prev_hulls
    )

# ── Metric helpers ─────────────────────────────────────────────────────────────

def count_loadings(loadings, start, end):
    subset = [l for l in loadings if start <= l["date"] <= end]
    total = len(subset)
    vlcc  = sum(1 for l in subset if l["class"] == "VLCC")
    suez  = sum(1 for l in subset if l["class"] == "SuezAframax")
    return {"total": total, "VLCC": vlcc, "SuezAframax": suez}

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    entries = discover_dates(HISTORY_START, today)

    # Pre-load BG per (berth, orbit)
    bg_cache = {}
    bg_source_cache = {}
    for berth, cfg in V4_CONFIG.items():
        for orbit in ORBITS:
            p = cfg["bg_dir"] / f"bg_o{orbit}_{berth}.npy"
            if p.exists():
                bg_cache[(berth, orbit)] = np.load(p)
                if berth == "east_t_head" and orbit == 35 and cfg["bg_dir"] == BG_V4:
                    bg_source_cache[(berth, orbit)] = "bg_v4_o35_2026"
                else:
                    bg_source_cache[(berth, orbit)] = cfg["bg_dir"].name
            else:
                p_v3 = BG_V3 / f"bg_o{orbit}_{berth}.npy"
                if p_v3.exists():
                    bg_cache[(berth, orbit)] = np.load(p_v3)
                    bg_source_cache[(berth, orbit)] = "bg_v3_fallback"
                    print(f"  BG fallback: {berth} o{orbit} → bg_v3")
                else:
                    bg_cache[(berth, orbit)] = None
                    bg_source_cache[(berth, orbit)] = None
                    print(f"  MISSING BG: {berth} o{orbit}")

    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTHS.items()}
    cell_dims   = {b: bbox_to_dimensions(cell_bboxes[b], resolution=RESOLUTION) for b in BERTHS}

    # Pass-level results (for JSON output)
    passes_out = []
    loadings   = []
    hull_rows  = []  # for CSV

    # Track previous pass hulls per (berth, orbit) for new_load detection
    prev_hulls: dict = {}  # (berth, orbit) → list of {pos_m, len_m}

    for entry in entries:
        date_str = entry["date_str"]
        orbit    = entry["orbit"]
        scene_dt = entry["scene_dt"]
        print(f"\n{date_str} o{orbit}  scene={scene_dt[:16]}")

        pass_record = {"date": date_str, "orbit": orbit}

        for berth, pts in BERTHS.items():
            cfg  = V4_CONFIG[berth]
            bg   = bg_cache.get((berth, orbit))
            bg_src = bg_source_cache.get((berth, orbit))
            if bg is None:
                pass_record[berth] = {"det": "NO_BG", "hulls": []}
                continue

            linear = fetch_s1_linear(scene_dt, cell_bboxes[berth])
            if linear is None:
                pass_record[berth] = {"det": "ERR", "hulls": []}
                continue

            if bg.shape != linear.shape:
                bg_r = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                    linear.shape[1]/bg.shape[1]), order=1)
            else:
                bg_r = bg

            ratio = np.where((linear>0)&(bg_r>0),
                             10*np.log10(linear/bg_r), np.nan).astype(np.float32)
            geom = build_geom(pts, cell_bboxes[berth], linear.shape,
                              cfg["pier"], cfg["water_i"], cfg["water_o"])

            blobs, best, detected = find_blobs(
                ratio, geom["water_mask"], geom["axis_uvec"],
                merge_gap_m=cfg.get("merge_gap", 0),
                min_len_m=cfg["min_len"],
                min_aspect=cfg.get("aspect_thr", 3.0),
                return_all=True,
            )

            det_str = "OCC" if detected else "EMP"

            # Build hull list from all qualifying blobs
            cw, ch = cell_dims[berth]
            cbbox  = cell_bboxes[berth]
            ux, uy = geom["axis_uvec"]

            prev = prev_hulls.get((berth, orbit), [])
            hull_list = []

            for blob in (blobs if blobs else []):
                len_m = float(blob["length_m"])
                wid_m = float(blob.get("width_m", blob.get("length_m", len_m) / max(blob.get("aspect", 3.0), 1)))

                # Geographic centre of hull
                axis_mid_px = (blob["axis_min"] + blob["axis_max"]) / 2
                perp_ctr    = blob.get("perp_ctr", 0)
                side        = 1 if perp_ctr >= 0 else -1
                pos_m       = round(float(axis_mid_px) * RESOLUTION, 0)

                # pixel position within cell
                col_c = axis_mid_px * ux + perp_ctr * (-uy)
                row_c = axis_mid_px * uy + perp_ctr * ux
                lon_c = cbbox.min_x + (col_c / cw) * (cbbox.max_x - cbbox.min_x)
                lat_c = cbbox.max_y - (row_c / ch) * (cbbox.max_y - cbbox.min_y)

                cls      = classify_hull(len_m)
                new_load = is_new_load(pos_m, len_m, prev)

                hull = {
                    "side":     side,
                    "pos_m":    pos_m,
                    "lon":      round(lon_c, 6),
                    "lat":      round(lat_c, 6),
                    "len_m":    round(len_m, 1),
                    "width_m":  round(wid_m, 1),
                    "class":    cls,
                    "new_load": new_load,
                }
                hull_list.append(hull)

                hull_rows.append({
                    "date": date_str, "orbit": orbit, "berth": berth,
                    "hull_idx": len(hull_list) - 1,
                    "det": det_str, "bg_source": bg_src,
                    **hull,
                })

                if new_load:
                    loadings.append({
                        "date":   date_str,
                        "orbit":  orbit,
                        "berth":  berth,
                        "side":   side,
                        "pos_m":  pos_m,
                        "lon":    round(lon_c, 6),
                        "lat":    round(lat_c, 6),
                        "len_m":  round(len_m, 1),
                        "class":  cls,
                    })

            prev_hulls[(berth, orbit)] = [{"pos_m": h["pos_m"], "len_m": h["len_m"]}
                                           for h in hull_list]

            if not hull_list and detected and best:
                # find_blobs didn't return all blobs — add best as fallback
                len_m = float(best["length_m"])
                hull_list.append({
                    "side": 0, "pos_m": 0, "lon": 0, "lat": 0,
                    "len_m": round(len_m, 1),
                    "width_m": round(len_m / max(best.get("aspect", 3.0), 1), 1),
                    "class": classify_hull(len_m),
                    "new_load": is_new_load(0, len_m, prev),
                })

            pass_record[berth] = {"det": det_str, "hulls": hull_list}
            print(f"  {berth}: {det_str}  hulls={len(hull_list)}")

        passes_out.append(pass_record)

    # ── CSV output (hull-level) ────────────────────────────────────────────────
    csv_path = OUT_DIR / "berth_v4_history.csv"
    fieldnames = ["date","orbit","berth","hull_idx","det","side","pos_m",
                  "lon","lat","len_m","width_m","class","new_load","bg_source"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in hull_rows:
            w.writerow(r)
    print(f"\nSaved: {csv_path} ({len(hull_rows)} hull rows)")

    # ── Metrics ───────────────────────────────────────────────────────────────
    from datetime import date as _date
    today_dt = _date.fromisoformat(today)
    d4w_start  = (today_dt - timedelta(weeks=4)).isoformat()
    d4w_py_end = (today_dt - timedelta(weeks=52)).isoformat()
    d4w_py_start = (today_dt - timedelta(weeks=56)).isoformat()

    l4w    = count_loadings(loadings, d4w_start, today)
    l4w_py = count_loadings(loadings, d4w_py_start, d4w_py_end)

    # Last loading event
    last_ship = None
    if loadings:
        ls = loadings[-1]
        last_ship = {
            "date":   ls["date"],
            "berth":  ls["berth"],
            "class":  ls["class"],
            "len_m":  ls["len_m"],
        }

    # Days since last loading
    days_since = None
    if last_ship:
        last_dt = _date.fromisoformat(last_ship["date"])
        days_since = (today_dt - last_dt).days

    metrics = {
        "loadings_4w":     l4w,
        "loadings_4w_py":  l4w_py,
        "last_ship":       last_ship,
        "days_since_loading": days_since,
    }

    # ── JSON output for site (v5.0) ───────────────────────────────────────────
    obj = {
        "generated": today,
        "version":   "v5.0",
        "note":      "lower bound — detector misses ~1/3 of ships; precision ~92% when OCC",
        "berths":    ["sea_island", "east_t_head", "southeast_pier"],
        "passes":    passes_out,
        "loadings":  loadings,
        "metrics":   metrics,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(obj, separators=(",",":")))
    print(f"Saved: {JSON_OUT} ({len(passes_out)} passes, {len(loadings)} loadings)")

    # ── Detection summary ─────────────────────────────────────────────────────
    print("\n=== Detection summary ===")
    print(f"Period: {HISTORY_START} → {today}")
    print(f"Loadings detected: {len(loadings)}")
    print(f"Loadings last 4w: {l4w}")
    if last_ship:
        print(f"Last ship: {last_ship}")

    # ── Supabase write ────────────────────────────────────────────────────────
    svc_key  = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    supa_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not svc_key:
        print("\nSUPABASE_SERVICE_KEY not set — history saved to CSV/JSON only, DB write skipped.")
        return
    if not supa_url:
        sys.exit("FATAL: SUPABASE_SERVICE_KEY set but SUPABASE_URL is empty")

    # DB rows: backward-compat with old berth_occupancy schema
    # Use best hull (max len_m) per (date, orbit, berth)
    db_map = {}
    for r in hull_rows:
        key = (r["date"], r["orbit"], r["berth"])
        if key not in db_map or r["len_m"] > db_map[key].get("length_m", 0):
            db_map[key] = {
                "date": r["date"], "orbit": r["orbit"], "berth": r["berth"],
                "det": r["det"], "length_m": r["len_m"], "aspect": None,
            }
    # Include EMP rows from passes without hulls
    for p in passes_out:
        for berth in ["sea_island","east_t_head","southeast_pier"]:
            bd = p.get(berth, {})
            key = (p["date"], p["orbit"], berth)
            if key not in db_map and bd.get("det") in ("EMP","NO_BG","ERR"):
                db_map[key] = {
                    "date": p["date"], "orbit": p["orbit"], "berth": berth,
                    "det": bd["det"], "length_m": None, "aspect": None,
                }

    import urllib.request as _ureq
    db_rows = list(db_map.values())
    payload = json.dumps(db_rows).encode()
    req = _ureq.Request(
        f"{supa_url}/rest/v1/berth_occupancy",
        data=payload,
        headers={
            "apikey": svc_key,
            "Authorization": f"Bearer {svc_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST",
    )
    try:
        with _ureq.urlopen(req) as resp:
            print(f"\nDB write OK: HTTP {resp.status} — {len(db_rows)} rows upserted")
    except Exception as exc:
        sys.exit(f"FATAL: DB write failed — {exc}")


if __name__ == "__main__":
    run()
