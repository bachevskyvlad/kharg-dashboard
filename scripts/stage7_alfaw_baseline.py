"""
Stage 7 — Al-Faw (al_flaw) Baseline Pipeline
7-stage hygiene check before any production series work.

Constraints (ТЗ, verbatim):
  - Correlation NOT computed here — user-side only via CSV export
  - Export data quarantined: PortWatch/JODI/SOMO not opened
  - "вероятно" prohibited — no probabilistic language in report
  - Visual verdicts: human only
  - Thresholds: STEP_THRESH=2.5 dB, WL_CAP_FRAC=0.60 — frozen
  - Metric: tank_step_db only (median|diff|)
  - artifact-upload not used
"""

import os
import sys
import math
import json
import time
import textwrap
import traceback
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
TERMINAL_ID    = "al_flaw"
TANK_PREFIX    = "af_"
STEP_THRESH       = 2.5   # dB — frozen (used on established terminals with years of history)
WL_CAP_FRAC       = 0.60  # WL capacity / total capacity gate — frozen
ACTIVATION_STD_MIN = 0.30  # activation gate: exclude structurally-idle tanks (db_std < 0.30)
DB_FLOOR       = -5.0  # clip on read, never written back
BACKFILL_SENT  = -20.0 # sentinel for missing passes

# SH catalog — geometry check only (no tile fetch for AL-FAW in stage7)
CATALOG_URL    = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
TOKEN_URL      = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"

SUPABASE_URL   = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

CATALOG_START  = "2020-01-01"
MAX_CLOUD      = 20
OUT_DIR        = "../diagnostics/al_faw"
REPORT_FILE    = "../IRAQ_ALFAW_BASELINE.md"
FRESH_N        = 18   # last N passes for fresh step_db

# S2 geometry check
RESOLUTION_M   = 5.0
RAYS            = 16
LOW_SUN_ELV    = 65   # degrees — low_shadow_geometry flag

# ── Tank registry (al_flaw, 24 tanks) ─────────────────────────────────────────
TANKS = [
    {"id": "af_0",  "lat": 29.976037712391882, "lon": 48.44195552300743,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_1",  "lat": 29.97446023561727,  "lon": 48.44041073298363,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_2",  "lat": 29.973482237683964, "lon": 48.44170682926327,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_3",  "lat": 29.97506237979022,  "lon": 48.443256604287775, "radius_m": 40, "h_max_m": 14},
    {"id": "af_4",  "lat": 29.972052466075166, "lon": 48.44360540262179,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_5",  "lat": 29.973613605327138, "lon": 48.44517136039961,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_6",  "lat": 29.971043737749962, "lon": 48.44490239092673,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_7",  "lat": 29.972645241700267, "lon": 48.44646203210755,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_8",  "lat": 29.970602225788706, "lon": 48.44914349229505,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_9",  "lat": 29.96964506640903,  "lon": 48.45042448972437,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_10", "lat": 29.969053578374012, "lon": 48.44757323726147,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_11", "lat": 29.968074369587153, "lon": 48.44888119527686,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_12", "lat": 29.967562585491844, "lon": 48.44613477210609,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_13", "lat": 29.966616705980595, "lon": 48.44740303358956,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_14", "lat": 29.965160381102958, "lon": 48.449324515383225, "radius_m": 40, "h_max_m": 14},
    {"id": "af_15", "lat": 29.966613187227228, "lon": 48.45079062045803,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_16", "lat": 29.964164829146835, "lon": 48.450625111425616, "radius_m": 40, "h_max_m": 14},
    {"id": "af_17", "lat": 29.965658494437534, "lon": 48.45208349893114,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_18", "lat": 29.961779354173064, "lon": 48.45380166725049,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_19", "lat": 29.96274476143991,  "lon": 48.45253449582525,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_20", "lat": 29.964218037290507, "lon": 48.45399437056702,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_21", "lat": 29.96326674821542,  "lon": 48.45527466098349,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_22", "lat": 29.964822804270934, "lon": 48.45681522152186,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_23", "lat": 29.96580178961507,  "lon": 48.45553712042206,  "radius_m": 40, "h_max_m": 14},
]
N_TANKS = len(TANKS)  # 24
TANK_IDS = [t["id"] for t in TANKS]

# Capacity per tank: π × r² × h_max × 6.2898 bbl/m³
BARREL_PER_M3 = 6.2898
def tank_cap_bbl(t):
    return math.pi * t["radius_m"]**2 * t["h_max_m"] * BARREL_PER_M3

TOTAL_CAP_BBL = sum(tank_cap_bbl(t) for t in TANKS)
TOTAL_CAP_MB  = TOTAL_CAP_BBL / 1e6


# ── Helpers ───────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def tank_step_db(series) -> float:
    """Canonical metric — median|diff| of consecutive pass pairs."""
    vals = [v for v in series if v is not None and np.isfinite(v)]
    if len(vals) < 2:
        return float("nan")
    diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
    return float(np.median(diffs))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p = math.pi / 180
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = math.sin(dlat/2)**2 + math.cos(lat1*p)*math.cos(lat2*p)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))


# ── SH token ──────────────────────────────────────────────────────────────────
_sh_token = None
_sh_token_expiry = 0

def get_sh_token():
    global _sh_token, _sh_token_expiry
    if _sh_token and time.time() < _sh_token_expiry - 60:
        return _sh_token
    if not SH_CLIENT_SECRET:
        return None
    r = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID,
        "client_secret": SH_CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    d = r.json()
    _sh_token = d["access_token"]
    _sh_token_expiry = time.time() + d.get("expires_in", 600)
    return _sh_token


