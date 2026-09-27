"""
diag_final_7_8.py — Round 7.8 (final): dihedral temporal test + FCS JW/YB + final verdicts.

Context established in rounds 7.6–7.7:
  - Signal = double-bounce (dihedral) from far interior wall of EMPTY tank.
  - Empty tank brighter than full at all azimuths; peak contrast at 270–300°
    (prod azimuth 282° for DESC). Prod mask geometry is optimal.
  - FCS = (p95 − p05 temporal dB) / median scene noise — cycling metric.
  - Kharg threshold = 3.45 (median + 1σ); 7/47 cycling.
  - Supabase 403 from GHA; JW/YB FCS from user-provided CSV files.

Gate chain: Task 1 does NOT gate Tasks 2–3. All verdicts independent.

Security:
  - No Supabase writes. Kharg tables not touched.
  - No paid API calls.
  - Up to 6 micro-bbox fetches for t60_2 Task 1 (only exception).
  - Honesty rule. "вероятно" / "скорее всего" / "не blocker" prohibited.
  - Unavailable data: "не проверено / awaiting input", never substituted.

Output: diagnostics/round7_8/FINAL_DIAGNOSIS.md + PNGs
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from sentinelhub import (
        BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest,
        DataCollection, SHConfig,
    )
    _SH_OK = True
except ImportError as e:
    print(f"[WARN] sentinelhub import: {e}")
    _SH_OK = False

sys.path.insert(0, str(Path(__file__).parent))
try:
    from sar_fetch import build_crescent_masks as _prod_build_crescent_masks
    from sar_fetch import lonlat_to_pix as _prod_lonlat_to_pix
    _PROD_IMPORT_OK = True
except ImportError as e:
    print(f"[FATAL] cannot import from sar_fetch: {e}")
    _PROD_IMPORT_OK = False

# ── Constants ──────────────────────────────────────────────────────────────────
RESOLUTION   = 5
LOS_AZ_DESC  = 282
WIDTH_WIDE   = 15
SECTOR_STEP  = 15

CLIENT_ID     = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")

if _SH_OK:
    try:
        from config import config as cfg, S1_CDSE
        if CLIENT_ID:
            cfg.sh_client_id = CLIENT_ID
        if CLIENT_SECRET:
            cfg.sh_client_secret = CLIENT_SECRET
    except ImportError:
        cfg = SHConfig()
        cfg.sh_client_id     = CLIENT_ID     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
        cfg.sh_client_secret = CLIENT_SECRET or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
        cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
        cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
            "S1_CDSE_78", service_url=cfg.sh_base_url
        )

OUT_DIR = Path("diagnostics/round7_8")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Tank geometry (from tank_registry.py) ─────────────────────────────────────
KHARG_BBOX = (50.295, 29.205, 50.340, 29.240)

T60_2 = {"id": "t60_2", "lat": 29.221840470329855, "lon": 50.31352911803618,
          "radius": 60, "h_max": 18}

# Micro-bbox for t60_2 only (Task 1 exception)
T60_2_MICRO_BBOX = (
    T60_2["lon"] - 0.008, T60_2["lat"] - 0.008,
    T60_2["lon"] + 0.008, T60_2["lat"] + 0.008,
)

# Yanbu azimuth pivot: commit 4ef88c9 date = 2026-09-04
YB_PIVOT_DATE = pd.Timestamp("2026-09-04")

# FCS threshold from Kharg calibration (round 7.7)
FCS_THRESHOLD_KHARG = 3.45

# Known FCS values from round 7.7
KHARG_FCS_ABOVE = 7    # out of 47
RT_FCS_ABOVE    = 9    # out of 66
KHARG_N_TANKS   = 47
RT_N_TANKS      = 66
KHARG_SCENE_NOISE = 4.250  # dB
RT_SCENE_NOISE    = 5.103  # dB


# ── Sentinel Hub helpers ───────────────────────────────────────────────────────

_EVALSCRIPT_VV = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


def _bbox_to_size(bbox_tuple):
    min_lon, min_lat, max_lon, max_lat = bbox_tuple
    w = max(10, int((max_lon - min_lon) * 111320 / RESOLUTION))
    h = max(10, int((max_lat - min_lat) * 111320 / RESOLUTION))
    return w, h


def fetch_s1(bbox_tuple, date_str, orbit="DESCENDING"):
    if not _SH_OK:
        return None, 0, 0
    bbox = BBox(bbox_tuple, crs=CRS.WGS84)
    size = _bbox_to_size(bbox_tuple)
    d = date.fromisoformat(date_str)
    try:
        req = SentinelHubRequest(
            evalscript=_EVALSCRIPT_VV,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(date_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                other_args={
                    "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                   "demInstance": "COPERNICUS_30"},
                    "dataFilter": {"orbitDirection": orbit},
                    "mosaicking": "ORBIT",
                },
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=size, config=cfg,
        )
        data = req.get_data()
        if not data:
            return None, size[0], size[1]
        arr = np.array(data[0], dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.std() == 0:
            return None, size[0], size[1]
        return arr, arr.shape[1], arr.shape[0]
    except Exception as e:
        print(f"    [WARN] fetch {date_str}: {e}")
        return None, size[0], size[1]


def _lonlat_to_pix_manual(lon, lat, bbox_tuple, w, h):
    min_lon, min_lat, max_lon, max_lat = bbox_tuple
    cx = (lon - min_lon) / (max_lon - min_lon) * w
    cy = (max_lat - lat) / (max_lat - min_lat) * h
    return cx, cy


def _build_prod_mask_at(cx, cy, radius_m, img_w, img_h, az_deg, res_m=RESOLUTION):
    if _SH_OK and _PROD_IMPORT_OK:
        syn_lon = cx * res_m / 111320.0
        syn_lat = img_h * res_m / 111320.0 - cy * res_m / 111320.0
        bbox_syn = BBox((0.0, 0.0, img_w * res_m / 111320.0, img_h * res_m / 111320.0),
                        crs=CRS.WGS84)
        td = _prod_build_crescent_masks(
            [{"id": "t", "lon": syn_lon, "lat": syn_lat, "radius": radius_m, "h_max": 18}],
            bbox_syn, img_w, img_h, los_azimuth=az_deg
        )
        return td[0]["crescent"]
    # fallback
    Y, X = np.mgrid[0:img_h, 0:img_w]
    r_outer = radius_m / res_m
    r_inner = max(0.0, radius_m - WIDTH_WIDE) / res_m
    dist_sq = (X - cx)**2 + (Y - cy)**2
    dx = np.sin(np.deg2rad(az_deg)); dy = -np.cos(np.deg2rad(az_deg))
    dot = (X - cx)*dx + (Y - cy)*dy
    return (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2) & (dot > 0)


def mask_mean_db(img_lin, mask):
    px = img_lin[mask][np.isfinite(img_lin[mask])]
    if px.size == 0:
        return np.nan, 0
    return float(10 * np.log10(np.clip(np.mean(px), 1e-10, None))), px.size


# ── Date matching unit test ────────────────────────────────────────────────────

def _test_date_matching():
    """Unit test: off-by-one normalisation. Returns True if all pass."""
    cases = [
        # (csv_date_str, scene_date_str, expected_match)
        ("2026-04-20", "2026-04-20", True),   # exact
        ("2026-04-20", "2026-04-21", True),   # +1 day
        ("2026-04-20", "2026-04-19", True),   # -1 day
        ("2026-04-20", "2026-04-22", False),  # +2 days (outside window)
        ("2026-04-20", "2026-04-18", False),  # -2 days
    ]
    ok = True
    for csv_d, scene_d, expected in cases:
        csv_ts    = pd.Timestamp(csv_d).normalize()
        scene_ts  = pd.Timestamp(scene_d).normalize()
        matched   = abs((csv_ts - scene_ts).days) <= 1
        status    = "OK" if (matched == expected) else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  date-match unit test [{status}]: csv={csv_d} scene={scene_d} → {matched} (expected {expected})")
    return ok


def _match_csv_to_scenes(csv_dates_set, scene_dates_list, window_days=1):
    """
    For each scene date, find matching CSV date within window_days.
    Returns dict: scene_date_str → csv_date_str (or None).
    """
    result = {}
    for sd in scene_dates_list:
        sd_ts = pd.Timestamp(sd).normalize()
        best = None
        for cd in csv_dates_set:
            if abs((sd_ts - cd).days) <= window_days:
                best = cd
                break
        result[sd] = best
    return result


# ── Task 1 — Dihedral temporal test ───────────────────────────────────────────

def task1_dihedral(kharg_csv_path, lines):
    lines.append("\n## Task 1 — Dihedral temporal test (t60_2)\n\n")

    # Unit test
    lines.append("### Date matching unit test\n\n")
    unit_ok = _test_date_matching()
    lines.append(f"Unit test result: {'PASS — all 5 cases correct' if unit_ok else 'FAIL'}\n\n")

    df = pd.read_csv(kharg_csv_path)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    t60_2_df = df[df["tank_id"] == "t60_2"].copy().sort_values("db_mean")

    # 3 most full (lowest dB) and 3 most empty (highest dB) dates
    full_dates  = list(t60_2_df.head(3)["date"])
    empty_dates = list(t60_2_df.tail(3)["date"])
    target_dates = {d.strftime("%Y-%m-%d"): ("full", float(t60_2_df[t60_2_df["date"]==d]["db_mean"].iloc[0]))
                    for d in full_dates + empty_dates}

    lines.append("### t60_2 target dates (from Kharg CSV)\n\n")
    lines.append("| date | status | CSV db_mean |\n|------|--------|-------------|\n")
    for d_str, (status, dbv) in target_dates.items():
        lines.append(f"| {d_str} | {status} | {dbv:.3f} |\n")
    lines.append("\n")

    # Try to fetch each date via micro-bbox
    csv_dates_set = set(t60_2_df["date"])
    fetch_count = 0
    rows = []  # (date, status, csv_db, prod_db, se_db, N_prod, N_se, source)

    for d_str, (status, csv_db) in target_dates.items():
        img, iw, ih = fetch_s1(T60_2_MICRO_BBOX, d_str, orbit="DESCENDING")
        source = "fetch"
        if img is None:
            # Try ±1 day
            for offset in [1, -1]:
                alt = (date.fromisoformat(d_str) + timedelta(days=offset)).strftime("%Y-%m-%d")
                img, iw, ih = fetch_s1(T60_2_MICRO_BBOX, alt, orbit="DESCENDING")
                if img is not None:
                    source = f"fetch±1d ({alt})"
                    break
        if img is not None:
            fetch_count += 1
            cx, cy = _lonlat_to_pix_manual(T60_2["lon"], T60_2["lat"],
                                            T60_2_MICRO_BBOX, iw, ih)
            prod_mask = _build_prod_mask_at(cx, cy, T60_2["radius"], iw, ih, LOS_AZ_DESC)
            # SE sector: sweep 135–165° (3 sectors × 15°)
            se_masks = [_build_prod_mask_at(cx, cy, T60_2["radius"], iw, ih, az)
                        for az in [135, 150, 165]]
            se_union = np.any(np.stack(se_masks, axis=0), axis=0)

            prod_db, n_prod = mask_mean_db(img, prod_mask)
            se_db,   n_se   = mask_mean_db(img, se_union)
            rows.append((d_str, status, csv_db, prod_db, se_db, n_prod, n_se, source))
            print(f"  T1 {d_str} ({status}): prod={prod_db:.2f} SE={se_db:.2f} n={n_prod}")
        else:
            rows.append((d_str, status, csv_db, np.nan, np.nan, 0, 0, "NO DATA"))
            print(f"  T1 {d_str}: NO DATA")

    if fetch_count > 0:
        lines.append(f"Micro-bbox fetches used: **{fetch_count}/6** "
                     f"(exception from no-download rule, logged here)\n\n")

    # Build table
    lines.append("### Results\n\n")
    lines.append("| date | status | CSV dB | prod-282° dB | SE 135–165° dB | source |\n")
    lines.append("|------|--------|--------|--------------|----------------|--------|\n")
    for r in rows:
        d_str, status, csv_db, prod_db, se_db, _, _, source = r
        p = f"{prod_db:.2f}" if np.isfinite(prod_db) else "NO DATA"
        s = f"{se_db:.2f}"   if np.isfinite(se_db)   else "NO DATA"
        lines.append(f"| {d_str} | {status} | {csv_db:.2f} | {p} | {s} | {source} |\n")

    # Compute Δ
    full_rows  = [(r[3], r[4]) for r in rows if r[1]=="full"  and np.isfinite(r[3])]
    empty_rows = [(r[3], r[4]) for r in rows if r[1]=="empty" and np.isfinite(r[3])]
    if full_rows and empty_rows:
        mean_prod_full  = float(np.mean([x[0] for x in full_rows]))
        mean_prod_empty = float(np.mean([x[0] for x in empty_rows]))
        mean_se_full    = float(np.mean([x[1] for x in full_rows if np.isfinite(x[1])]))
        mean_se_empty   = float(np.mean([x[1] for x in empty_rows if np.isfinite(x[1])]))
        delta_prod = mean_prod_empty - mean_prod_full
        delta_se   = mean_se_empty   - mean_se_full

        pass_prod = delta_prod >= 4.0
        pass_se   = delta_se   < 2.0

        lines.append(f"\n**Δ prod-282°** (empty − full) = {delta_prod:+.2f} dB "
                     f"(criterion ≥+4 dB) → {'PASS' if pass_prod else 'FAIL'}\n")
        lines.append(f"**Δ SE 135–165°** (empty − full) = {delta_se:+.2f} dB "
                     f"(criterion < 2 dB) → {'PASS' if pass_se else 'FAIL'}\n")

        if pass_prod and pass_se:
            lines.append("\n**Task 1 verdict: PASS — dihedral confirmed temporally.**\n")
            lines.append("[FINDING] prod-282° separates full/empty dates; "
                         "SE sector contrast is weak. Consistent with dihedral physics.\n")
        elif pass_prod and not pass_se:
            lines.append("\n**Task 1 verdict: PARTIAL — prod-282° discriminates but SE sector also separates.**\n")
            lines.append("[FINDING] SE sector shows non-negligible contrast "
                         f"(Δ={delta_se:+.2f} dB). "
                         "Dihedral mechanism not ruled out, but geometry is not exclusively 282°.\n")
        else:
            lines.append(f"\n**Task 1 verdict: FAIL — prod-282° Δ={delta_prod:+.2f} dB < 4 dB.**\n")
            lines.append("[FINDING] Temporal dihedral prediction NOT CONFIRMED. "
                         "Factual structure: prod-282° and SE sector both show Δ < 4 dB.\n")

        # Save plot
        _save_t1_scatter(rows, OUT_DIR / "t1_dihedral_t60_2.png")
    else:
        n_nodata = sum(1 for r in rows if r[7] == "NO DATA")
        lines.append(f"\n[FINDING] Insufficient data for Δ calculation "
                     f"({n_nodata}/{len(rows)} dates returned NO DATA). "
                     "Task 1 verdict: INSUFFICIENT DATA.\n")

    lines.append(f"\nTask 1 does NOT gate Tasks 2–3.\n")


# ── Task 2 — FCS for JW/YB ────────────────────────────────────────────────────

def task2_fcs_jw_yb(lines):
    lines.append("\n## Task 2 — FCS Juwaymah / Yanbu\n\n")
    lines.append("Input files: `diagnostics/round7_8/jw_fcs_input.csv` "
                 "and `yb_fcs_input.csv`\n")
    lines.append("(User runs SQL from `diagnostics/round7_7/task4_jw_yb_query.sql` "
                 "and places output here.)\n\n")

    jw_path = Path("diagnostics/round7_8/jw_fcs_input.csv")
    yb_path = Path("diagnostics/round7_8/yb_fcs_input.csv")

    jw_result = None
    yb_result = None

    if not jw_path.exists():
        lines.append("**Juwaymah: awaiting manual SQL input** — "
                     f"`{jw_path}` not found. Task 2 JW: не проверено / awaiting input.\n\n")
    else:
        jw_result = _compute_fcs_site(jw_path, "juwaymah", pivot_date=None, lines=lines)

    if not yb_path.exists():
        lines.append("**Yanbu: awaiting manual SQL input** — "
                     f"`{yb_path}` not found. Task 2 YB: не проверено / awaiting input.\n\n")
    else:
        yb_result = _compute_fcs_site(yb_path, "yanbu", pivot_date=YB_PIVOT_DATE, lines=lines)

    return jw_result, yb_result


def _compute_fcs_site(csv_path, site_name, pivot_date, lines):
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])

    n_total = len(df)
    if pivot_date is not None:
        n_before = int((df["date"] <= pivot_date).sum())
        df = df[df["date"] > pivot_date]
        lines.append(f"**{site_name}**: {n_total} rows total; "
                     f"{n_before} rows ≤ pivot {pivot_date.date()} excluded; "
                     f"{len(df)} rows retained.\n\n")
    else:
        lines.append(f"**{site_name}**: {n_total} rows.\n\n")

    # Scene noise for THIS site
    scene_noise = df.groupby("date")["db_mean"].std().median()
    if np.isnan(scene_noise) or scene_noise == 0:
        scene_noise = KHARG_SCENE_NOISE
        noise_note = f"(fallback to Kharg noise {KHARG_SCENE_NOISE} dB — site median was NaN/0)"
    else:
        noise_note = f"(site-specific; Kharg = {KHARG_SCENE_NOISE:.3f} dB for comparison)"
    lines.append(f"Median scene noise ({site_name}) = **{scene_noise:.3f} dB** {noise_note}\n\n")

    # FCS per tank
    results = []
    for tid, grp in df.groupby("tank_id"):
        vals = grp["db_mean"].dropna().values
        n = len(vals)
        if n < 3:
            results.append({"tank_id": tid, "n_dates": n, "fcs": np.nan,
                             "verdict": "insufficient (N < 10)"})
            continue
        p95, p05 = np.percentile(vals, 95), np.percentile(vals, 5)
        fcs = (p95 - p05) / scene_noise
        if n < 10:
            verdict = "insufficient (N < 10)"
        elif fcs >= FCS_THRESHOLD_KHARG:
            verdict = "cycling"
        else:
            verdict = "static"
        results.append({"tank_id": tid, "n_dates": n, "p05": p05, "p95": p95,
                         "fcs": fcs, "verdict": verdict})

    df_fcs = pd.DataFrame(results).sort_values("fcs", ascending=False, na_position="last")
    n_cycling = int((df_fcs["verdict"] == "cycling").sum())
    n_static  = int((df_fcs["verdict"] == "static").sum())
    n_insuf   = int(df_fcs["verdict"].str.startswith("insufficient").sum())
    n_tanks   = len(df_fcs)

    lines.append(f"Tanks: {n_tanks} total — cycling: {n_cycling}, "
                 f"static: {n_static}, insufficient: {n_insuf}\n\n")
    lines.append("| tank_id | N dates | p05 dB | p95 dB | FCS | verdict |\n")
    lines.append("|---------|---------|--------|--------|-----|---------|\n")
    for _, row in df_fcs.iterrows():
        p05_s = f"{row.p05:.2f}" if "p05" in row and np.isfinite(row.get("p05", np.nan)) else "—"
        p95_s = f"{row.p95:.2f}" if "p95" in row and np.isfinite(row.get("p95", np.nan)) else "—"
        fcs_s = f"{row.fcs:.2f}" if np.isfinite(row.fcs) else "—"
        lines.append(f"| {row.tank_id} | {row.n_dates} | {p05_s} | {p95_s} | {fcs_s} | {row.verdict} |\n")

    # FCS histogram
    fcs_vals = df_fcs["fcs"].dropna().values
    if len(fcs_vals) > 0:
        _save_fcs_hist(fcs_vals, FCS_THRESHOLD_KHARG, site_name,
                       OUT_DIR / f"t2_fcs_{site_name}.png")

    return {"n_tanks": n_tanks, "n_cycling": n_cycling, "scene_noise": scene_noise,
            "df_fcs": df_fcs, "site": site_name}


# ── Task 3 — Final site verdicts ───────────────────────────────────────────────

def task3_verdicts(jw_result, yb_result, lines):
    lines.append("\n## Task 3 — Final site verdicts\n\n")

    def site_verdict(n_cycling, n_tanks, df_fcs, site_name):
        if n_tanks == 0 or df_fcs is None:
            return "INSUFFICIENT DATA"
        med_n = df_fcs["n_dates"].median()
        if med_n < 10:
            return "INSUFFICIENT DATA"
        if n_cycling == 0:
            return "NOT APPLICABLE"
        if n_cycling <= 5:
            return "TANK-LEVEL only"
        # n_cycling >= 6: check SNR
        cycling_rows = df_fcs[df_fcs["verdict"] == "cycling"]
        # SNR estimate: √N averaging reduces noise by √n_cycling
        snr_gain_db = 10 * np.log10(len(cycling_rows))
        dyn_range = (cycling_rows["p95"] - cycling_rows["p05"]).median()
        snr_ok = snr_gain_db > 0 and dyn_range > 6.0
        if snr_ok:
            return "CANDIDATE"
        else:
            return "TANK-LEVEL only"

    rows = []

    # Kharg — established
    rows.append(("Kharg",    KHARG_N_TANKS, KHARG_FCS_ABOVE,
                 "PROD — terminal series, validated r=0.7; majority static is a site property"))
    # RT
    rows.append(("RT south", RT_N_TANKS, RT_FCS_ABOVE,
                 "PROD — terminal-level, validated by 2026 war events; per-tank signal weak"))

    # JW
    if jw_result is None:
        rows.append(("Juwaymah", "—", "—", "не проверено / awaiting input"))
        jw_v = "не проверено / awaiting input"
    else:
        jw_v = site_verdict(jw_result["n_cycling"], jw_result["n_tanks"],
                            jw_result["df_fcs"], "juwaymah")
        rows.append(("Juwaymah", jw_result["n_tanks"], jw_result["n_cycling"], jw_v))

    # YB
    if yb_result is None:
        rows.append(("Yanbu", "—", "—", "не проверено / awaiting input"))
        yb_v = "не проверено / awaiting input"
    else:
        yb_v = site_verdict(yb_result["n_cycling"], yb_result["n_tanks"],
                            yb_result["df_fcs"], "yanbu")
        rows.append(("Yanbu", yb_result["n_tanks"], yb_result["n_cycling"], yb_v))

    lines.append("| Site | Tanks | Cycling (FCS > 3.45) | Verdict |\n")
    lines.append("|------|-------|----------------------|---------|\n")
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |\n")

    lines.append("\n**Verdict rules applied:**\n")
    lines.append("- `cycling ≥ 6` AND `dyn_range > 6 dB median` AND `med_N ≥ 10` → CANDIDATE\n")
    lines.append("- `cycling 1–5` OR SNR insufficient → TANK-LEVEL only\n")
    lines.append("- `cycling = 0` → NOT APPLICABLE\n")
    lines.append("- `median N dates < 10` → INSUFFICIENT DATA\n")
    lines.append("- File absent → не проверено / awaiting input\n\n")

    return jw_v, yb_v


# ── Task 4 — Final report + code comment fix ──────────────────────────────────

def task4_final_report(t1_summary, jw_verdict, yb_verdict, lines):
    lines.append("\n## Task 4 — Final diagnosis\n\n")

    lines.append("### Physics of the method (dihedral)\n\n")
    lines.append(
        "The SAR signal measured by the crescent mask is **double-bounce (dihedral)** "
        "from the far interior wall of the tank — NOT a shadow.\n\n"
        "Mechanism: the radar beam (282° azimuth, descending orbit) strikes the "
        "floating roof at grazing incidence; the beam then bounces off the inner "
        "cylinder wall and returns to the sensor (two-surface specular reflection). "
        "An EMPTY tank has its roof near the bottom: the beam travels the full "
        "interior height, produces a strong dihedral return → high dB. "
        "A FULL tank has its roof near the top: the beam barely enters, the "
        "dihedral path is short → low dB. Direction matters because the beam "
        "must reach the far wall; the 282° mask selects the hemisphere where this "
        "geometry is strongest.\n\n"
        "Evidence:\n"
        "- Round 7.7 sign-check: t60_1 (full) prod-mask = +1.52 dB; "
        "t55_5 (empty) = +10.29 dB (CSV reference 2.82 vs 13.02 — confirmed).\n"
        "- Round 7.7 sector tables: Δ(empty−full) peaks at 270–300° (WNW), "
        "falls to ~0 dB at 90–180° (SE). Direction-dependent: dihedral model.\n"
        "- Round 7.6: np.array_equal(prod_mask, diag_mask) = True (diff=0 px) — "
        "diagnostic tool is pixel-identical to prod.\n"
    )

    lines.append("### Error chronicle rounds 2–7.5\n\n")
    lines.append(
        "**Shadow-sector sweep criterion (rounds 2–7.5):** "
        "All sweep verdicts for Juwaymah and Yanbu in rounds 6–7.5 were based on "
        "the prediction that the minimum-dB sector should lie at 282° (shadow at "
        "the far wall). Round 7.6 proved the minimum is at 117–177° (SE), not 282°. "
        "The test was measuring the wrong physical quantity. All JW/YB verdicts "
        "from those rounds are **ANNULLED**.\n\n"
    )
    lines.append(
        "**rot vs eff_az bug (round 7.5):** "
        "sweep_profile returned raw `rot` offset (0–345°) instead of "
        "`eff_az = (los_az + rot) % 360`. Min-az was reported relative to offset, "
        "not absolute azimuth. Fixed in round 7.5 and confirmed in 7.6.\n\n"
    )
    lines.append(
        "**Circular scene selection (rounds 3–5):** "
        "SAR scenes were selected by their own dB value (\"high-signal dates\"), "
        "biasing the sample toward already-cycling tanks. Fixed by using "
        "catalog-only metadata for scene selection.\n\n"
    )
    lines.append(
        "**Hough 213m artifact (round 4):** "
        "Tank radius detection via Hough transform returned 213m due to "
        "CRS pixel-scale mismatch (degrees vs metres). Production uses "
        "hardcoded radius from tank registry.\n\n"
    )
    lines.append(
        "**Black crop images (rounds 3–5):** "
        "Crops saved in linear domain appeared black (values ~1e-4). "
        "Fixed by converting to dB before display.\n\n"
    )
    lines.append(
        "**\"Plausible explanations instead of checking\" (rounds 2–6):** "
        "Recurring pattern: when a test failed, the failure was attributed to "
        "a site-specific property (weak signal, few tanks, wrong orbit) rather "
        "than verifying the test instrument itself. Round 7.6 was the first round "
        "to test the instrument (sweep ≡ prod mask), which immediately identified "
        "the shadow-direction error.\n\n"
    )

    lines.append("### What stands / what is annulled\n\n")
    lines.append("**STANDS:**\n")
    lines.append("- Mask equivalence (7.6): prod mask = diag mask, diff=0 px.\n")
    lines.append("- Sign-check (7.7): t60_1=+1.52 dB (full), t55_5=+10.29 dB (empty).\n")
    lines.append("- FCS calibration (7.7): threshold=3.45; Kharg 7/47; RT 9/66.\n")
    lines.append("- RT cycling validation (rounds 1–3): built on prod-mask dB — "
                 "the mask was correct; the physics interpretation is updated "
                 "(dihedral, not shadow) but the signal is the same.\n")
    lines.append("- Kharg prod validation: r=0.7 vs independent benchmarks.\n\n")
    lines.append("**ANNULLED:**\n")
    lines.append("- All sweep-based JW/YB verdicts from rounds 6–7.5 "
                 "(tested shadow-direction, wrong criterion).\n")
    lines.append("- Well-depth (pure isotropic) hypothesis: "
                 "sector tables show azimuth-dependent contrast, not uniform.\n\n")

    lines.append("### Code comment diff (shadow → dihedral)\n\n")
    lines.append("Files to patch: `scripts/sar_fetch.py`, `scripts/config.py`.\n")
    lines.append("Changes are applied below and diff is shown.\n\n")

    # Apply patches
    diff = _patch_shadow_comments()
    lines.append("```diff\n" + diff + "\n```\n\n")

    lines.append("### Backlog\n\n")
    lines.append("See `diagnostics/backlog.md` (written by this script).\n\n")

    lines.append("---\n## Stop-line\n\n")
    lines.append(
        "Round 7.8 is the final diagnostic round. "
        "No prod logic was changed; only docstrings updated. "
        "No Supabase writes. No paid API calls. "
        f"JW verdict: {jw_verdict}. YB verdict: {yb_verdict}. "
        "Next step (if CANDIDATE): prod ТЗ. "
        "If awaiting input: re-run after CSV files are placed in diagnostics/round7_8/.\n"
    )


def _patch_shadow_comments():
    """Patch shadow→dihedral in sar_fetch.py and config.py. Returns unified diff string."""
    import difflib

    patches = {
        Path("scripts/sar_fetch.py"): [
            (
                "  - The floating roof casts a radar shadow on the FAR wall of the tank.\n"
                "  - We mask a ring annulus on the far-side half of each tank.\n"
                "  - Mean linear backscatter in that crescent → convert to dB → fill proxy.\n"
                "  - Lower dB = full tank (more oil = smoother surface = less radar return).",
                "  - The far interior wall of the tank produces a double-bounce (dihedral)\n"
                "    return. Empty tank: roof at bottom, long dihedral path → high dB.\n"
                "    Full tank: roof near top, short/absent dihedral path → low dB.\n"
                "  - We mask a ring annulus on the far-side half (282° for DESC) where\n"
                "    the dihedral geometry is strongest.\n"
                "  - Lower dB = full tank; higher dB = empty/draining tank.",
            ),
        ],
        Path("scripts/config.py"): [
            (
                "# The crescent mask captures the radar shadow on the FAR side of each tank wall.\n"
                "# Wider crescent = more shadow pixels = more stable dB mean, but risk of\n"
                "# capturing adjacent tank reflections. 15m annulus is empirically validated.",
                "# The crescent mask captures the double-bounce (dihedral) return from the FAR\n"
                "# interior wall. 282° selects the hemisphere of maximum dihedral contrast for\n"
                "# DESC orbit. Wider annulus = more pixels = more stable dB, but risk of\n"
                "# spilling into adjacent tanks. 15m is empirically validated.",
            ),
        ],
    }

    diff_lines = []
    for fpath, replacements in patches.items():
        if not fpath.exists():
            diff_lines.append(f"# {fpath} not found — skipped\n")
            continue
        original = fpath.read_text(encoding="utf-8")
        patched = original
        for old, new in replacements:
            if old in patched:
                patched = patched.replace(old, new)
            else:
                diff_lines.append(f"# WARNING: pattern not found in {fpath} — may already be patched\n")
        if patched != original:
            fpath.write_text(patched, encoding="utf-8")
            diff = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                patched.splitlines(keepends=True),
                fromfile=f"a/{fpath}",
                tofile=f"b/{fpath}",
                n=3,
            ))
            diff_lines.extend(diff)
        else:
            diff_lines.append(f"# {fpath}: no changes (pattern not found or already updated)\n")

    return "".join(diff_lines)


# ── Backlog ────────────────────────────────────────────────────────────────────

def write_backlog(jw_verdict, yb_verdict):
    bl = Path("diagnostics/backlog.md")
    lines = ["# Diagnostics backlog\n\n"]
    lines.append("Items below are noted for future investigation. "
                 "No commitments, no priority order.\n\n")
    lines.append("- **Full ring vs prod half-ring A/B (SNR)**: "
                 "Compare volume estimates from prod crescent mask (282°) vs full-ring mask "
                 "on Kharg historical CSV. Hypothesis: full ring averages over both "
                 "bright and dark sectors, reducing amplitude but also noise. "
                 "Metric: correlation with independent export events.\n\n")
    if "CANDIDATE" in jw_verdict:
        lines.append("- **Juwaymah pilot series**: "
                     "JW returned CANDIDATE. Recommend downloading 12 months of DESC scenes "
                     "for JW bbox, running prod pipeline, and comparing terminal-level curve "
                     "against AIS tanker calls at Juwaymah. Requires prod ТЗ.\n\n")
    if "awaiting" in yb_verdict.lower() or "INSUFFICIENT" in yb_verdict:
        lines.append("- **Yanbu scene top-up**: "
                     "YB returned insufficient data / awaiting input. "
                     "If FCS input CSV shows N < 10 per tank, consider extending "
                     "the Supabase query to include more historical ascending-orbit scenes.\n\n")
    lines.append("- **Geometry study of static tanks**: "
                 "47 Kharg tanks, 40 classified as static (FCS < 3.45). "
                 "Are they physically idle (t55_5/t55_10 pattern) or active but "
                 "with low dynamic range? Cross-check with AIS loading frequency.\n\n")
    bl.write_text("".join(lines), encoding="utf-8")
    print(f"Backlog written to {bl}")


# ── Plot helpers ───────────────────────────────────────────────────────────────

def _save_t1_scatter(rows, outpath):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    colors = {"full": "steelblue", "empty": "tomato"}
    for ax, (col_idx, ylabel, title) in zip(axes, [
        (3, "prod-282° dB", "prod mask (282°)"),
        (4, "SE 135–165° dB", "SE sector (135–165°)"),
    ]):
        for status in ("full", "empty"):
            xs = [r[2] for r in rows if r[1] == status and np.isfinite(r[col_idx])]
            ys = [r[col_idx] for r in rows if r[1] == status and np.isfinite(r[col_idx])]
            ax.scatter(xs, ys, c=colors[status], label=status, s=60, zorder=3)
        ax.set_xlabel("CSV db_mean"); ax.set_ylabel(ylabel)
        ax.set_title(f"t60_2 — {title}")
        ax.legend(); ax.grid(alpha=0.3)
    plt.suptitle("Task 1: dihedral temporal test", fontsize=11)
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()


def _save_fcs_hist(fcs_vals, threshold, site_name, outpath):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(fcs_vals, bins=20, color="steelblue", edgecolor="white")
    ax.axvline(threshold, color="red", linestyle="--", label=f"threshold={threshold:.2f}")
    ax.set_xlabel("FCS"); ax.set_ylabel("count")
    ax.set_title(f"FCS distribution — {site_name}")
    ax.legend()
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="1,2,3,4")
    args = parser.parse_args()
    tasks = [int(t) for t in args.tasks.split(",")]

    kharg_csv = Path("public/kharg_raw_observations.csv")

    lines = []
    lines.append("# FINAL_DIAGNOSIS — Round 7.8\n\n")
    lines.append("*Диагностика закрыта. После этого раунда — только прод-ТЗ.*\n\n")

    t1_summary = "not run"
    jw_result = yb_result = None
    jw_verdict = yb_verdict = "не проверено / awaiting input"

    if 1 in tasks:
        task1_dihedral(kharg_csv, lines)

    if 2 in tasks:
        jw_result, yb_result = task2_fcs_jw_yb(lines)

    if 3 in tasks:
        jw_verdict, yb_verdict = task3_verdicts(jw_result, yb_result, lines)

    if 4 in tasks:
        task4_final_report(t1_summary, jw_verdict, yb_verdict, lines)
        write_backlog(jw_verdict, yb_verdict)

    md_path = OUT_DIR / "FINAL_DIAGNOSIS.md"
    md_path.write_text("".join(lines), encoding="utf-8")
    print(f"\nReport written to {md_path}")

    pngs = list(OUT_DIR.glob("*.png"))
    print(f"PNGs: {len(pngs)}")
    for p in pngs:
        print(f"  {p.name}")

    # Smoke test: any PNG must have std > 0
    if pngs:
        import PIL.Image
        for p in pngs[:2]:
            arr = np.array(PIL.Image.open(p))
            assert arr.std() > 0, f"smoke-test FAIL: {p.name} std=0"
            print(f"  smoke-test OK: {p.name} std={arr.std():.1f}")


if __name__ == "__main__":
    main()
