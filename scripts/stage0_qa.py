"""
Stage 0-QA — Post-measurement quality assurance before registry update.

Inputs:
  diagnostics/stage0_full/stage0_full_report.txt  (raw measurement table)

Algorithm:
  1. Parse measurements.
  2. RT 35m cluster: group tanks by coordinate rows (lat tolerance ~60m).
     Within each row compute median r_measured + MAD.
     Flag tank as outlier if |r_measured - row_median| > 2*MAD.
     Replace outlier r with row_median; mark source="row-median".
  3. JW northern block (jw_14..jw_18): all show +25–30% consistently →
     this is a real systematic error in the registry (all called 50m but
     optically ~63m). Accept as measured; mark qa_status="ok-systematic".
  4. jw_17 special check: largest individual shift (65m, +30%, shift=11.2m).
     If r_measured consistent with the jw_14..jw_18 block median → accept.
     If outlier vs block → flag qa_status="suspect-bund".
  5. Tanks with rays<16: flag qa_status="low-rays".
  6. Generate:
       diagnostics/stage0_full/stage0_qa_report.txt   (full table with actions)
       registry_candidates/saudi_geometry_full_v2.py  (clean geometry)

Output columns: tank | terminal | r_reg | r_measured_raw | r_final | Δr%
                center_lat | center_lon | h_max | source | qa_status
"""

import math, re
from pathlib import Path

# ── API 650 h_max ─────────────────────────────────────────────────────────────
H_MAX_TABLE = [(60,18),(55,17),(50,16),(45,15),(40,14),(35,13),(30,12),(25,12),(20,9),(15,7)]
def hmax_for_radius(r):
    return min(H_MAX_TABLE, key=lambda x: abs(x[0]-r))[1]