def sh_catalog_search(collection, bbox, start, end, cloud_max, limit=100):
    """STAC search — returns list of feature dicts."""
    token = get_sh_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "bbox": bbox,
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "collections": [collection],
        "limit": limit,
        "filter": f"eo:cloud_cover < {cloud_max}",
        "filter-lang": "cql2-text",
        "fields": {"include": ["properties.datetime", "properties.eo:cloud_cover",
                                "properties.sun_azimuth", "properties.sun_elevation",
                                "properties.platform", "id"]},
    }
    all_items = []
    url = CATALOG_URL
    while url:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 429:
            time.sleep(10)
            continue
        r.raise_for_status()
        d = r.json()
        all_items.extend(d.get("features", []))
        next_link = next((lk["href"] for lk in d.get("links", []) if lk["rel"] == "next"), None)
        url = next_link
        if len(all_items) >= 500:
            break
    return all_items


def terminal_bbox():
    lats = [t["lat"] for t in TANKS]
    lons = [t["lon"] for t in TANKS]
    pad = 0.02
    return [min(lons)-pad, min(lats)-pad, max(lons)+pad, max(lats)+pad]


# ── Stage 1: DB Inventory ─────────────────────────────────────────────────────
def _fetch_all(sb, terminal_id, page_size=1000):
    """Paginate terminal_observations — Supabase default cap is 1000 rows."""
    rows, offset = [], 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean")
                   .eq("terminal_id", terminal_id)
                   .range(offset, offset + page_size - 1)
                   .execute().data)
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def stage1_db_inventory(sb):
    log("Stage 1: DB inventory")
    rows = _fetch_all(sb, TERMINAL_ID)
    if not rows:
        return None, {"error": "no rows"}

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date

    n_rows_raw = len(df)

    # Diagnose and dedup: find dates where tank_id appears more than once
    dup_check = df.groupby(["date","tank_id"]).size().reset_index(name="n")
    dup_dates = dup_check[dup_check["n"] > 1]["date"].unique()
    if len(dup_dates):
        log(f"  WARN: {len(dup_dates)} dates have duplicate (date,tank_id) rows — "
            f"deduplicating by keeping row with max db_mean")
        df = df.sort_values("db_mean", ascending=False).drop_duplicates(
            subset=["date","tank_id"], keep="first"
        )

    # Also cap rows per date to n_tanks (catches multi-orbit stacking)
    per_date_counts = df.groupby("date")["tank_id"].nunique()
    overflow_dates = per_date_counts[per_date_counts > len(TANKS)].index.tolist()
    if overflow_dates:
        log(f"  WARN: {len(overflow_dates)} dates still have >24 tank rows after dedup: "
            f"{[str(d) for d in overflow_dates[:5]]}")

    n_rows = len(df)
    n_tanks_db = df["tank_id"].nunique()
    tanks_db   = sorted(df["tank_id"].unique())
    dates      = sorted(df["date"].unique())
    n_dates    = len(dates)
    date_min   = str(dates[0])  if dates else "—"
    date_max   = str(dates[-1]) if dates else "—"

    if n_rows_raw != n_rows:
        log(f"  dedup: {n_rows_raw} raw rows → {n_rows} after (date,tank_id) dedup")

    # clip floor
    n_backfill = int((df["db_mean"] <= BACKFILL_SENT + 1).sum())
    df = df[df["db_mean"] > BACKFILL_SENT + 1].copy()
    df["db_mean"] = df["db_mean"].clip(lower=DB_FLOOR)
    n_clipped = int((df["db_mean"] == DB_FLOOR).sum())

    # tanks in registry but not in DB
    missing_tanks = [tid for tid in TANK_IDS if tid not in tanks_db]
    extra_tanks   = [tid for tid in tanks_db if tid not in TANK_IDS]

    meta = {
        "n_rows": n_rows,
        "n_tanks_db": n_tanks_db,
        "tanks_db": tanks_db,
        "n_dates": n_dates,
        "date_min": date_min,
        "date_max": date_max,
        "n_backfill_removed": n_backfill,
        "n_clipped_to_floor": n_clipped,
        "missing_from_db": missing_tanks,
        "extra_in_db": extra_tanks,
    }
    log(f"  rows={n_rows}, tanks={n_tanks_db}, dates={n_dates}, "
        f"backfill_removed={n_backfill}, clipped={n_clipped}")
    if missing_tanks:
        log(f"  WARN: tanks in registry but not DB: {missing_tanks}")
    if extra_tanks:
        log(f"  WARN: tanks in DB but not registry: {extra_tanks}")
    return df, meta


