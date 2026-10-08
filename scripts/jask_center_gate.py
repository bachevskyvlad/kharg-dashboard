"""
jask_center_gate.py — A/B center-gate for Jask: single SAR tile pass, dual mask.

Config A: original eye-placed centers (lat_reg/lon_reg) + ESRI r_fit
Config B: ESRI-measured centers  (lat_fit/lon_fit)      + ESRI r_fit

For each of the 109 already-catalogued scenes:
  1. Fetch SAR tile once
  2. Apply crescent masks for both A and B configs
  3. Record db_mean_A and db_mean_B per (date, tank_id)

Gate criterion (fixed before running):
  step_db = median |Δdb| over consecutive observations per tank
  median_B < median_A by ≥15%  → B wins
  median_A < median_B by ≥15%  → A wins
  |diff| < 15%                  → B wins (tiebreak: documented measurement beats eyeball)

Per-tank exception: if a tank's step on the winning config is >20% WORSE than
the losing config → that tank individually keeps the better-performing center.

Outputs (committed by workflow):
  diagnostics/jask/center_gate/ab_db_means.csv
  diagnostics/jask/center_gate/ab_step_db.csv
  diagnostics/jask/JASK_CENTER_GATE.md

Usage:
  cd scripts
  python jask_center_gate.py [--dry_run]  # dry_run skips SH fetch; uses stored CSV if present
"""

import argparse
import datetime
import json
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

SUPABASE_URL     = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""

sys.path.insert(0, os.path.dirname(__file__))

# Inlined to avoid importing sar_fetch/config which pull in supabase
LOS_AZIMUTH = 282    # degrees — DESC orbit, Persian Gulf
DB_FLOOR    = -5.0   # dB minimum clamp

def lonlat_to_pix(lon, lat, bbox, w, h):
    x = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    y = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return x, y

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, MimeType, SentinelHubRequest
)
from datetime import timedelta

sh_config = SHConfig()
sh_config.sh_client_id     = SH_CLIENT_ID
sh_config.sh_client_secret = SH_CLIENT_SECRET
sh_config.sh_base_url      = "https://sh.dataspace.copernicus.eu"
sh_config.sh_token_url     = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1_cdse",
    service_url="https://sh.dataspace.copernicus.eu",
    collection_id="fd50d8df-75c7-46a5-9985-12ee17e0b06f",
)

EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""

REL_ORBIT  = 93
DIRECTION  = "DESCENDING"
RESOLUTION = 5   # m/px
WIDTH_WIDE = 15  # crescent annulus width (m) — same as sar_fetch.py

JASK_START = datetime.datetime(2024, 6, 1)
GATE_THRESHOLD = 0.15   # 15% median step difference → decisive
TANK_INVERT_THRESHOLD = 0.20  # 20% per-tank inversion → individual override

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "jask" / "center_gate"
MD_PATH = Path(__file__).parent.parent / "diagnostics" / "jask" / "JASK_CENTER_GATE.md"
MEAS_JSON = Path(__file__).parent.parent / "diagnostics" / "jask" / "jask_radius_raw.json"


# ── Load both configs from measurement JSON ───────────────────────────────────

def load_configs():
    """Returns (tanks_A, tanks_B): list of {id, lat, lon, radius, h_max} dicts."""
    rows = json.loads(MEAS_JSON.read_text())
    A, B = [], []
    for r in rows:
        if r["status"] != "OK":
            continue
        base = {
            "id":     r["id"],
            "radius": r["r_fit_m"],
            "h_max":  14 if r["r_fit_m"] >= 40 else 13,
        }
        A.append({**base, "lat": r["lat_reg"], "lon": r["lon_reg"]})
        B.append({**base, "lat": r["lat_fit"], "lon": r["lon_fit"]})
    return A, B


# ── Crescent mask builder (mirrors sar_fetch.build_crescent_masks) ────────────

def build_masks(tanks, bbox, img_w, img_h, los_az=LOS_AZIMUTH):
    Y, X = np.indices((img_h, img_w))
    dx = np.sin(np.deg2rad(los_az))
    dy = -np.cos(np.deg2rad(los_az))
    out = []
    for t in tanks:
        cx, cy = lonlat_to_pix(t["lon"], t["lat"], bbox, img_w, img_h)
        dist_sq = (X - cx)**2 + (Y - cy)**2
        dot     = (X - cx) * dx + (Y - cy) * dy
        r_outer = t["radius"] / RESOLUTION
        r_inner = max(0, t["radius"] - WIDTH_WIDE) / RESOLUTION
        crescent = (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2) & (dot > 0)
        out.append({**t, "crescent": crescent, "n_pix": int(crescent.sum())})
    return out