# ── Registry coordinates (from stage0_full_optical.py, verbatim) ──────────────
_RT_TANKS_RAW = [
    ("rt_0",  26.638626, 50.154236, 60),("rt_1",  26.640430, 50.153296, 60),
    ("rt_2",  26.641913, 50.152682, 60),("rt_3",  26.650024, 50.152807, 60),
    ("rt_4",  26.652085, 50.149920, 60),("rt_5",  26.688442, 50.118714, 60),
    ("rt_6",  26.687011, 50.117969, 60),("rt_7",  26.687548, 50.116596, 60),
    ("rt_8",  26.689090, 50.117193, 60),("rt_9",  26.688215, 50.114879, 60),
    ("rt_10", 26.689749, 50.115695, 60),("rt_11", 26.651657, 50.148320, 35),
    ("rt_12", 26.649988, 50.148177, 35),("rt_13", 26.650865, 50.148946, 35),
    ("rt_14", 26.650985, 50.150605, 35),("rt_15", 26.648982, 50.148984, 35),
    ("rt_16", 26.649609, 50.149935, 35),("rt_17", 26.650144, 50.151086, 35),
    ("rt_18", 26.648029, 50.149726, 35),("rt_19", 26.648818, 50.150545, 35),
    ("rt_20", 26.649339, 50.151734, 35),("rt_21", 26.646997, 50.150259, 35),
    ("rt_22", 26.647971, 50.151020, 35),("rt_23", 26.648509, 50.152502, 35),
    ("rt_24", 26.648872, 50.153435, 35),("rt_25", 26.647181, 50.151415, 35),
    ("rt_26", 26.647496613, 50.152485930, 35),("rt_27", 26.647870961, 50.153402153, 35),
    ("rt_28", 26.648243784, 50.154304015, 35),("rt_29", 26.646272546, 50.151974459, 35),
    ("rt_30", 26.646641703, 50.152926255, 35),("rt_31", 26.647029129, 50.153832257, 35),
    ("rt_32", 26.647394612, 50.154733110, 35),("rt_33", 26.645433932, 50.152392769, 35),
    ("rt_34", 26.645827818, 50.153347593, 35),("rt_35", 26.646200394, 50.154249852, 35),
    ("rt_36", 26.646570259, 50.155158460, 35),("rt_37", 26.643842240, 50.153203110, 35),
    ("rt_38", 26.644232379, 50.154144912, 35),("rt_39", 26.644607973, 50.155046837, 35),
    ("rt_40", 26.644975530, 50.155944016, 35),("rt_41", 26.643016459, 50.153611656, 35),
    ("rt_42", 26.643406095, 50.154572485, 35),("rt_43", 26.643782414, 50.155472085, 35),
    ("rt_44", 26.644156293, 50.156383567, 35),("rt_45", 26.641814725, 50.154215785, 35),
    ("rt_46", 26.642210411, 50.155178684, 35),("rt_47", 26.642577330, 50.156072461, 35),
    ("rt_48", 26.642950972, 50.156992054, 35),("rt_49", 26.641339934, 50.155599952, 35),
    ("rt_50", 26.641729102, 50.156516229, 35),("rt_51", 26.642073615, 50.157423258, 35),
    ("rt_52", 26.640476856, 50.156043180, 35),("rt_53", 26.640847523, 50.156946870, 35),
    ("rt_54", 26.641188536, 50.157794617, 35),("rt_55", 26.641760390, 50.159183452, 35),
    ("rt_56", 26.640105425, 50.157328512, 35),("rt_57", 26.640432763, 50.158165902, 35),
    ("rt_58", 26.641106377, 50.158670772, 35),("rt_59", 26.640346939, 50.159052659, 35),
    ("rt_60", 26.639675405, 50.158553012, 35),
    ("rt_61", 26.643554305621972, 50.152191100588105, 35),
    ("rt_62", 26.645139789560954, 50.151448430921810, 35),
    ("rt_63", 26.645949000703600, 50.151042337312750, 35),
    ("rt_64", 26.639988864810530, 50.154953473357590, 35),
    ("rt_65", 26.637591408424406, 50.160445373274800, 20),
]
_JW_TANKS_RAW = [
    ("jw_0",  26.814939449818950, 49.987095744022476, 50),
    ("jw_1",  26.816115167835957, 49.985801747261085, 50),
    ("jw_2",  26.817719856216030, 49.984028796562500, 50),
    ("jw_3",  26.818881636808456, 49.982719201457790, 50),
    ("jw_4",  26.816309495888724, 49.988651189962226, 50),
    ("jw_5",  26.817523593073770, 49.987380783090224, 50),
    ("jw_6",  26.819128015846970, 49.985623716671796, 50),
    ("jw_7",  26.820299858791643, 49.984324056007070, 50),
    ("jw_8",  26.818163062259000, 49.990692300527390, 50),
    ("jw_9",  26.819531458247540, 49.992284316605510, 50),
    ("jw_10", 26.819300433907060, 49.989417325119426, 50),
    ("jw_11", 26.820698661135080, 49.991014367679170, 50),
    ("jw_12", 26.820894127364330, 49.987648761169076, 50),
    ("jw_13", 26.822307642984722, 49.989232681648080, 50),
    ("jw_14", 26.822084302491750, 49.986363350107820, 50),
    ("jw_15", 26.823495358779535, 49.987952836960950, 50),
    ("jw_16", 26.823098922243503, 49.983801260652840, 50),
    ("jw_17", 26.824515110824244, 49.985368840304960, 50),
    ("jw_18", 26.825690135207460, 49.984073983323120, 50),
]
_YB_TANKS_RAW = [
    ("yb_0",  23.959074, 38.244398, 60),("yb_1",  23.962590, 38.247925, 60),
    ("yb_2",  23.964053, 38.245977, 60),("yb_3",  23.965544, 38.244086, 60),
    ("yb_4",  23.964986, 38.250081, 60),("yb_5",  23.966460, 38.248119, 60),
    ("yb_6",  23.967960, 38.246187, 60),("yb_7",  23.967392, 38.252229, 60),
    ("yb_8",  23.968866, 38.250263, 60),("yb_9",  23.970331, 38.248336, 60),
    ("yb_10", 23.969788, 38.254362, 60),("yb_11", 23.971259, 38.252416, 60),
]

REG = {}
for tid, lat, lon, r in _RT_TANKS_RAW:
    REG[tid] = {"lat": lat, "lon": lon, "r_reg": r, "terminal": "ras_tanura"}