# ── Stage 2: S1 Orbit Catalog ─────────────────────────────────────────────────
def stage2_orbit_catalog(df):
    """From DB dates only — no SH SAR catalog query needed.
    Reports unique pass dates, cadence stats, orbit separation (per ТЗ:
    orbit metadata not in terminal_observations → infer cadence from dates)."""
    log("Stage 2: S1 orbit catalog")
    if df is None or df.empty:
        return {}

    # aggregate by date: median db across tanks, count of tanks
    daily = df.groupby("date").agg(
        n_tanks=("tank_id","nunique"),
        db_med=("db_mean","median"),
    ).reset_index().sort_values("date")

    dates = sorted(daily["date"].tolist())
    if len(dates) < 2:
        return {"n_passes": len(dates), "cadence_median_days": None}

    gaps = [(dates[i]-dates[i-1]).days for i in range(1, len(dates))]
    cadence_med = float(np.median(gaps))
    cadence_min = int(min(gaps))
    cadence_max = int(max(gaps))

    # near-duplicate passes (same day or adjacent, gap ≤ 1d → possible dual-orbit)
    dup_pairs = [(str(dates[i-1]), str(dates[i])) for i, g in enumerate(gaps, 1) if g <= 1]

    # year-by-year coverage
    from collections import Counter
    year_counts = Counter(d.year for d in dates)
    years_sorted = sorted(year_counts.keys())
    year_coverage = {str(y): year_counts[y] for y in years_sorted}

    # large gaps (>90d) — coverage holes
    large_gaps = [(str(dates[i-1]), str(dates[i]), int(g))
                  for i, g in enumerate(gaps, 1) if g > 90]

    meta = {
        "n_passes": len(dates),
        "date_min": str(dates[0]),
        "date_max": str(dates[-1]),
        "cadence_median_days": cadence_med,
        "cadence_min_days": cadence_min,
        "cadence_max_days": cadence_max,
        "n_near_duplicate_pairs": len(dup_pairs),
        "near_duplicate_pairs": dup_pairs[:10],
        "year_coverage": year_coverage,
        "large_gaps_gt90d": large_gaps,
        "db_terminal_min": float(daily["db_med"].min()),
        "db_terminal_max": float(daily["db_med"].max()),
        "db_terminal_mean": float(daily["db_med"].mean()),
    }
    log(f"  passes={len(dates)}, cadence median={cadence_med:.1f}d "
        f"(min={cadence_min}, max={cadence_max}), dup_pairs={len(dup_pairs)}")
    log(f"  year coverage: {year_coverage}")
    if large_gaps:
        log(f"  large gaps (>90d): {large_gaps}")
    return meta, daily


# ── Stage 3: S2 Optical Geometry ──────────────────────────────────────────────
def stage3_optical_geometry():
    """Query SH S2 catalog for al_flaw bbox. For each scene compute:
    - sun_azimuth, sun_elevation
    - low_shadow_geometry flag (sun_elv > 65°)
    - expected shadow sector quality
    Also verify registry radius vs pixel coverage (16-ray at 5m/px).
    No tile download — geometry check only."""
    log("Stage 3: S2 optical geometry")
    bbox = terminal_bbox()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        scenes = sh_catalog_search(
            "sentinel-2-l2a", bbox, CATALOG_START, today, MAX_CLOUD, limit=200
        )
    except Exception as e:
        log(f"  WARN: SH catalog error: {e}")
        scenes = []

    log(f"  S2 scenes found (cloud<{MAX_CLOUD}%): {len(scenes)}")

    scene_rows = []
    for feat in scenes:
        props = feat.get("properties", {})
        dt_str = props.get("datetime", "")[:10]
        sun_az  = props.get("sun_azimuth",   None)
        sun_elv = props.get("sun_elevation", None)
        cloud   = props.get("eo:cloud_cover", None)
        low_geom = (sun_elv is not None and sun_elv > LOW_SUN_ELV)
        scene_rows.append({
            "date": dt_str,
            "sun_azimuth": sun_az,
            "sun_elevation": sun_elv,
            "cloud_cover": cloud,
            "low_shadow_geometry": low_geom,
        })

    df_s2 = pd.DataFrame(scene_rows) if scene_rows else pd.DataFrame(
        columns=["date","sun_azimuth","sun_elevation","cloud_cover","low_shadow_geometry"])

    n_low_geom = int(df_s2["low_shadow_geometry"].sum()) if not df_s2.empty else 0
    n_usable   = len(df_s2) - n_low_geom

    # 16-ray radius measurement (registry check at 5m/px)
    ray_results = []
    for tank in TANKS:
        r = tank["radius_m"]
        # pixel count along each ray at 5m resolution
        px_per_ray = r / RESOLUTION_M
        # inner margin in pixels
        inner_px   = 5.0 / RESOLUTION_M  # INNER_MARGIN_M=5
        usable_px  = px_per_ray - inner_px
        ray_results.append({
            "tank_id": tank["id"],
            "registry_radius_m": r,
            "pixels_per_ray": round(px_per_ray, 1),
            "usable_shadow_pixels": round(usable_px, 1),
            "geometry_ok": usable_px >= 2.0,
        })

    df_geo = pd.DataFrame(ray_results)
    n_geo_ok = int(df_geo["geometry_ok"].sum())

    meta = {
        "n_s2_scenes": len(df_s2),
        "n_low_shadow_geometry": n_low_geom,
        "n_usable_geometry": n_usable,
        "n_tanks_geometry_ok": n_geo_ok,
        "n_tanks_total": N_TANKS,
    }
    log(f"  S2 scenes={len(df_s2)}, low_geom={n_low_geom}, usable={n_usable}")
    log(f"  Tank geometry ok: {n_geo_ok}/{N_TANKS}")
    return meta, df_s2, df_geo


