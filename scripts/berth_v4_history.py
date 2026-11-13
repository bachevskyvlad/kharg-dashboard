"""
berth_v4_history.py — v4 detection on all S1 scenes 2024-01-01 → today.

v4 config (fixed per berth):
  sea_island:     BG_V3  pier=20 water=20-80 len≥120 aspect≥2.0 merge=40
  east_t_head:    BG_V4  pier=20 water=20-80 len≥150 aspect≥3.0 merge=0   ← EMP background
  southeast_pier: BG_V3  pier=20 water=20-80 len≥150 aspect≥3.0 merge=0

Output:
  diagnostics/berth_v4/history_v4/berth_v4_history.csv
  data/berth_occupancy.json  (site widget)

DB write: if SUPABASE_SERVICE_KEY is set, upserts rows into berth_occupancy table.
"""

import os, sys, csv, json
from pathlib import Path
from datetime import datetime, timezone

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

# geometry and find_blobs imported from berth_detect_core above

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

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    entries = discover_dates(HISTORY_START, today)

    # Pre-load BG per (berth, orbit); fallback to BG_V3 when primary missing
    bg_cache = {}
    bg_source_cache = {}
    for berth, cfg in V4_CONFIG.items():
        for orbit in ORBITS:
            p = cfg["bg_dir"] / f"bg_o{orbit}_{berth}.npy"
            if p.exists():
                bg_cache[(berth, orbit)] = np.load(p)
                # Special label for the purpose-built orbit-35 east_t_head bg
                if berth == "east_t_head" and orbit == 35 and cfg["bg_dir"] == BG_V4:
                    bg_source_cache[(berth, orbit)] = "bg_v4_o35_2026"
                else:
                    bg_source_cache[(berth, orbit)] = cfg["bg_dir"].name  # "bg_v4" or "bg_v3"
            else:
                # Fallback: try BG_V3
                p_v3 = BG_V3 / f"bg_o{orbit}_{berth}.npy"
                if p_v3.exists():
                    bg_cache[(berth, orbit)] = np.load(p_v3)
                    bg_source_cache[(berth, orbit)] = "bg_v3_fallback"
                    print(f"  BG fallback: {berth} o{orbit} → bg_v3 (primary {cfg['bg_dir'].name} missing)")
                else:
                    bg_cache[(berth, orbit)] = None
                    bg_source_cache[(berth, orbit)] = None
                    print(f"  MISSING BG: {berth} o{orbit} (no v4 or v3)")

    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTHS.items()}

    results = []
    for entry in entries:
        date_str = entry["date_str"]
        orbit    = entry["orbit"]
        scene_dt = entry["scene_dt"]
        print(f"\n{date_str} o{orbit}  scene={scene_dt[:16]}")

        # fetch once per (date, orbit), reuse across berths
        # use sea_island bbox for fetch (all berths share same S1 swath)
        # Actually fetch per berth (different cell bboxes)
        for berth, pts in BERTHS.items():
            cfg = V4_CONFIG[berth]
            bg  = bg_cache.get((berth, orbit))
            bg_src = bg_source_cache.get((berth, orbit))
            if bg is None:
                results.append({"date": date_str, "orbit": orbit, "berth": berth,
                                 "det": "NO_BG", "length_m": None, "aspect": None,
                                 "bg_source": None})
                continue

            linear = fetch_s1_linear(scene_dt, cell_bboxes[berth])
            if linear is None:
                results.append({"date": date_str, "orbit": orbit, "berth": berth,
                                 "det": "ERR", "length_m": None, "aspect": None,
                                 "bg_source": bg_src})
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
            _, best, detected = find_blobs(
                ratio, geom["water_mask"], geom["axis_uvec"],
                merge_gap_m=cfg.get("merge_gap", 0),
                min_len_m=cfg["min_len"],
                min_aspect=cfg.get("aspect_thr", 3.0))

            det_str = "OCC" if detected else "EMP"
            length_m = round(best["length_m"], 1) if (detected and best) else None
            aspect   = round(best["aspect"], 2) if (detected and best) else None
            results.append({"date": date_str, "orbit": orbit, "berth": berth,
                             "det": det_str, "length_m": length_m, "aspect": aspect,
                             "bg_source": bg_src})
            print(f"  {berth}: {det_str}" +
                  (f"  len={length_m:.0f}m asp={aspect:.1f}" if detected else ""))

    # ── CSV output ────────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "berth_v4_history.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date","orbit","berth","det","length_m","aspect","bg_source"])
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"\nSaved: {csv_path} ({len(results)} rows)")

    # ── JSON output for site ──────────────────────────────────────────────────
    # Pivot to {date, orbit} → {berth: det}
    pivot = {}
    for r in results:
        key = (r["date"], r["orbit"])
        if key not in pivot:
            pivot[key] = {"date": r["date"], "orbit": r["orbit"]}
        pivot[key][r["berth"]] = r["det"]

    rows_sorted = sorted(pivot.values(), key=lambda x: (x["date"], x["orbit"]))

    # Summary stats
    occ_total = sum(1 for r in results if r["det"] == "OCC")
    total_valid = sum(1 for r in results if r["det"] in ("OCC","EMP"))

    obj = {
        "generated": today,
        "version": "v4.1",
        "note": "lower bound — detector misses ~1/3 of ships; precision ~92% when OCC",
        "berths": ["sea_island", "east_t_head", "southeast_pier"],
        "stats": {
            "total_berth_dates": total_valid,
            "occupied_detections": occ_total,
        },
        "rows": rows_sorted,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(obj, separators=(",",":")))
    print(f"Saved: {JSON_OUT} ({len(rows_sorted)} date-orbit pairs)")

    # ── Detection summary ────────────────────────────────────────────────────
    print("\n=== Detection summary ===")
    print(f"Period: {HISTORY_START} → {today}")
    print(f"Scene-berth pairs processed: {total_valid}")
    print(f"Detections (OCC): {occ_total}")
    for berth in ["sea_island","east_t_head","southeast_pier"]:
        br = [r for r in results if r["berth"]==berth and r["det"] in ("OCC","EMP")]
        occ = sum(1 for r in br if r["det"]=="OCC")
        print(f"  {berth}: {occ}/{len(br)} occupied")

    # ── Supabase write ────────────────────────────────────────────────────────
    svc_key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    supa_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not svc_key:
        print("\nSUPABASE_SERVICE_KEY not set — history saved to CSV only, DB write skipped.")
        return

    import urllib.request as _ureq
    db_rows = [
        {"date": r["date"], "orbit": r["orbit"], "berth": r["berth"],
         "det": r["det"], "length_m": r["length_m"], "aspect": r["aspect"]}
        for r in results if r["det"] in ("OCC", "EMP")
    ]
    if not supa_url:
        sys.exit("FATAL: SUPABASE_SERVICE_KEY set but SUPABASE_URL is empty")
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
            print(f"\nDB write OK: HTTP {resp.status} — {len(db_rows)} rows upserted to berth_occupancy")
    except Exception as exc:
        sys.exit(f"FATAL: DB write failed — {exc}")


if __name__ == "__main__":
    run()