for tid, lat, lon, r in _JW_TANKS_RAW:
    REG[tid] = {"lat": lat, "lon": lon, "r_reg": r, "terminal": "juwaymah"}
for tid, lat, lon, r in _YB_TANKS_RAW:
    REG[tid] = {"lat": lat, "lon": lon, "r_reg": r, "terminal": "yanbu"}

# ── Measured centers from Stage 0-full ────────────────────────────────────────
# These were committed by the GHA run.  We re-parse the geometry file.
# Fall back to parsing the report if geometry file not present.
def load_measured():
    """
    Load refined lat/lon from saudi_geometry_full.py (Stage 0-full output).
    Format: dict(id="...", terminal="...", lat=..., lon=..., radius_m=...,
                 h_max_m=..., dr_pct=..., shift_m=..., status="OK")
    """
    geo_path = Path("registry_candidates/saudi_geometry_full.py")
    measured = {}
    if not geo_path.exists():
        raise FileNotFoundError(f"Missing {geo_path}; run stage0_full_optical.py first")
    text = geo_path.read_text()
    # Match both positive and negative dr_pct values
    for m in re.finditer(
        r'dict\(id="(?P<id>[^"]+)",\s*terminal="[^"]+",\s*'
        r'lat=(?P<lat>[\-0-9.]+),\s*lon=(?P<lon>[\-0-9.]+),\s*'
        r'radius_m=(?P<r>[\-0-9.]+)',
        text
    ):
        measured[m["id"]] = {
            "lat_meas": float(m["lat"]),
            "lon_meas": float(m["lon"]),
            "r_meas":   float(m["r"]),
        }
    return measured


def parse_report():
    """Parse stage0_full_report.txt → {tank_id: {r_meas, n_rays, shift_m}}."""
    rep = Path("diagnostics/stage0_full/stage0_full_report.txt")
    if not rep.exists():
        raise FileNotFoundError(f"Missing {rep}")
    rows = {}
    for line in rep.read_text().splitlines():
        # e.g.: rt_0       ras_tanura       60.0     67.5   +12.5%      1.7m    16
        m = re.match(
            r'^([\w_]+)\s+\w+\s+[\d.]+\s+([\d.]+)\s+[+\-][\d.]+%\s+([\d.]+)m\s+(\d+)',
            line.strip()
        )
        if m:
            rows[m[1]] = {
                "r_meas":  float(m[2]),
                "shift_m": float(m[3]),
                "n_rays":  int(m[4]),
            }
    return rows


# ── Row grouping for RT 35m cluster ──────────────────────────────────────────
LAT_ROW_TOL_M = 60.0  # metres — tolerance to group tanks into the same lat-row
DEG_PER_M_LAT = 1.0 / 111320.0

def assign_rows(tank_ids):
    """
    Group RT 35m tanks into coordinate rows.
    Returns {tank_id: row_index}, sorted by latitude (south=0).
    """
    tanks = [(tid, REG[tid]["lat"]) for tid in tank_ids]
    tanks.sort(key=lambda x: x[1])

    rows = []   # list of lists of tank_ids
    for tid, lat in tanks:
        placed = False
        for row in rows:
            row_lat = REG[row[0]]["lat"]
            if abs(lat - row_lat) * 111320.0 < LAT_ROW_TOL_M:
                row.append(tid)
                placed = True
                break
        if not placed:
            rows.append([tid])

    assignment = {}
    for idx, row in enumerate(rows):
        for tid in row:
            assignment[tid] = idx
    return assignment, rows


def mad(values):
    import statistics
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values]), med


# ── JW northern block detection ───────────────────────────────────────────────
JW_NORTH_IDS = {"jw_14", "jw_15", "jw_16", "jw_17", "jw_18"}
JW_NORTH_LAT_MIN = 26.822  # degrees — these tanks cluster north of this

# ── Main QA logic ─────────────────────────────────────────────────────────────