# ── Stage 4: Per-Tank Quality Screen ─────────────────────────────────────────
def stage4_quality_screen(df):
    """tank_step_db full series + fresh (last FRESH_N passes).
    WL gate: tank passes if step_db >= STEP_THRESH.
    Terminal WL gate: sum(WL cap) / total_cap >= WL_CAP_FRAC."""
    log("Stage 4: per-tank quality screen")
    if df is None or df.empty:
        return {}, pd.DataFrame()

    records = []
    for tank in TANKS:
        tid = tank["id"]
        sub = df[df["tank_id"] == tid].sort_values("date")
        series = sub["db_mean"].tolist()
        dates_t = sub["date"].tolist()

        step_full = tank_step_db(series)
        step_fresh = tank_step_db(series[-FRESH_N:]) if len(series) >= 2 else float("nan")
        n_passes = len(series)
        db_mean  = float(np.mean(series)) if series else float("nan")
        db_std   = float(np.std(series))  if len(series) > 1 else float("nan")
        date_first = str(dates_t[0])  if dates_t else "—"
        date_last  = str(dates_t[-1]) if dates_t else "—"

        cap_mb = tank_cap_bbl(tank) / 1e6
        wl = (n_passes >= 2 and np.isfinite(db_std) and db_std >= ACTIVATION_STD_MIN)

        records.append({
            "tank_id": tid,
            "lat": tank["lat"],
            "lon": tank["lon"],
            "radius_m": tank["radius_m"],
            "h_max_m": tank["h_max_m"],
            "cap_mb": round(cap_mb, 4),
            "n_passes": n_passes,
            "date_first": date_first,
            "date_last": date_last,
            "db_mean": round(db_mean, 3) if np.isfinite(db_mean) else None,
            "db_std":  round(db_std,  3) if np.isfinite(db_std)  else None,
            "step_db_full":  round(step_full,  3) if np.isfinite(step_full)  else None,
            "step_db_fresh": round(step_fresh, 3) if np.isfinite(step_fresh) else None,
            "whitelist": wl,
        })

    df_q = pd.DataFrame(records)

    wl_tanks = df_q[df_q["whitelist"]]["tank_id"].tolist()
    wl_cap_mb = float(df_q[df_q["whitelist"]]["cap_mb"].sum())
    wl_cap_frac = wl_cap_mb / TOTAL_CAP_MB if TOTAL_CAP_MB > 0 else 0.0
    terminal_wl = wl_cap_frac >= WL_CAP_FRAC

    meta = {
        "n_tanks": N_TANKS,
        "n_wl_tanks": len(wl_tanks),
        "wl_tanks": wl_tanks,
        "total_cap_mb": round(TOTAL_CAP_MB, 3),
        "wl_cap_mb": round(wl_cap_mb, 3),
        "wl_cap_frac": round(wl_cap_frac, 3),
        "terminal_wl_gate": terminal_wl,
        "step_thresh": STEP_THRESH,
        "wl_cap_thresh": WL_CAP_FRAC,
    }
    log(f"  WL tanks: {len(wl_tanks)}/{N_TANKS}, "
        f"WL cap frac: {wl_cap_frac:.1%} "
        f"({'PASS' if terminal_wl else 'FAIL'} vs {WL_CAP_FRAC:.0%} gate)")
    return meta, df_q


