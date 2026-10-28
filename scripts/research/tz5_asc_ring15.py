"""
tz5_asc_ring15.py — ТЗ-5 item 2: ring15_far_dB / ring15_near_dB / gap_m_asc
for Yanbu ASC-14 and RT ASC-101 using az=80° (az_source='const').

CDSE is blocked in GHA (HTTP 403 proxy); heading cannot be obtained.
ASC heading ≈ 347° → LOS = (347+90)%360 = 77°. ТЗ expects 78–81°.
Using az=80° as specified in ТЗ-5.

Outputs:
  diagnostics/mega/tz5_asc_ring15_<TS>.csv
  columns: date, scene_id, rel_orbit, platform, terminal, tank_id,
           az_used, az_source, ring15_far_dB, ring15_near_dB, gap_m_asc

Pre-delivery: print доля непустых ring15_far_dB by terminal × rel_orbit.
RT o101 and Yanbu o14 MUST be > 0 before writing file.
"""

import io, math, os, sys, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")

_CID = os.environ.get("CLIENT_ID", "")
_CSC = os.environ.get("CLIENT_SECRET", "")
if not _CID or not _CSC:
    print("FATAL: CLIENT_ID or CLIENT_SECRET empty"); sys.exit(1)

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

# ASC LOS azimuth: const (CDSE blocked, heading unavailable)
ASC_AZ      = 80.0
AZ_SOURCE   = "const"   # heading not obtainable → fixed value

OBS_CSV     = OUT_DIR / "observations_all.csv.gz"
BARREL_PER_M3 = 6.2898


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _crescent_mask(cx, cy, r_px, los_az_deg, width_px, H, W, half_angle=90):
    y, x = np.mgrid[0:H, 0:W]
    dist = np.sqrt((x - cx)**2 + (y - cy)**2)
    ring = (dist >= r_px - width_px) & (dist <= r_px)
    los_rad = math.radians(90 - los_az_deg)
    angle   = np.arctan2(-(y - cy), x - cx)
    diff    = np.abs(((angle - los_rad + math.pi) % (2 * math.pi)) - math.pi)
    return ring & (diff <= math.radians(half_angle))


def _safe_db(pixels):
    v = pixels[np.isfinite(pixels) & (pixels > 0)]
    if len(v) == 0:
        return float("nan")
    return float(10 * math.log10(float(np.mean(v))))