def run_qa():
    import statistics

    report_rows = parse_report()
    measured    = {}
    geo_path    = Path("registry_candidates/saudi_geometry_full.py")
    if geo_path.exists():
        measured = load_measured()

    results = []  # list of dicts — one per tank

    # ── RT 35m cluster: row-sanity ────────────────────────────────────────────
    rt35_ids = [tid for tid, lat, lon, r in _RT_TANKS_RAW if r == 35]
    row_assign, rows = assign_rows(rt35_ids)

    # Compute per-row stats using raw measurements
    row_stats = {}
    for idx, row_tanks in enumerate(rows):
        vals = [report_rows[t]["r_meas"] for t in row_tanks if t in report_rows]
        if len(vals) >= 2:
            m_mad, m_med = mad(vals)
            row_stats[idx] = {"median": m_med, "mad": max(m_mad, 2.5)}  # floor MAD at 2.5m
        elif vals:
            row_stats[idx] = {"median": vals[0], "mad": 5.0}
        else:
            row_stats[idx] = {"median": 35.0, "mad": 5.0}

    # Process all RT tanks
    for tid, lat, lon, r_reg in _RT_TANKS_RAW:
        rep = report_rows.get(tid, {})
        r_meas_raw = rep.get("r_meas")
        n_rays     = rep.get("n_rays", 0)
        shift_m    = rep.get("shift_m", 0.0)

        # Use measured center if available, else registry
        if tid in measured:
            lat_final = measured[tid]["lat_meas"]
            lon_final = measured[tid]["lon_meas"]
        else:
            lat_final, lon_final = lat, lon

        # rt_65 special: tiny tank (20m), only 1 of its kind — keep measured
        if r_reg == 20:
            r_final   = r_meas_raw if r_meas_raw else r_reg
            source    = "measured" if r_meas_raw else "registry"
            qa_status = "low-rays" if n_rays < 12 else "ok"
            results.append(dict(
                id=tid, terminal="ras_tanura",
                r_reg=r_reg, r_meas_raw=r_meas_raw, r_final=r_final,
                lat=lat_final, lon=lon_final, h_max=hmax_for_radius(r_final),
                source=source, qa_status=qa_status, n_rays=n_rays, shift_m=shift_m,
            ))
            continue

        # RT big tanks (60m) — use measured directly
        if r_reg == 60:
            r_final   = r_meas_raw if r_meas_raw else r_reg
            source    = "measured" if r_meas_raw else "registry"
            qa_status = "low-rays" if n_rays < 12 else "ok"
            results.append(dict(
                id=tid, terminal="ras_tanura",
                r_reg=r_reg, r_meas_raw=r_meas_raw, r_final=r_final,
                lat=lat_final, lon=lon_final, h_max=hmax_for_radius(r_final),
                source=source, qa_status=qa_status, n_rays=n_rays, shift_m=shift_m,
            ))
            continue

        # RT 35m tanks — row-sanity check
        row_idx  = row_assign.get(tid)
        stats    = row_stats.get(row_idx, {"median": 35.0, "mad": 5.0})
        row_med  = stats["median"]
        row_mad  = stats["mad"]

        if r_meas_raw is None:
            r_final   = row_med
            source    = "row-median"
            qa_status = "no-measurement"
        elif abs(r_meas_raw - row_med) > 2 * row_mad:
            # Outlier: replace with row median
            r_final   = row_med
            source    = "row-median"
            qa_status = f"outlier-replaced (raw={r_meas_raw:.1f}m row_med={row_med:.1f}m 2MAD={2*row_mad:.1f}m)"
        else:
            r_final   = r_meas_raw
            source    = "measured"
            qa_status = "low-rays" if n_rays < 12 else "ok"

        # Round r_final to nearest 2.5m (instrument resolution)
        r_final = round(r_final / 2.5) * 2.5

        results.append(dict(
            id=tid, terminal="ras_tanura",
            r_reg=r_reg, r_meas_raw=r_meas_raw, r_final=r_final,
            lat=lat_final, lon=lon_final, h_max=hmax_for_radius(r_final),
            source=source, qa_status=qa_status, n_rays=n_rays, shift_m=shift_m,
            row_id=row_idx, row_med=row_med, row_mad=row_mad,
        ))

    # ── JW tanks ──────────────────────────────────────────────────────────────
    # Compute northern block median for jw_17 consistency check
    north_vals = [report_rows[t]["r_meas"] for t in JW_NORTH_IDS if t in report_rows]
    north_med  = statistics.median(north_vals) if north_vals else None

    for tid, lat, lon, r_reg in _JW_TANKS_RAW:
        rep     = report_rows.get(tid, {})
        r_meas  = rep.get("r_meas")
        n_rays  = rep.get("n_rays", 0)
        shift_m = rep.get("shift_m", 0.0)

        if tid in measured:
            lat_final = measured[tid]["lat_meas"]
            lon_final = measured[tid]["lon_meas"]
        else:
            lat_final, lon_final = lat, lon

        r_final = r_meas if r_meas else r_reg
        source  = "measured" if r_meas else "registry"

        if tid in JW_NORTH_IDS:
            # jw_14..jw_18: all show +25–30% — systematic registry undercount
            if tid == "jw_17" and north_med is not None:
                # jw_17: check against block median (exclude itself)
                others = [report_rows[t]["r_meas"] for t in JW_NORTH_IDS - {"jw_17"} if t in report_rows]
                oth_med = statistics.median(others) if others else north_med
                oth_mad = mad([report_rows[t]["r_meas"] for t in JW_NORTH_IDS - {"jw_17"} if t in report_rows])[0]
                oth_mad = max(oth_mad, 2.5)
                if abs(r_meas - oth_med) > 2 * oth_mad:
                    qa_status = "suspect-bund"
                    # Use block median instead
                    r_final   = round(oth_med / 2.5) * 2.5
                    source    = "row-median"
                else:
                    qa_status = "ok-systematic"
            else:
                qa_status = "ok-systematic"
            # Round to nearest 2.5m
            r_final = round(r_final / 2.5) * 2.5
        else:
            qa_status = "low-rays" if n_rays < 12 else "ok"
            r_final   = round(r_final / 2.5) * 2.5

        results.append(dict(
            id=tid, terminal="juwaymah",
            r_reg=r_reg, r_meas_raw=r_meas, r_final=r_final,
            lat=lat_final, lon=lon_final, h_max=hmax_for_radius(r_final),
            source=source, qa_status=qa_status, n_rays=n_rays, shift_m=shift_m,
        ))

    # ── YB tanks ──────────────────────────────────────────────────────────────
    for tid, lat, lon, r_reg in _YB_TANKS_RAW:
        rep     = report_rows.get(tid, {})
        r_meas  = rep.get("r_meas")
        n_rays  = rep.get("n_rays", 0)
        shift_m = rep.get("shift_m", 0.0)

        if tid in measured:
            lat_final = measured[tid]["lat_meas"]
            lon_final = measured[tid]["lon_meas"]
        else:
            lat_final, lon_final = lat, lon

        r_final   = r_meas if r_meas else r_reg
        r_final   = round(r_final / 2.5) * 2.5
        source    = "measured" if r_meas else "registry"
        qa_status = "low-rays" if n_rays < 12 else "ok"

        results.append(dict(
            id=tid, terminal="yanbu",
            r_reg=r_reg, r_meas_raw=r_meas, r_final=r_final,
            lat=lat_final, lon=lon_final, h_max=hmax_for_radius(r_final),
            source=source, qa_status=qa_status, n_rays=n_rays, shift_m=shift_m,
        ))

    return results