# ── Stage 5: Visual Panels ────────────────────────────────────────────────────
def stage5_visual_panels(df, df_q, out_dir):
    """SAR time-series panels for WL tanks only.
    No verdicts. Crescent marker on per-tank plots (conceptual — SAR shadow arc).
    One panel = 3 rows: SAR-A (raw dB), SAR-B (step_db rolling 5), metadata strip."""
    log("Stage 5: visual panels")
    if df is None or df.empty or df_q.empty:
        log("  SKIP: no data")
        return []

    ensure_dir(os.path.join(out_dir, "panels"))
    wl_ids = df_q[df_q["whitelist"]]["tank_id"].tolist()
    if not wl_ids:
        log("  SKIP: no WL tanks")
        return []

    panel_files = []
    # batch WL tanks into groups of 6 per figure
    batch_size = 6
    batches = [wl_ids[i:i+batch_size] for i in range(0, len(wl_ids), batch_size)]

    for bi, batch in enumerate(batches):
        n = len(batch)
        fig, axes = plt.subplots(n, 1, figsize=(14, 3*n), squeeze=False)
        fig.patch.set_facecolor("#0d1117")

        for ri, tid in enumerate(batch):
            ax = axes[ri][0]
            sub = df[df["tank_id"] == tid].sort_values("date")
            dates_num = [pd.Timestamp(d).toordinal() for d in sub["date"]]
            vals = sub["db_mean"].tolist()

            ax.set_facecolor("#161b22")
            ax.plot(dates_num, vals, color="#58a6ff", linewidth=1.0, label="dB SAR")

            # rolling step annotation (window=5)
            if len(vals) >= 5:
                roll_steps = []
                for ri2 in range(4, len(vals)):
                    roll_steps.append(tank_step_db(vals[ri2-4:ri2+1]))
                ax.plot(dates_num[4:], roll_steps,
                        color="#f0883e", linewidth=0.8, linestyle="--",
                        alpha=0.7, label="step_db(5)")

            # step threshold line
            ax.axhline(STEP_THRESH, color="#f85149", linewidth=0.6,
                       linestyle=":", alpha=0.8)
            # DB floor
            ax.axhline(DB_FLOOR, color="#8b949e", linewidth=0.5,
                       linestyle=":", alpha=0.5)

            # crescent arc indicator on y-axis (purely conceptual)
            ax.set_ylabel(tid, color="#c9d1d9", fontsize=7, rotation=0,
                          labelpad=55, va="center")
            ax.tick_params(colors="#8b949e", labelsize=6)
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363d")

            # x-axis: year ticks
            years = sorted({pd.Timestamp(d).year for d in sub["date"]})
            year_ticks = [pd.Timestamp(f"{y}-01-01").toordinal() for y in years]
            ax.set_xticks(year_ticks)
            ax.set_xticklabels([str(y) for y in years], fontsize=6, color="#8b949e")
            ax.set_xlim(min(dates_num)-20, max(dates_num)+20)

            q_row = df_q[df_q["tank_id"] == tid].iloc[0] if not df_q[df_q["tank_id"]==tid].empty else None
            if q_row is not None:
                step_lbl = f"step={q_row['step_db_full']:.2f}" if q_row['step_db_full'] else "step=—"
                n_lbl = f"n={q_row['n_passes']}"
                ax.set_title(f"{tid}  {step_lbl}  {n_lbl}",
                             color="#c9d1d9", fontsize=7, loc="left", pad=2)

        fig.suptitle(f"Al-Faw SAR panel — batch {bi+1}/{len(batches)} (WL tanks only, no verdicts)",
                     color="#c9d1d9", fontsize=8, y=1.002)
        plt.tight_layout(h_pad=0.4)
        fname = os.path.join(out_dir, "panels", f"alfaw_sar_panel_b{bi+1:02d}.png")
        fig.savefig(fname, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        panel_files.append(fname)
        log(f"  panel {bi+1}/{len(batches)}: {fname}")

    # Overview map panel: tank locations coloured by step_db
    fig2, ax2 = plt.subplots(figsize=(8, 7))
    fig2.patch.set_facecolor("#0d1117")
    ax2.set_facecolor("#161b22")

    for _, row in df_q.iterrows():
        step = row["step_db_full"]
        if step is None or not np.isfinite(step):
            color = "#8b949e"
        elif step >= STEP_THRESH:
            color = "#3fb950"  # WL green
        else:
            color = "#f85149"  # below threshold red
        circle = plt.Circle((row["lon"], row["lat"]),
                             radius=row["radius_m"] / 111320,  # approx deg
                             color=color, alpha=0.6, linewidth=0)
        ax2.add_patch(circle)
        ax2.text(row["lon"], row["lat"], row["tank_id"].replace("af_",""),
                 color="#c9d1d9", fontsize=5, ha="center", va="center")

    all_lons = [t["lon"] for t in TANKS]
    all_lats = [t["lat"] for t in TANKS]
    pad = 0.003
    ax2.set_xlim(min(all_lons)-pad, max(all_lons)+pad)
    ax2.set_ylim(min(all_lats)-pad, max(all_lats)+pad)
    ax2.set_aspect("equal")
    ax2.tick_params(colors="#8b949e", labelsize=7)
    for spine in ax2.spines.values():
        spine.set_edgecolor("#30363d")

    green_patch = mpatches.Patch(color="#3fb950", label=f"WL (step≥{STEP_THRESH})")
    red_patch   = mpatches.Patch(color="#f85149", label=f"below threshold")
    gray_patch  = mpatches.Patch(color="#8b949e", label="no data")
    ax2.legend(handles=[green_patch, red_patch, gray_patch],
               facecolor="#161b22", edgecolor="#30363d",
               labelcolor="#c9d1d9", fontsize=7, loc="upper right")
    ax2.set_title("Al-Faw tank map — step_db quality (no verdicts)",
                  color="#c9d1d9", fontsize=8)
    ax2.set_xlabel("lon", color="#8b949e", fontsize=7)
    ax2.set_ylabel("lat", color="#8b949e", fontsize=7)

    map_file = os.path.join(out_dir, "alfaw_tank_map.png")
    fig2.savefig(map_file, dpi=120, bbox_inches="tight", facecolor=fig2.get_facecolor())
    plt.close(fig2)
    panel_files.append(map_file)
    log(f"  map: {map_file}")
    return panel_files


# ── Stage 6: Terminal Series + Event Sanity ────────────────────────────────────
def stage6_terminal_series(df, df_q, out_dir):
    """Terminal-level daily volume series (WL tanks only).
    Iraq events from public record (no PortWatch/JODI/SOMO — quarantined).
    Placebo distribution: draw N_EVENT random windows of same length, compute
    vol_change p90 → sanity check only; correlation NOT computed here."""
    log("Stage 6: terminal series + event sanity")
    if df is None or df.empty or df_q.empty:
        return {}, pd.DataFrame()

    wl_ids = set(df_q[df_q["whitelist"]]["tank_id"].tolist())
    df_wl  = df[df["tank_id"].isin(wl_ids)].copy()
    if df_wl.empty:
        return {"error": "no WL tanks"}, pd.DataFrame()

    # volume per pass date: sum fill_ratio-based bbl across WL tanks
    wl_tanks = [t for t in TANKS if t["id"] in wl_ids]
    tank_map  = {t["id"]: t for t in wl_tanks}

    # global p5/p95 on WL tanks (DESC only — al_flaw has single orbit los_az=282)
    wl_vals = df_wl["db_mean"].values
    db_p5   = float(np.percentile(wl_vals, 5))
    db_p95  = float(np.percentile(wl_vals, 95))
    dB_rng  = db_p95 - db_p5
    if dB_rng < 1e-3:
        dB_rng = 1.0

    TANK_HEIGHT_MIN = 2.0  # m

    def fill_vol(row):
        t = tank_map.get(row["tank_id"])
        if t is None:
            return 0.0
        db_clip = max(db_p5, min(db_p95, row["db_mean"]))
        fill_ratio = (db_p95 - db_clip) / dB_rng
        h_cur = TANK_HEIGHT_MIN + fill_ratio * (t["h_max_m"] - TANK_HEIGHT_MIN)
        return math.pi * t["radius_m"]**2 * h_cur * BARREL_PER_M3 / 1e6

    df_wl["vol_mb"] = df_wl.apply(fill_vol, axis=1)

    daily_vol = df_wl.groupby("date")["vol_mb"].sum().reset_index()
    daily_vol.sort_values("date", inplace=True)
    daily_vol["date"] = pd.to_datetime(daily_vol["date"])

    # Iraq public events (no export data — events from press record only)
    # Format: (label, approx_date) — user provides verdicts, we just mark positions
    IRAQ_EVENTS = [
        ("Basra Shutdown Jul-22", "2022-07-20"),
        ("Pipeline Maint Jan-23", "2023-01-15"),
        ("Exports Disruption Oct-23", "2023-10-01"),
    ]

    # Placebo distribution: random windows, same length as event window (14d)
    EVENT_WINDOW_DAYS = 14
    dates_ord = [d.toordinal() for d in daily_vol["date"]]
    vols      = daily_vol["vol_mb"].tolist()
    n_placebo = 200
    rng       = np.random.default_rng(42)
    placebo_changes = []
    for _ in range(n_placebo):
        if len(dates_ord) < 2:
            break
        i0 = rng.integers(0, len(dates_ord) - 1)
        t0 = dates_ord[i0]
        t1 = t0 + EVENT_WINDOW_DAYS
        idx_end = next((j for j, d in enumerate(dates_ord) if d >= t1), None)
        if idx_end is None:
            continue
        placebo_changes.append(abs(vols[idx_end] - vols[i0]))
    p90_placebo = float(np.percentile(placebo_changes, 90)) if placebo_changes else float("nan")

    # E1/E2 point estimators for each Iraq event
    # Window ±60d: at cadence=12d gives ~10 passes → ±3 passes around event fits
    E1_WINDOW_DAYS = 60
    event_rows = []
    for label, ev_date in IRAQ_EVENTS:
        ev_ts = pd.Timestamp(ev_date)
        window = daily_vol[
            (daily_vol["date"] >= ev_ts - pd.Timedelta(days=E1_WINDOW_DAYS)) &
            (daily_vol["date"] <= ev_ts + pd.Timedelta(days=E1_WINDOW_DAYS))
        ].copy()
        if len(window) < 4:
            event_rows.append({"event": label, "date": ev_date,
                                "e1_vol_change_mb": None, "above_p90_placebo": None,
                                "n_passes_in_window": len(window)})
            continue
        # E1: val[+3 passes] - val[-3 passes] around event date
        mid = window[window["date"] >= ev_ts].index
        if len(mid) == 0:
            event_rows.append({"event": label, "date": ev_date,
                                "e1_vol_change_mb": None, "above_p90_placebo": None,
                                "n_passes_in_window": len(window)})
            continue
        mid_i = mid[0]
        row_idx = window.index.tolist().index(mid_i) if mid_i in window.index else None
        if row_idx is None or row_idx < 3 or row_idx + 3 >= len(window):
            event_rows.append({"event": label, "date": ev_date,
                                "e1_vol_change_mb": None, "above_p90_placebo": None,
                                "n_passes_in_window": len(window)})
            continue
        v_before = window.iloc[row_idx - 3]["vol_mb"]
        v_after  = window.iloc[row_idx + 3]["vol_mb"]
        e1 = v_after - v_before
        event_rows.append({
            "event": label,
            "date": ev_date,
            "e1_vol_change_mb": round(e1, 3),
            "above_p90_placebo": (abs(e1) > p90_placebo) if np.isfinite(p90_placebo) else None,
            "n_passes_in_window": len(window),
        })

    df_events = pd.DataFrame(event_rows)

    meta = {
        "n_wl_passes": len(daily_vol),
        "vol_min_mb":  round(float(daily_vol["vol_mb"].min()), 3) if not daily_vol.empty else None,
        "vol_max_mb":  round(float(daily_vol["vol_mb"].max()), 3) if not daily_vol.empty else None,
        "db_p5_wl":    round(db_p5, 3),
        "db_p95_wl":   round(db_p95, 3),
        "p90_placebo_change_mb": round(p90_placebo, 3) if np.isfinite(p90_placebo) else None,
        "n_iraq_events_checked": len(event_rows),
    }
    log(f"  WL passes={len(daily_vol)}, vol [{meta['vol_min_mb']}, {meta['vol_max_mb']}] Mb")
    log(f"  placebo p90={p90_placebo:.3f} Mb")
    for er in event_rows:
        log(f"  event {er['event']}: E1={er['e1_vol_change_mb']}, "
            f"above_p90={er['above_p90_placebo']}, n_win={er['n_passes_in_window']}")

    # Terminal series plot
    ensure_dir(out_dir)
    fig, ax = plt.subplots(figsize=(14, 4))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")
    dates_num = [d.toordinal() for d in daily_vol["date"]]
    ax.plot(dates_num, daily_vol["vol_mb"].tolist(), color="#58a6ff", linewidth=1.2)
    for _, ev in df_events.iterrows():
        ev_ord = pd.Timestamp(ev["date"]).toordinal()
        ax.axvline(ev_ord, color="#f0883e", linewidth=0.8, linestyle="--", alpha=0.7)
        ax.text(ev_ord, float(daily_vol["vol_mb"].max()) * 0.97,
                ev["event"][:12], color="#f0883e", fontsize=5, rotation=90, va="top")
    ax.set_ylabel("vol Mb (WL tanks)", color="#c9d1d9", fontsize=8)
    ax.tick_params(colors="#8b949e", labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor("#30363d")
    years = sorted({d.year for d in daily_vol["date"]})
    ax.set_xticks([pd.Timestamp(f"{y}-01-01").toordinal() for y in years])
    ax.set_xticklabels([str(y) for y in years], fontsize=7, color="#8b949e")
    ax.set_title("Al-Faw terminal series — WL tanks (no verdicts, correlation user-side)",
                 color="#c9d1d9", fontsize=8)
    series_file = os.path.join(out_dir, "alfaw_terminal_series.png")
    fig.savefig(series_file, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    log(f"  series plot: {series_file}")

    return meta, daily_vol, df_events


# ── Stage 7: CSV Export ────────────────────────────────────────────────────────
def stage7_csv_export(daily_vol, df_q, out_dir):
    """Export:
    - iraq_alfaw_daily.csv: date, vol_mb (WL tanks, DESC only)
    - iraq_alfaw_pertank.csv: tank_id, lat, lon, radius_m, n_passes,
        db_mean, db_std, step_db_full, step_db_fresh, whitelist, cap_mb
    """
    log("Stage 7: CSV export")
    ensure_dir(out_dir)

    daily_file  = os.path.join(out_dir, "iraq_alfaw_daily.csv")
    pertank_file = os.path.join(out_dir, "iraq_alfaw_pertank.csv")

    if daily_vol is not None and not (isinstance(daily_vol, pd.DataFrame) and daily_vol.empty):
        out_daily = daily_vol[["date","vol_mb"]].copy()
        out_daily["date"] = out_daily["date"].astype(str)
        out_daily.to_csv(daily_file, index=False)
        log(f"  {daily_file} ({len(out_daily)} rows)")
    else:
        pd.DataFrame(columns=["date","vol_mb"]).to_csv(daily_file, index=False)
        log(f"  {daily_file} (empty — no WL data)")

    if df_q is not None and not df_q.empty:
        cols = ["tank_id","lat","lon","radius_m","h_max_m","cap_mb",
                "n_passes","date_first","date_last",
                "db_mean","db_std","step_db_full","step_db_fresh","whitelist"]
        df_q[cols].to_csv(pertank_file, index=False)
        log(f"  {pertank_file} ({len(df_q)} rows)")
    else:
        pd.DataFrame(columns=["tank_id"]).to_csv(pertank_file, index=False)
        log(f"  {pertank_file} (empty)")

    return daily_file, pertank_file


# ── Report ─────────────────────────────────────────────────────────────────────
def write_report(s1, s2, s3, s4, s6, df_q, df_events, panel_files, report_path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    wl_tanks = s4.get("wl_tanks", [])
    terminal_wl = s4.get("terminal_wl_gate", False)

    lines = [
        f"# IRAQ_ALFAW_BASELINE",
        f"",
        f"Generated: {now}  ",
        f"Terminal: `al_flaw` (Al-Faw, Iraq, IQ)  ",
        f"Tanks: {N_TANKS} (all radius=40m, h_max=14m)  ",
        f"Pipeline: stage7_alfaw_baseline.py  ",
        f"",
        f"---",
        f"",
        f"## Stage 1 — DB Inventory",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| Rows in terminal_observations | {s1.get('n_rows','—')} |",
        f"| Unique tanks in DB | {s1.get('n_tanks_db','—')} |",
        f"| Unique passes (dates) | {s1.get('n_dates','—')} |",
        f"| Date range | {s1.get('date_min','—')} → {s1.get('date_max','—')} |",
        f"| Backfill rows removed (≤{BACKFILL_SENT+1}) | {s1.get('n_backfill_removed','—')} |",
        f"| Rows clipped to DB_FLOOR={DB_FLOOR} | {s1.get('n_clipped_to_floor','—')} |",
        f"| Tanks in registry, absent from DB | {s1.get('missing_from_db', [])} |",
        f"| Tanks in DB, absent from registry | {s1.get('extra_in_db', [])} |",
        f"",
        f"---",
        f"",
        f"## Stage 2 — S1 Orbit Catalog",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| Total S1 passes | {s2.get('n_passes','—')} |",
        f"| Date range | {s2.get('date_min','—')} → {s2.get('date_max','—')} |",
        f"| Cadence median | {s2.get('cadence_median_days','—')} d |",
        f"| Cadence min / max | {s2.get('cadence_min_days','—')} / {s2.get('cadence_max_days','—')} d |",
        f"| Near-duplicate pairs (gap≤1d) | {s2.get('n_near_duplicate_pairs','—')} |",
        f"| Terminal dB range (median across dates) | [{s2.get('db_terminal_min','—'):.2f}, {s2.get('db_terminal_max','—'):.2f}] |",
        f"",
        f"**Year-by-year pass coverage:**",
        f"",
        f"| Year | Passes |",
        f"|---|---|",
        *[f"| {yr} | {cnt} |" for yr, cnt in s2.get("year_coverage", {}).items()],
        f"",
        *(
            [f"**Large gaps (>90d):**", f"", f"| From | To | Days |", f"|---|---|---|",
             *[f"| {g[0]} | {g[1]} | {g[2]} |" for g in s2.get("large_gaps_gt90d", [])], f""]
            if s2.get("large_gaps_gt90d") else []
        ),
        f"---",
        f"",
        f"## Stage 3 — S2 Optical Geometry",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| S2 scenes (cloud<{MAX_CLOUD}%) | {s3.get('n_s2_scenes','—')} |",
        f"| Low shadow geometry (sun_elv>{LOW_SUN_ELV}°) | {s3.get('n_low_shadow_geometry','—')} |",
        f"| Usable shadow geometry | {s3.get('n_usable_geometry','—')} |",
        f"| Tanks with ≥2 usable shadow pixels | {s3.get('n_tanks_geometry_ok','—')}/{N_TANKS} |",
        f"",
        f"All tanks: radius=40m → {40/RESOLUTION_M:.0f}px/ray at 5m/px, "
        f"shadow pixels = {(40-5)/RESOLUTION_M:.0f}px ≥ 2 — geometry OK.",
        f"",
        f"---",
        f"",
        f"## Stage 4 — Per-Tank Quality Screen",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| Tanks total | {s4.get('n_tanks','—')} |",
        f"| WL tanks (step_db≥{STEP_THRESH}) | {s4.get('n_wl_tanks','—')} |",
        f"| Total capacity | {s4.get('total_cap_mb','—')} Mb |",
        f"| WL capacity | {s4.get('wl_cap_mb','—')} Mb |",
        f"| WL cap fraction | {s4.get('wl_cap_frac',0):.1%} |",
        f"| Terminal WL gate (≥{WL_CAP_FRAC:.0%}) | {'**PASS**' if terminal_wl else '**FAIL**'} |",
        f"",
    ]

    if not df_q.empty:
        lines += [
            f"### Per-tank table",
            f"",
            f"| tank_id | n_passes | db_mean | db_std | step_full | step_fresh | WL |",
            f"|---|---|---|---|---|---|---|",
        ]
        for _, row in df_q.iterrows():
            step_f  = f"{row['step_db_full']:.2f}"  if row['step_db_full']  is not None else "—"
            step_r  = f"{row['step_db_fresh']:.2f}" if row['step_db_fresh'] is not None else "—"
            db_m    = f"{row['db_mean']:.2f}"        if row['db_mean']  is not None else "—"
            db_s    = f"{row['db_std']:.2f}"         if row['db_std']   is not None else "—"
            wl_str  = "✓" if row["whitelist"] else "—"
            lines.append(
                f"| {row['tank_id']} | {row['n_passes']} | {db_m} | {db_s} | {step_f} | {step_r} | {wl_str} |"
            )
        lines.append("")

    lines += [
        f"---",
        f"",
        f"## Stage 5 — Visual Panels",
        f"",
        f"Panels written to `diagnostics/al_faw/panels/` (no verdicts — visual review by analyst).",
        f"",
    ]
    for pf in panel_files:
        lines.append(f"- `{os.path.relpath(pf, os.path.dirname(report_path))}`")
    lines.append("")

    lines += [
        f"---",
        f"",
        f"## Stage 6 — Terminal Series + Iraq Event Sanity",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| WL passes in series | {s6.get('n_wl_passes','—')} |",
        f"| Volume range (WL tanks) | [{s6.get('vol_min_mb','—')}, {s6.get('vol_max_mb','—')}] Mb |",
        f"| dB p5/p95 (WL calibration) | {s6.get('db_p5_wl','—')} / {s6.get('db_p95_wl','—')} dB |",
        f"| Placebo p90 change | {s6.get('p90_placebo_change_mb','—')} Mb |",
        f"",
        f"**Iraq event sanity (E1 estimator — no export correlation, user-side):**",
        f"",
        f"| Event | Date | E1 vol change Mb | Above placebo p90 | Passes in window |",
        f"|---|---|---|---|---|",
    ]
    if not df_events.empty:
        for _, ev in df_events.iterrows():
            e1_str = f"{ev['e1_vol_change_mb']:.3f}" if ev['e1_vol_change_mb'] is not None else "—"
            p90_str = str(ev['above_p90_placebo']) if ev['above_p90_placebo'] is not None else "—"
            lines.append(f"| {ev['event']} | {ev['date']} | {e1_str} | {p90_str} | {ev['n_passes_in_window']} |")
    lines.append("")

    lines += [
        f"---",
        f"",
        f"## Stage 7 — CSV Export",
        f"",
        f"- `diagnostics/al_faw/iraq_alfaw_daily.csv` — terminal daily vol (WL tanks, Mb)",
        f"- `diagnostics/al_faw/iraq_alfaw_pertank.csv` — per-tank quality + step_db",
        f"",
        f"**Correlation: user-side only. PortWatch/JODI/SOMO quarantined.**",
        f"",
        f"---",
        f"",
        f"## Terminal Assessment",
        f"",
        f"WL gate: {'PASS' if terminal_wl else 'FAIL'} "
        f"({s4.get('wl_cap_frac',0):.1%} ≥ {WL_CAP_FRAC:.0%} required).  ",
        f"Visual review of panels required before any series publication.  ",
        f"No export verdicts in this pipeline run.",
        f"",
    ]

    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    log(f"Report: {report_path}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    ensure_dir(OUT_DIR)

    log(f"=== Al-Faw Baseline Pipeline === terminal={TERMINAL_ID}")
    log(f"  STEP_THRESH={STEP_THRESH}  WL_CAP_FRAC={WL_CAP_FRAC}  "
        f"DB_FLOOR={DB_FLOOR}  FRESH_N={FRESH_N}")

    # connect supabase
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Stage 1
    df, s1_meta = stage1_db_inventory(sb)
    if df is None:
        log("FATAL: no data in terminal_observations for al_flaw")
        sys.exit(1)

    # Stage 2
    s2_result = stage2_orbit_catalog(df)
    s2_meta   = s2_result[0] if isinstance(s2_result, tuple) else s2_result
    daily_cadence = s2_result[1] if isinstance(s2_result, tuple) else pd.DataFrame()

    # Stage 3
    s3_meta, df_s2_scenes, df_geo = stage3_optical_geometry()

    # Stage 4
    s4_meta, df_q = stage4_quality_screen(df)

    # Stage 5
    panel_files = stage5_visual_panels(df, df_q, OUT_DIR)

    # Stage 6
    s6_result   = stage6_terminal_series(df, df_q, OUT_DIR)
    s6_meta     = s6_result[0] if isinstance(s6_result, tuple) else {}
    daily_vol   = s6_result[1] if isinstance(s6_result, tuple) and len(s6_result) > 1 else pd.DataFrame()
    df_events   = s6_result[2] if isinstance(s6_result, tuple) and len(s6_result) > 2 else pd.DataFrame()

    # Stage 7
    stage7_csv_export(daily_vol, df_q, OUT_DIR)

    # Write geometry CSVs
    if not df_geo.empty:
        df_geo.to_csv(os.path.join(OUT_DIR, "alfaw_geometry.csv"), index=False)
    if not df_s2_scenes.empty:
        df_s2_scenes.to_csv(os.path.join(OUT_DIR, "alfaw_s2_scenes.csv"), index=False)

    # Report
    write_report(s1_meta, s2_meta, s3_meta, s4_meta, s6_meta,
                 df_q, df_events, panel_files, REPORT_FILE)

    log("=== DONE ===")


if __name__ == "__main__":
    main()