def compute_gap_m(tanks, los_az_deg, max_dist_m=500.0, half_angle_deg=45.0):
    half   = math.radians(half_angle_deg)
    az_rad = math.radians(los_az_deg)

    def _dist(lat1, lon1, lat2, lon2):
        R = 6371000.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + (math.cos(math.radians(lat1)) *
            math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def _bearing(lat1, lon1, lat2, lon2):
        dlon = math.radians(lon2 - lon1)
        y = math.sin(dlon) * math.cos(math.radians(lat2))
        x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
             math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
        return math.atan2(y, x)

    gap = {}
    for t in tanks:
        best = float("nan")
        for other in tanks:
            if other["id"] == t["id"]:
                continue
            d   = _dist(t["lat"], t["lon"], other["lat"], other["lon"])
            brg = _bearing(t["lat"], t["lon"], other["lat"], other["lon"])
            diff = abs(((brg - az_rad + math.pi) % (2 * math.pi)) - math.pi)
            if diff <= half:
                edge_gap = max(0.0, d - t["radius_m"] - other["radius_m"])
                if edge_gap < max_dist_m and (math.isnan(best) or edge_gap < best):
                    best = edge_gap
        gap[t["id"]] = best
    return gap


# ── SentinelHub ring15 fetch ───────────────────────────────────────────────────

def fetch_ring15(terminal_id, rel_orbit, direction, tanks, az_deg, obs_dates):
    """
    Re-fetch SH images for the given terminal/orbit and compute ring15_far/near
    at az_deg. Returns list of row dicts.
    """
    from sentinelhub import (SHConfig, DataCollection, BBox, CRS,
                              SentinelHubRequest, MimeType)

    cfg = SHConfig()
    cfg.sh_client_id     = _CID
    cfg.sh_client_secret = _CSC
    cfg.sh_token_url     = CDSE_TOKEN_URL
    cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

    S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
        f"s1iw_cdse_tz5_{terminal_id}",
        service_url="https://sh.dataspace.copernicus.eu"
    )

    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]

    RES    = 10   # m/px
    BUFFER = 300  # m beyond outermost tank

    bbox = BBox([min(lons) - BUFFER/111320, min(lats) - BUFFER/111320,
                 max(lons) + BUFFER/111320, max(lats) + BUFFER/111320],
                crs=CRS.WGS84)
    W_px = max(1, int((bbox.max_x - bbox.min_x) / (RES / 111320)))
    H_px = max(1, int((bbox.max_y - bbox.min_y) / (RES / 111320)))

    print(f"  {terminal_id} orbit={rel_orbit} {direction}: bbox={W_px}×{H_px} px, "
          f"{len(obs_dates)} dates, az={az_deg}°")

    EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""

    w15 = 15 / RES
    tank_pc = {}
    for tank in tanks:
        cx = int((tank["lon"] - bbox.min_x) / (bbox.max_x - bbox.min_x) * W_px)
        cy = int((bbox.max_y - tank["lat"]) / (bbox.max_y - bbox.min_y) * H_px)
        r  = tank["radius_m"] / RES
        if not (0 <= cx < W_px and 0 <= cy < H_px):
            tank_pc[tank["id"]] = None
            continue
        mh, mw = H_px, W_px
        far_mask  = _crescent_mask(cx, cy, r, az_deg,               w15, mh, mw)
        near_mask = _crescent_mask(cx, cy, r, (az_deg+180)%360,     w15, mh, mw)
        tank_pc[tank["id"]] = {
            "far":  np.where(far_mask),
            "near": np.where(near_mask),
        }

    rows = []
    for d_str in sorted(obs_dates):
        d = datetime.strptime(str(d_str), "%Y-%m-%d")
        try:
            req = SentinelHubRequest(
                evalscript=EVALSCRIPT,
                input_data=[SentinelHubRequest.input_data(
                    data_collection=S1_CDSE,
                    time_interval=(d_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                    other_args={
                        "processing": {"orthorectify": True,
                                       "backCoeff":    "GAMMA0_TERRAIN",
                                       "demInstance":  "COPERNICUS_30"},
                        "dataFilter": {"orbitDirection":    direction.upper(),
                                       "relativeOrbitNumber": int(rel_orbit)},
                        "mosaicking": "ORBIT",
                    },
                )],
                responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                bbox=bbox, size=(W_px, H_px), config=cfg,
            )
            data = req.get_data()
            if not data:
                print(f"    {d_str}: empty"); continue
            img = np.array(data[0], dtype=np.float32).squeeze()
        except Exception as e:
            print(f"    {d_str}: SKIP ({e})"); continue

        H_img, W_img = img.shape
        for tank in tanks:
            pc = tank_pc.get(tank["id"])
            if pc is None:
                continue
            def _pix(rc):
                rr, cc = rc
                m = (rr < H_img) & (cc < W_img)
                return img[rr[m], cc[m]]
            rows.append({
                "date":           d_str,
                "tank_id":        tank["id"],
                "ring15_far_dB":  _safe_db(_pix(pc["far"])),
                "ring15_near_dB": _safe_db(_pix(pc["near"])),
            })
        print(f"    {d_str}: done ({len(tanks)} tanks)")

    return rows


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import pandas as pd
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from tank_registry import LOCATIONS

    print(f"tz5_asc_ring15.py  {datetime.now(timezone.utc).isoformat()}")
    print(f"az_used={ASC_AZ}°  az_source={AZ_SOURCE}")
    print()

    # Build terminal → tanks map
    term_tanks = {loc["id"]: loc["tanks"] for loc in LOCATIONS
                  if loc["id"] in ("yanbu", "ras_tanura")}

    # Target orbits
    TARGETS = [
        ("yanbu",      14,  "ASCENDING"),
        ("ras_tanura", 101, "ASCENDING"),
    ]

    # Load observations to get dates and scene metadata
    if not OBS_CSV.exists():
        print(f"FATAL: {OBS_CSV} not found"); sys.exit(1)
    obs_all = pd.read_csv(OBS_CSV)

    # ── gap_m_asc (pure geometry, no SH) ─────────────────────────────────────
    print("=== gap_m_asc (geometry only, az=80°) ===")
    all_gaps = {}  # tank_id → gap_m
    for (tid, rel_orb, direction) in TARGETS:
        tanks = term_tanks.get(tid, [])
        gaps  = compute_gap_m(tanks, ASC_AZ)
        all_gaps.update(gaps)
        for tank in tanks:
            g = gaps.get(tank["id"], float("nan"))
            print(f"  {tank['id']:20s}  gap_m_asc={g:.1f}" if not math.isnan(g)
                  else f"  {tank['id']:20s}  gap_m_asc=NaN")
    print()

    # ── Ring15 via SentinelHub ─────────────────────────────────────────────────
    print("=== ring15 recomputation via SentinelHub ===")
    all_ring15_rows = []
    for (tid, rel_orb, direction) in TARGETS:
        tanks = term_tanks.get(tid, [])
        # Dates come from p9 pertank CSV if OBS has 0 rows for this orbit
        # (RT o101 not in observations_all — use p9 CSV as date source)
        mask = ((obs_all["terminal_id"] == tid) &
                (obs_all["relative_orbit"] == rel_orb) &
                (obs_all["orbit_direction"] == direction))
        obs_dates = obs_all[mask]["date"].dropna().unique().tolist()

        if len(obs_dates) == 0:
            # Fall back: read p9 CSV for dates
            p9_files = sorted(OUT_DIR.glob("p9_pertank_all_*.csv"))
            if p9_files:
                p9 = pd.read_csv(p9_files[-1])
                p9m = ((p9["terminal"] == tid) & (p9["rel_orbit"] == rel_orb))
                obs_dates = p9[p9m]["date"].dropna().unique().tolist()
                print(f"  {tid} o{rel_orb}: using {len(obs_dates)} dates from p9 CSV")
            else:
                print(f"  {tid} o{rel_orb}: no obs dates found — SKIP")
                continue

        print(f"  {tid} o{rel_orb}: {len(obs_dates)} dates")
        rows = fetch_ring15(tid, rel_orb, direction, tanks, ASC_AZ, obs_dates)
        # Attach metadata from p9/obs
        p9_files = sorted(OUT_DIR.glob("p9_pertank_all_*.csv"))
        p9_meta  = pd.DataFrame()
        if p9_files:
            p9_df = pd.read_csv(p9_files[-1])
            p9_meta = p9_df[p9_df["terminal"] == tid][
                ["date","scene_id","rel_orbit","platform","tank_id"]
            ].drop_duplicates(["date","tank_id"])

        for row in rows:
            row["terminal"]  = tid
            row["rel_orbit"] = rel_orb
            row["az_used"]   = ASC_AZ
            row["az_source"] = AZ_SOURCE
            row["gap_m_asc"] = round(all_gaps.get(row["tank_id"], float("nan")), 1)
            # attach scene_id and platform from p9 metadata
            if not p9_meta.empty:
                hit = p9_meta[(p9_meta["date"] == row["date"]) &
                              (p9_meta["tank_id"] == row["tank_id"])]
                row["scene_id"] = hit["scene_id"].iloc[0] if len(hit) else ""
                row["platform"] = hit["platform"].iloc[0] if len(hit) else ""
            else:
                row["scene_id"] = ""
                row["platform"] = ""

        all_ring15_rows.extend(rows)
        print(f"  {tid} o{rel_orb}: {len(rows)} rows")

    if not all_ring15_rows:
        print("FATAL: no ring15 rows produced"); sys.exit(1)

    df = pd.DataFrame(all_ring15_rows)[[
        "date","scene_id","rel_orbit","platform","terminal","tank_id",
        "az_used","az_source","ring15_far_dB","ring15_near_dB","gap_m_asc"
    ]].sort_values(["terminal","date","tank_id"]).reset_index(drop=True)

    # ── Pre-delivery: доля непустых ring15 by terminal × rel_orbit ────────────
    print()
    print("=== PRE-DELIVERY: доля непустых ring15_far_dB по terminal × rel_orbit ===")
    grp = df.groupby(["terminal","rel_orbit"]).agg(
        n=("ring15_far_dB","size"),
        nn=("ring15_far_dB", lambda x: x.notna().sum()),
    ).reset_index()
    grp["frac"] = grp["nn"] / grp["n"]
    for _, row2 in grp.iterrows():
        print(f"  {row2['terminal']:12s}  o{int(row2['rel_orbit']):3d}  "
              f"n={int(row2['n']):5d}  nn={int(row2['nn']):5d}  frac={row2['frac']:.3f}")
        if row2["frac"] == 0:
            print(f"  FATAL: {row2['terminal']} o{int(row2['rel_orbit'])} ring15_far_dB = 0% non-null")
            sys.exit(1)
    print()

    out = OUT_DIR / f"tz5_asc_ring15_{TS}.csv"
    df.to_csv(out, index=False)
    print(f"WRITTEN: {out.name}  ({len(df)} rows)")


if __name__ == "__main__":
    main()