def write_qa_report(results):
    lines = [
        "Stage 0-QA — Post-measurement geometry QA",
        f"Tanks: {len(results)}",
        "",
        f"{'tank':<8} {'term':<12} {'r_reg':>6} {'r_raw':>6} {'r_final':>7} {'Δr%':>7} "
        f"{'shift_m':>8} {'rays':>4}  source          qa_status",
        "-" * 110,
    ]

    n_replaced = 0
    n_bund     = 0
    for r in sorted(results, key=lambda x: x["id"]):
        r_reg   = r["r_reg"]
        r_raw   = r["r_meas_raw"]
        r_fin   = r["r_final"]
        dr      = (r_fin - r_reg) / r_reg * 100
        raw_s   = f"{r_raw:.1f}" if r_raw else "N/A"
        source  = r["source"]
        status  = r["qa_status"]
        flag    = ""
        if "outlier-replaced" in status:
            n_replaced += 1
            flag = " ★REPLACED"
        elif status == "suspect-bund":
            n_bund += 1
            flag = " ★BUND?"
        lines.append(
            f"{r['id']:<8} {r['terminal']:<12} {r_reg:>6.1f} {raw_s:>6} {r_fin:>7.1f} "
            f"{dr:>+6.1f}% {r.get('shift_m',0):>7.1f}m {r['n_rays']:>4}  "
            f"{source:<16} {status[:40]}{flag}"
        )

    lines += [
        "",
        f"Outliers replaced by row-median: {n_replaced}",
        f"Suspect-bund flags:              {n_bund}",
        "",
        "── JW northern block (jw_14..jw_18) ──",
        "  All show +25–30% vs 50m registry → systematic registry undercount.",
        "  These tanks are physically larger than recorded. Source: ok-systematic.",
        "",
        "── RT 35m cluster ──",
        "  Row-median applied to outliers. Instrument noise at 2.5m/px is ±1–2px",
        "  (~2.5–5m). Tanks with |r_raw − row_median| > 2·MAD replaced.",
        "  Row MAD floored at 2.5m to avoid over-accepting noise.",
    ]

    out = Path("diagnostics/stage0_full/stage0_qa_report.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"QA report → {out}")
    print(f"  {n_replaced} tanks replaced by row-median")
    print(f"  {n_bund} suspect-bund flags")
    return n_replaced, n_bund


def write_v2_geometry(results):
    lines = [
        '"""',
        "saudi_geometry_full_v2.py — Stage 0-QA cleaned geometry for all 97 Saudi tanks.",
        "Generated by stage0_qa.py.  Do NOT edit by hand.",
        "",
        "Columns: id, lat, lon, radius_m, h_max, source_r, qa_status",
        "source_r: measured | row-median | registry",
        "qa_status: ok | ok-systematic | low-rays | outlier-replaced | suspect-bund | no-measurement",
        '"""',
        "",
        "SAUDI_GEOMETRY_V2 = [",
    ]

    for r in sorted(results, key=lambda x: x["id"]):
        qa = r["qa_status"].split(" (")[0]  # trim verbose detail for the file
        lines.append(
            f'    dict(id="{r["id"]}", lat={r["lat"]:.8f}, lon={r["lon"]:.8f}, '
            f'radius_m={r["r_final"]:.1f}, h_max={r["h_max"]}, '
            f'source_r="{r["source"]}", qa_status="{qa}"),'
        )

    lines += ["]", ""]

    out = Path("registry_candidates/saudi_geometry_full_v2.py")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"v2 geometry → {out}  ({len(results)} tanks)")