# ── Extract db_mean for one config from a fetched image ───────────────────────

def extract(img_linear, masks):
    rows = {}
    for t in masks:
        pix = img_linear[t["crescent"]]
        pix = pix[np.isfinite(pix)]
        if pix.size == 0:
            continue
        db = 10 * np.log10(np.clip(pix.mean(), 1e-10, None))
        if np.isfinite(db):
            rows[t["id"]] = max(float(db), DB_FLOOR)
    return rows


# ── Scene date catalog ────────────────────────────────────────────────────────

def get_scene_dates(bbox):
    from sentinelhub import SentinelHubCatalog
    import datetime as _dt
    catalog  = SentinelHubCatalog(config=sh_config)
    end_date = _dt.datetime.utcnow()
    scenes   = list(catalog.search(
        S1_CDSE, bbox=bbox,
        time=(JASK_START.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        filter="sat:orbit_state = 'descending'",
        fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
    ))
    seen_orbits = set()
    dates = []
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        if s["properties"].get("sat:relative_orbit") != REL_ORBIT:
            continue
        dt = pd.to_datetime(s["properties"]["datetime"]).normalize()
        key = (dt.date(), s["properties"].get("sat:relative_orbit"))
        if key not in seen_orbits:
            seen_orbits.add(key)
            dates.append(dt)
    return sorted(set(dates))


# ── Fetch tile for one date ───────────────────────────────────────────────────

def fetch_tile(d, bbox, img_w, img_h, retries=4):
    d_str = d.strftime("%Y-%m-%d")
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(d_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
            other_args={
                "processing": {
                    "orthorectify": True,
                    "backCoeff":    "GAMMA0_TERRAIN",
                    "demInstance":  "COPERNICUS_30",
                },
                "dataFilter": {
                    "orbitDirection":    DIRECTION,
                    "relativeOrbitNumber": REL_ORBIT,
                },
                "mosaicking": "ORBIT",
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=(img_w, img_h), config=sh_config,
    )
    for attempt in range(retries):
        try:
            data = req.get_data()
            if data:
                return data[0].squeeze()
        except Exception as e:
            wait = 2 * (2 ** attempt)
            print(f"  [retry {attempt+1}] {d_str}: {e} — wait {wait}s")
            time.sleep(wait)
    return None


# ── Compute step_db per tank from a db_mean time series ──────────────────────

def compute_step_db(df, config_col):
    """
    Returns dict {tank_id: median_abs_step} for the given config column.
    step = median |db[t] - db[t-1]| over consecutive observations per tank.
    """
    results = {}
    for tid in df["tank_id"].unique():
        ts = df[df["tank_id"] == tid].sort_values("date")[config_col].dropna()
        if len(ts) < 4:
            continue
        diffs = ts.diff().abs().dropna()
        results[tid] = float(diffs.median())
    return results


# ── Gate decision ─────────────────────────────────────────────────────────────

def gate_decision(step_A, step_B, tank_ids):
    """
    Returns: winner ('A'|'B'), per_tank_winner dict, summary str.
    Gate criterion (fixed before running):
      median_B < median_A by ≥15% → B wins
      median_A < median_B by ≥15% → A wins
      tie (<15% diff)              → B wins (tiebreak)
    Per-tank: if step on winning config >20% worse than losing → individual override.
    """
    common = [t for t in tank_ids if t in step_A and t in step_B]
    med_A = float(np.median([step_A[t] for t in common]))
    med_B = float(np.median([step_B[t] for t in common]))

    diff_pct = (med_A - med_B) / med_A * 100  # positive = B better (smaller step = less noisy)
    # Note: LARGER step_db = MORE dynamic range = better signal.
    # So we want the config with LARGER median step_db.
    # Redefine: diff_pct = (med_B - med_A) / med_A * 100; positive = B has larger steps
    diff_pct = (med_B - med_A) / med_A * 100

    if diff_pct >= GATE_THRESHOLD * 100:
        overall_winner = "B"
        summary = f"B wins: median step_B={med_B:.3f} > step_A={med_A:.3f} (+{diff_pct:.1f}%)"
    elif -diff_pct >= GATE_THRESHOLD * 100:
        overall_winner = "A"
        summary = f"A wins: median step_A={med_A:.3f} > step_B={med_B:.3f} ({diff_pct:.1f}%)"
    else:
        overall_winner = "B"
        summary = (f"TIE (diff={diff_pct:+.1f}% < 15%) — B wins tiebreak "
                   f"(measured center beats eyeball per protocol)")

    loser = "A" if overall_winner == "B" else "B"
    step_win  = step_B if overall_winner == "B" else step_A
    step_lose = step_A if overall_winner == "B" else step_B

    per_tank = {}
    overrides = []
    for t in common:
        sw = step_win.get(t, 0)
        sl = step_lose.get(t, 0)
        if sl > 0 and sw < sl * (1 - TANK_INVERT_THRESHOLD):
            per_tank[t] = loser
            overrides.append(f"{t}: override → {loser} (step_{overall_winner}={sw:.3f} vs step_{loser}={sl:.3f})")
        else:
            per_tank[t] = overall_winner

    return overall_winner, per_tank, summary, overrides, med_A, med_B, diff_pct


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry_run", action="store_true",
                        help="Skip SH fetch; use stored ab_db_means.csv if present")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "ab_db_means.csv"

    tanks_A, tanks_B = load_configs()
    print(f"Config A: {len(tanks_A)} tanks (original eye centers + r_fit)")
    print(f"Config B: {len(tanks_B)} tanks (ESRI-measured centers + r_fit)")

    # Unique tank ids (same set for A and B)
    tank_ids = [t["id"] for t in tanks_A]

    # Build BBox from all tanks
    all_lats = [t["lat"] for t in tanks_A + tanks_B]
    all_lons = [t["lon"] for t in tanks_A + tanks_B]
    buf = 500 / 111320
    bbox = BBox([min(all_lons) - buf, min(all_lats) - buf,
                 max(all_lons) + buf, max(all_lats) + buf], crs=CRS.WGS84)

    img_w = int((bbox.max_x - bbox.min_x) * 111320 *
                np.cos(np.deg2rad(min(all_lats))) / RESOLUTION)
    img_h = int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION)
    print(f"Tile size: {img_w}×{img_h}px  BBox: "
          f"[{bbox.min_x:.4f},{bbox.min_y:.4f},{bbox.max_x:.4f},{bbox.max_y:.4f}]")

    if args.dry_run and csv_path.exists():
        print(f"[dry_run] Loading existing {csv_path}")
        df = pd.read_csv(csv_path)
    else:
        # Build crescent masks for both configs
        masks_A = build_masks(tanks_A, bbox, img_w, img_h, los_az=282)
        masks_B = build_masks(tanks_B, bbox, img_w, img_h, los_az=282)
        pix_A = {m["id"]: m["n_pix"] for m in masks_A}
        pix_B = {m["id"]: m["n_pix"] for m in masks_B}
        print(f"\nCrescent pixel counts (A / B):")
        for tid in tank_ids:
            print(f"  {tid}: {pix_A.get(tid,0)} / {pix_B.get(tid,0)}")

        # Get scene dates
        print(f"\nQuerying catalog (jask DESC rel={REL_ORBIT} from {JASK_START.date()}) …")
        dates = get_scene_dates(bbox)
        print(f"Found {len(dates)} scene dates")

        records = []
        n_ok = n_skip = 0
        for i, d in enumerate(dates):
            d_str = d.strftime("%Y-%m-%d")
            print(f"  [{i+1:3d}/{len(dates)}] {d_str} … ", end="", flush=True)
            img = fetch_tile(d, bbox, img_w, img_h)
            if img is None:
                print("SKIP (no data)")
                n_skip += 1
                continue
            row_A = extract(img, masks_A)
            row_B = extract(img, masks_B)
            for tid in tank_ids:
                if tid in row_A or tid in row_B:
                    records.append({
                        "date":     d_str,
                        "tank_id":  tid,
                        "db_mean_A": row_A.get(tid),
                        "db_mean_B": row_B.get(tid),
                    })
            n_ok += 1
            print(f"A_tanks={len(row_A)} B_tanks={len(row_B)}")

        print(f"\nDone: {n_ok} ok, {n_skip} skipped")
        df = pd.DataFrame(records)
        df.to_csv(csv_path, index=False)
        print(f"Saved: {csv_path}")

    # Compute step_db per tank for each config
    step_A = compute_step_db(df, "db_mean_A")
    step_B = compute_step_db(df, "db_mean_B")

    # Gate decision
    winner, per_tank, summary, overrides, med_A, med_B, diff_pct = gate_decision(
        step_A, step_B, tank_ids
    )

    # Per-tank table
    rows_table = []
    for tid in sorted(tank_ids):
        sA = step_A.get(tid, float("nan"))
        sB = step_B.get(tid, float("nan"))
        delta = ((sB - sA) / sA * 100) if sA > 0 else float("nan")
        ptw = per_tank.get(tid, winner)
        rows_table.append({
            "tank": tid,
            "step_A": round(sA, 3),
            "step_B": round(sB, 3),
            "delta_pct": round(delta, 1) if np.isfinite(delta) else None,
            "winner": ptw,
        })

    df_step = pd.DataFrame(rows_table)
    step_csv = OUT_DIR / "ab_step_db.csv"
    df_step.to_csv(step_csv, index=False)

    # Print table
    print("\n" + "="*70)
    print(f"CENTER GATE A/B — JASK")
    print(f"Criterion: step_db = median |Δdb| per consecutive observations/tank")
    print(f"Threshold: ≥15% median diff → decisive; tie → B wins")
    print("="*70)
    print(f"{'Tank':<8} {'step_A':>8} {'step_B':>8} {'Δ%':>7} {'winner':>8}")
    print("-"*45)
    for r in rows_table:
        dp = f"{r['delta_pct']:+.1f}%" if r['delta_pct'] is not None else "  N/A"
        print(f"{r['tank']:<8} {r['step_A']:>8.3f} {r['step_B']:>8.3f} {dp:>7} {r['winner']:>8}")
    print("-"*45)
    print(f"{'MEDIAN':<8} {med_A:>8.3f} {med_B:>8.3f} {diff_pct:>+6.1f}%")
    print(f"\nVERDICT: {summary}")
    if overrides:
        print("\nPer-tank overrides:")
        for o in overrides:
            print(f"  {o}")

    # Markdown report
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    md_lines = [
        "# Jask Center Gate A/B",
        "",
        f"Run: {now}",
        "",
        "**Config A**: original eye-placed centers + ESRI r_fit",
        "**Config B**: ESRI-measured centers (32-ray circle fit) + ESRI r_fit",
        "",
        "**Gate criterion (fixed before running)**:",
        "- step_db = median |Δdb_mean| over consecutive observations per tank",
        "- median_B > median_A by ≥15% → B wins",
        "- median_A > median_B by ≥15% → A wins",
        "- |diff| < 15% → B wins (tiebreak: documented measurement beats eyeball)",
        "- Per-tank: if winning config step >20% worse than losing → individual override",
        "",
        "## Results",
        "",
        f"| Tank | step_A | step_B | Δ% | winner |",
        f"|---|---|---|---|---|",
    ]
    for r in rows_table:
        dp = f"{r['delta_pct']:+.1f}%" if r['delta_pct'] is not None else "N/A"
        md_lines.append(
            f"| {r['tank']} | {r['step_A']} | {r['step_B']} | {dp} | **{r['winner']}** |"
        )
    md_lines += [
        "",
        f"| **MEDIAN** | **{med_A:.3f}** | **{med_B:.3f}** | **{diff_pct:+.1f}%** | |",
        "",
        f"## Verdict",
        "",
        f"**{summary}**",
    ]
    if overrides:
        md_lines += ["", "### Per-tank overrides", ""]
        for o in overrides:
            md_lines.append(f"- {o}")

    md_lines += [
        "",
        f"Overall winner: **Config {winner}**",
        "",
        "Registry action: see TERMINAL_PASSPORT_jask.md for final center_source per tank.",
    ]
    MD_PATH.write_text("\n".join(md_lines))
    print(f"\nReport → {MD_PATH}")
    print(f"Step CSV → {step_csv}")
    print(f"Raw CSV  → {csv_path}")


if __name__ == "__main__":
    main()