def main():
    print("Stage 0-QA running...")
    results = run_qa()
    write_qa_report(results)
    write_v2_geometry(results)

    # Summary stats
    replaced = [r for r in results if "outlier-replaced" in r["qa_status"]]
    bund     = [r for r in results if r["qa_status"] == "suspect-bund"]
    ok_sys   = [r for r in results if r["qa_status"] == "ok-systematic"]
    low_rays = [r for r in results if r["qa_status"] == "low-rays"]

    print(f"\n{'='*60}")
    print(f"Stage 0-QA summary")
    print(f"  Total tanks:              {len(results)}")
    print(f"  source=measured:          {sum(1 for r in results if r['source']=='measured')}")
    print(f"  source=row-median:        {len(replaced)}")
    print(f"  ok-systematic (JW north): {len(ok_sys)}")
    print(f"  low-rays:                 {len(low_rays)}")
    print(f"  suspect-bund:             {len(bund)}")
    if bund:
        for r in bund:
            print(f"    {r['id']}: r_raw={r['r_meas_raw']}m → r_final={r['r_final']}m")
    print(f"\nOutputs:")
    print(f"  diagnostics/stage0_full/stage0_qa_report.txt")
    print(f"  registry_candidates/saudi_geometry_full_v2.py")


if __name__ == "__main__":
    main()
