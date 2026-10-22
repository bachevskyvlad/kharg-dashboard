"""
export_event_csv.py — Read-only export for retrospective event analysis.

Writes to diagnostics/research/2026-10/event_csv/ on the research/2026-10-auto branch.
NO writes to any production table. NO correlation analysis.

Output files:
  1. kharg_raw_observations.csv
  2. rt_terminal_observations.csv
  3. yanbu_terminal_observations.csv
  4. registry_kharg_rt_yanbu.csv
  5. tvt_kharg_rt_yanbu.csv
  6. calibration_params.csv
  7. anchors.csv  (+ missing_anchors.txt)
  8. README.md
"""

import os, sys, json, csv, io
from datetime import datetime, date
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Supabase ──────────────────────────────────────────────────────────────────
from config import sb

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "diagnostics", "research", "2026-10", "event_csv"
)
os.makedirs(OUT_DIR, exist_ok=True)

SCRIPT_SHA = os.environ.get("GITHUB_SHA", "local")[:12]
RUN_ID     = os.environ.get("GITHUB_RUN_ID", "local")
EXPORT_TS  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Helpers ───────────────────────────────────────────────────────────────────
def fetch_all_rows(table, select_cols, filters=None, order_col=None):
    rows = []; offset = 0
    while True:
        q = sb.table(table).select(select_cols)
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        if order_col:
            q = q.order(order_col)
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows

def write_csv(path, rows, fieldnames=None):
    if not rows:
        print(f"  WARNING: 0 rows for {path}")
        with open(path, "w") as f:
            f.write("")
        return 0
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)

# ── CDSE catalog helpers ───────────────────────────────────────────────────────
from sentinelhub import SHConfig, BBox, CRS, SentinelHubCatalog, DataCollection

def _make_config():
    cfg = SHConfig()
    cfg.sh_client_id     = os.environ.get("CLIENT_ID", "")
    cfg.sh_client_secret = os.environ.get("CLIENT_SECRET", "")
    cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
    return cfg

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_exp", service_url="https://sh.dataspace.copernicus.eu"
)

def cdse_date_orbit_map(bbox, start="2014-01-01", end=None):
    """
    Query CDSE catalog for bbox → {date_str: (relative_orbit, orbit_direction)}.
    Multiple scenes on the same date → keep most common (majority vote).
    """
    if end is None:
        end = datetime.utcnow().strftime("%Y-%m-%d")
    cfg = _make_config()
    catalog = SentinelHubCatalog(config=cfg)
    results = list(catalog.search(
        collection=S1_CDSE,
        bbox=bbox,
        time=(start, end),
        fields={"include": ["id", "properties.datetime",
                            "properties.eo:bands",
                            "properties.sat:orbit_state",
                            "properties.sat:relative_orbit"]},
        limit=200,
    ))
    date_map = {}
    for r in results:
        props = r.get("properties", {})
        dt_str = (props.get("datetime") or "")[:10]
        if not dt_str:
            continue
        ro  = props.get("sat:relative_orbit")
        dir_ = (props.get("sat:orbit_state") or "").upper()
        if dt_str not in date_map:
            date_map[dt_str] = []
        date_map[dt_str].append((ro, dir_))
    # Majority vote per date
    merged = {}
    for dt_str, entries in date_map.items():
        ro_vals  = [e[0] for e in entries if e[0] is not None]
        dir_vals = [e[1] for e in entries if e[1]]
        ro  = max(set(ro_vals),  key=ro_vals.count)  if ro_vals  else None
        dir_ = max(set(dir_vals), key=dir_vals.count) if dir_vals else ""
        merged[dt_str] = (ro, dir_)
    print(f"  CDSE catalog: {len(merged)} unique dates from {len(results)} scenes")
    return merged

# ── 1. kharg_raw_observations.csv ─────────────────────────────────────────────
print("\n[1/8] kharg_raw_observations.csv")
kharg_rows = fetch_all_rows(
    "raw_observations",
    "date,tank_id,db_mean,created_at",
    order_col="date"
)
print(f"  raw_observations: {len(kharg_rows)} rows")

# CDSE orbit map for Kharg bbox
from locations import raw_tanks, get_super_bbox
import numpy as np
kharg_bbox = get_super_bbox(raw_tanks, buffer_m=200)

print("  Querying CDSE for Kharg orbit map…")
try:
    kharg_orbit_map = cdse_date_orbit_map(kharg_bbox)
except Exception as e:
    print(f"  CDSE query failed: {e}"); kharg_orbit_map = {}

for r in kharg_rows:
    dt = str(r.get("date", ""))[:10]
    ro, dir_ = kharg_orbit_map.get(dt, (None, ""))
    r["relative_orbit_cdse"] = ro
    r["orbit_direction_cdse"] = dir_

path1 = os.path.join(OUT_DIR, "kharg_raw_observations.csv")
n1 = write_csv(path1, kharg_rows,
    ["date", "tank_id", "db_mean", "created_at", "relative_orbit_cdse", "orbit_direction_cdse"])
dates1 = sorted({str(r["date"])[:10] for r in kharg_rows})
print(f"  → {n1} rows, {len(dates1)} unique dates, {dates1[0]}–{dates1[-1]}")

# ── 2. rt_terminal_observations.csv ───────────────────────────────────────────
print("\n[2/8] rt_terminal_observations.csv")
# Try fetching with orbit columns; fall back if they don't exist
try:
    rt_rows = fetch_all_rows(
        "terminal_observations",
        "date,tank_id,db_mean,orbit_direction,relative_orbit,created_at",
        filters={"terminal_id": "ras_tanura"},
        order_col="date"
    )
    has_rt_orbit_cols = True
except Exception:
    rt_rows = fetch_all_rows(
        "terminal_observations",
        "date,tank_id,db_mean,created_at",
        filters={"terminal_id": "ras_tanura"},
        order_col="date"
    )
    has_rt_orbit_cols = False
    for r in rt_rows:
        r["orbit_direction"] = None
        r["relative_orbit"]  = None

print(f"  terminal_observations RT: {len(rt_rows)} rows, orbit_cols={has_rt_orbit_cols}")

# CDSE orbit map for RT bbox
from locations import TERMINALS
rt_term = next((t for t in TERMINALS if t["id"] == "ras_tanura"), None)
if rt_term:
    rt_tanks = rt_term["tanks"]
    lats = [t["lat"] for t in rt_tanks]; lons = [t["lon"] for t in rt_tanks]
    rt_bbox = BBox([min(lons)-0.01, min(lats)-0.01, max(lons)+0.01, max(lats)+0.01], crs=CRS.WGS84)
    print("  Querying CDSE for RT orbit map…")
    try:
        rt_orbit_map = cdse_date_orbit_map(rt_bbox)
    except Exception as e:
        print(f"  CDSE query failed: {e}"); rt_orbit_map = {}
else:
    rt_orbit_map = {}
    print("  RT terminal not found in TERMINALS")

for r in rt_rows:
    dt = str(r.get("date", ""))[:10]
    ro_cdse, dir_cdse = rt_orbit_map.get(dt, (None, ""))
    r["relative_orbit_cdse"]  = ro_cdse
    r["orbit_direction_cdse"] = dir_cdse

path2 = os.path.join(OUT_DIR, "rt_terminal_observations.csv")
n2 = write_csv(path2, rt_rows,
    ["date", "tank_id", "db_mean", "orbit_direction", "relative_orbit", "created_at",
     "relative_orbit_cdse", "orbit_direction_cdse"])
dates2 = sorted({str(r["date"])[:10] for r in rt_rows})
print(f"  → {n2} rows, {len(dates2)} unique dates, {dates2[0]}–{dates2[-1]}")

# ── 3. yanbu_terminal_observations.csv ────────────────────────────────────────
print("\n[3/8] yanbu_terminal_observations.csv")
try:
    yn_rows = fetch_all_rows(
        "terminal_observations",
        "date,tank_id,db_mean,orbit_direction,relative_orbit,created_at",
        filters={"terminal_id": "yanbu"},
        order_col="date"
    )
    has_yn_orbit_cols = True
except Exception:
    yn_rows = fetch_all_rows(
        "terminal_observations",
        "date,tank_id,db_mean,created_at",
        filters={"terminal_id": "yanbu"},
        order_col="date"
    )
    has_yn_orbit_cols = False
    for r in yn_rows:
        r["orbit_direction"] = None
        r["relative_orbit"]  = None

print(f"  terminal_observations Yanbu: {len(yn_rows)} rows, orbit_cols={has_yn_orbit_cols}")

yn_term = next((t for t in TERMINALS if t["id"] == "yanbu"), None)
if yn_term:
    yn_tanks = yn_term["tanks"]
    lats = [t["lat"] for t in yn_tanks]; lons = [t["lon"] for t in yn_tanks]
    yn_bbox = BBox([min(lons)-0.01, min(lats)-0.01, max(lons)+0.01, max(lats)+0.01], crs=CRS.WGS84)
    print("  Querying CDSE for Yanbu orbit map…")
    try:
        yn_orbit_map = cdse_date_orbit_map(yn_bbox)
    except Exception as e:
        print(f"  CDSE query failed: {e}"); yn_orbit_map = {}
else:
    yn_orbit_map = {}
    print("  Yanbu terminal not found in TERMINALS")

for r in yn_rows:
    dt = str(r.get("date", ""))[:10]
    ro_cdse, dir_cdse = yn_orbit_map.get(dt, (None, ""))
    r["relative_orbit_cdse"]  = ro_cdse
    r["orbit_direction_cdse"] = dir_cdse

path3 = os.path.join(OUT_DIR, "yanbu_terminal_observations.csv")
n3 = write_csv(path3, yn_rows,
    ["date", "tank_id", "db_mean", "orbit_direction", "relative_orbit", "created_at",
     "relative_orbit_cdse", "orbit_direction_cdse"])
dates3 = sorted({str(r["date"])[:10] for r in yn_rows})
period3 = f"{dates3[0]}–{dates3[-1]}" if dates3 else "empty"
print(f"  → {n3} rows, period: {period3}")

# ── 4. registry_kharg_rt_yanbu.csv ────────────────────────────────────────────
print("\n[4/8] registry_kharg_rt_yanbu.csv")
from tank_registry import LOCATIONS

# Load whitelist for RT
wl_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "diagnostics", "stage3", "whitelist_results.json"
)
whitelist_ids = set()
if os.path.exists(wl_path):
    with open(wl_path) as f:
        wl_data = json.load(f)
    # whitelist_results.json: list of tank_ids or dict with "whitelist" key
    if isinstance(wl_data, list):
        whitelist_ids = {str(x) for x in wl_data}
    elif isinstance(wl_data, dict):
        whitelist_ids = {str(x) for x in wl_data.get("whitelist", [])}
    print(f"  Whitelist loaded: {len(whitelist_ids)} tank_ids")
else:
    print(f"  WARNING: whitelist not found at {wl_path}")

by_id = {loc["id"]: loc for loc in LOCATIONS}
target_terminals = ["kharg", "ras_tanura", "yanbu"]

reg_rows = []
for tid in target_terminals:
    loc = by_id.get(tid)
    if not loc:
        print(f"  WARNING: {tid} not found in LOCATIONS"); continue
    for t in loc["tanks"]:
        row = {
            "tank_id":     t["id"],
            "terminal_id": tid,
            "lat":         t["lat"],
            "lon":         t["lon"],
            "radius_m":    t["radius_m"],
            "h_max_m":     t["h_max_m"],
            "enabled":     t.get("enabled", True),
            "in_whitelist": (t["id"] in whitelist_ids) if tid == "ras_tanura" else "",
            "los_azimuth": loc.get("los_azimuth", ""),
            "uncertain":   t.get("uncertain", False),
            "notes":       t.get("notes", ""),
        }
        reg_rows.append(row)

path4 = os.path.join(OUT_DIR, "registry_kharg_rt_yanbu.csv")
n4 = write_csv(path4, reg_rows,
    ["tank_id", "terminal_id", "lat", "lon", "radius_m", "h_max_m",
     "enabled", "in_whitelist", "los_azimuth", "uncertain", "notes"])
print(f"  → {n4} rows ({sum(1 for r in reg_rows if r['terminal_id']=='kharg')} Kharg, "
      f"{sum(1 for r in reg_rows if r['terminal_id']=='ras_tanura')} RT, "
      f"{sum(1 for r in reg_rows if r['terminal_id']=='yanbu')} Yanbu)")

# ── 5. tvt_kharg_rt_yanbu.csv ─────────────────────────────────────────────────
print("\n[5/8] tvt_kharg_rt_yanbu.csv")
tvt_rows = []

# Kharg: volume_timeseries
kharg_tvt = fetch_all_rows("volume_timeseries", "*", order_col="date")
for r in kharg_tvt:
    r["terminal_id"] = "kharg"
tvt_rows.extend(kharg_tvt)
print(f"  volume_timeseries (Kharg): {len(kharg_tvt)} rows")

# RT + Yanbu: terminal_volume_timeseries
for tid in ["ras_tanura", "yanbu"]:
    rows_t = fetch_all_rows(
        "terminal_volume_timeseries", "*",
        filters={"terminal_id": tid},
        order_col="date"
    )
    tvt_rows.extend(rows_t)
    print(f"  terminal_volume_timeseries ({tid}): {len(rows_t)} rows")

# Normalize columns (may differ between tables)
all_cols = set()
for r in tvt_rows:
    all_cols.update(r.keys())
std_cols = ["terminal_id", "date", "aggregation", "smoothed", "vol_mn_bbl"]
extra = sorted(all_cols - set(std_cols))
fieldnames5 = std_cols + extra

path5 = os.path.join(OUT_DIR, "tvt_kharg_rt_yanbu.csv")
n5 = write_csv(path5, tvt_rows, fieldnames5)
dates5 = sorted({str(r["date"])[:10] for r in tvt_rows})
print(f"  → {n5} rows, period: {dates5[0]}–{dates5[-1]}")

# ── 6. calibration_params.csv ─────────────────────────────────────────────────
print("\n[6/8] calibration_params.csv")

# Compute live calibration from DB data for each terminal
# Kharg: global percentiles from raw_observations
# RT/Yanbu: from terminal_observations (per-orbit if orbit cols exist)

def _compute_calib(rows, col="db_mean", per_orbit=False, orbit_col="orbit_direction"):
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    out = []
    if per_orbit and orbit_col in df.columns and df[orbit_col].notna().any():
        for dir_, grp in df.groupby(orbit_col):
            vals = grp[col].dropna()
            out.append({
                "group": str(dir_),
                "n_obs": len(vals),
                "db_p05": round(float(vals.quantile(0.05)), 4),
                "db_p95": round(float(vals.quantile(0.95)), 4),
                "db_mean": round(float(vals.mean()), 4),
                "db_std":  round(float(vals.std()), 4),
            })
    else:
        vals = df[col].dropna()
        out.append({
            "group": "global",
            "n_obs": len(vals),
            "db_p05": round(float(vals.quantile(0.05)), 4),
            "db_p95": round(float(vals.quantile(0.95)), 4),
            "db_mean": round(float(vals.mean()), 4),
            "db_std":  round(float(vals.std()), 4),
        })
    return out

calib_rows = []
for terminal_id, rows, per_orbit_flag, src_table, override_min, override_max in [
    ("kharg",      kharg_rows, False, "raw_observations",         None, None),
    ("ras_tanura", rt_rows,    True,  "terminal_observations",
     rt_term.get("db_min") if rt_term else None,
     rt_term.get("db_max") if rt_term else None),
    ("yanbu",      yn_rows,    True,  "terminal_observations",
     yn_term.get("db_min") if yn_term else None,
     yn_term.get("db_max") if yn_term else None),
]:
    groups = _compute_calib(rows, per_orbit=per_orbit_flag)
    for g in groups:
        calib_rows.append({
            "terminal_id":    terminal_id,
            "source_table":   src_table,
            "orbit_group":    g["group"],
            "n_obs":          g["n_obs"],
            "db_p05_live":    g["db_p05"],
            "db_p95_live":    g["db_p95"],
            "db_mean_all":    g["db_mean"],
            "db_std_all":     g["db_std"],
            "override_db_min": override_min,
            "override_db_max": override_max,
            "model":          "inverse_crescent_radiometry",
            "tank_height_min_m": 2,
            "barrel_per_m3":     6.2898,
            "file_line":      "scripts/volume.py:41 (compute_volumes), scripts/config.py:TANK_HEIGHT_MIN,BARREL_PER_M3",
        })

path6 = os.path.join(OUT_DIR, "calibration_params.csv")
n6 = write_csv(path6, calib_rows)
print(f"  → {n6} rows")

# ── 7. anchors.csv ────────────────────────────────────────────────────────────
print("\n[7/8] anchors.csv")

anchor_rows = []
missing_anchors = []

# JODI Saudi crude production
jodi_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "external", "jodi_saudi_crude.csv"
)
if os.path.exists(jodi_path):
    df_jodi = pd.read_csv(jodi_path)
    print(f"  JODI Saudi: {len(df_jodi)} rows, cols: {list(df_jodi.columns)}")
    for _, row in df_jodi.iterrows():
        r = row.to_dict()
        r["source"] = "jodi_saudi_crude"
        r["terminal_hint"] = "ras_tanura,yanbu"
        anchor_rows.append(r)
else:
    missing_anchors.append("jodi_saudi_crude — expected at data/external/jodi_saudi_crude.csv")

# PortWatch ports: 1091 (RT), 526 (Juaymah), 570 (Yanbu), Kharg
portwatch_files = [
    ("port1091", "ras_tanura"),
    ("port526",  "juaymah"),
    ("port570",  "yanbu"),
    ("port_kharg", "kharg"),
]
for fname, hint in portwatch_files:
    pw_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "external", f"{fname}.csv"
    )
    if os.path.exists(pw_path):
        df_pw = pd.read_csv(pw_path)
        for _, row in df_pw.iterrows():
            r = row.to_dict()
            r["source"] = f"portwatch_{fname}"
            r["terminal_hint"] = hint
            anchor_rows.append(r)
        print(f"  PortWatch {fname}: {len(df_pw)} rows")
    else:
        missing_anchors.append(f"portwatch_{fname} — not found at data/external/{fname}.csv")

# UANI
uani_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "external", "uani.csv"
)
if os.path.exists(uani_path):
    df_uani = pd.read_csv(uani_path)
    for _, row in df_uani.iterrows():
        r = row.to_dict()
        r["source"] = "uani"
        anchor_rows.append(r)
    print(f"  UANI: {len(df_uani)} rows")
else:
    missing_anchors.append("uani — not found at data/external/uani.csv")

# rtju_mbpd
rtju_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "external", "rtju_mbpd.csv"
)
if os.path.exists(rtju_path):
    df_rtju = pd.read_csv(rtju_path)
    for _, row in df_rtju.iterrows():
        r = row.to_dict()
        r["source"] = "rtju_mbpd"
        anchor_rows.append(r)
    print(f"  rtju_mbpd: {len(df_rtju)} rows")
else:
    missing_anchors.append("rtju_mbpd — not found at data/external/rtju_mbpd.csv")

# JODI Iran
jodi_iran_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "external", "jodi_iran_crude.csv"
)
if os.path.exists(jodi_iran_path):
    df_ji = pd.read_csv(jodi_iran_path)
    for _, row in df_ji.iterrows():
        r = row.to_dict()
        r["source"] = "jodi_iran_crude"
        r["terminal_hint"] = "kharg"
        anchor_rows.append(r)
    print(f"  JODI Iran: {len(df_ji)} rows")
else:
    missing_anchors.append("jodi_iran_crude — not found at data/external/jodi_iran_crude.csv")

# Hormuz timeseries (already in data/)
hormuz_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "hormuz_timeseries.csv"
)
if os.path.exists(hormuz_path):
    df_horm = pd.read_csv(hormuz_path)
    for _, row in df_horm.iterrows():
        r = row.to_dict()
        r["source"] = "hormuz_timeseries_repo"
        r["terminal_hint"] = "kharg"
        anchor_rows.append(r)
    print(f"  Hormuz timeseries: {len(df_horm)} rows")
else:
    missing_anchors.append("hormuz_timeseries — not found at data/hormuz_timeseries.csv")

path7a = os.path.join(OUT_DIR, "anchors.csv")
path7b = os.path.join(OUT_DIR, "missing_anchors.txt")

n7 = 0
if anchor_rows:
    all_anchor_cols = set()
    for r in anchor_rows:
        all_anchor_cols.update(r.keys())
    std_anchor = ["source", "terminal_hint"]
    extra_anchor = sorted(all_anchor_cols - set(std_anchor))
    n7 = write_csv(path7a, anchor_rows, std_anchor + extra_anchor)
    print(f"  anchors.csv → {n7} rows from {len(set(r['source'] for r in anchor_rows))} sources")
else:
    with open(path7a, "w") as f:
        f.write("source,terminal_hint\n")
    print("  anchors.csv → empty (no anchor files found)")

with open(path7b, "w") as f:
    f.write(f"# Missing anchor files — export run {RUN_ID} SHA {SCRIPT_SHA}\n")
    for m in missing_anchors:
        f.write(m + "\n")
print(f"  missing_anchors.txt → {len(missing_anchors)} missing")

# ── 8. README.md ──────────────────────────────────────────────────────────────
print("\n[8/8] README.md")

def _fsize(path):
    try:
        return f"{os.path.getsize(path)/1024:.1f} KB"
    except:
        return "?"

readme_lines = [
    f"# event_csv — Retrospective Event Analysis Export",
    f"",
    f"**Export run**: {EXPORT_TS}  ",
    f"**SHA**: `{SCRIPT_SHA}`  ",
    f"**GHA Run ID**: `{RUN_ID}`  ",
    f"**Script**: `scripts/research/export_event_csv.py`",
    f"",
    f"Read-only export from Supabase + CDSE catalog. NO production writes.",
    f"",
    f"## Files",
    f"",
    f"| File | n rows | Period | Size | Notes |",
    f"|---|---|---|---|---|",
    f"| kharg_raw_observations.csv | {n1} | {dates1[0]}–{dates1[-1]} | {_fsize(path1)} | raw_observations; relOrbit/direction from CDSE date-match |",
    f"| rt_terminal_observations.csv | {n2} | {dates2[0] if dates2 else '?'}–{dates2[-1] if dates2 else '?'} | {_fsize(path2)} | orbit_cols={'yes' if has_rt_orbit_cols else 'no (fallback)'}; CDSE match appended |",
    f"| yanbu_terminal_observations.csv | {n3} | {dates3[0] if dates3 else '?'}–{dates3[-1] if dates3 else '?'} | {_fsize(path3)} | orbit_cols={'yes' if has_yn_orbit_cols else 'no (fallback)'}; CDSE match appended |",
    f"| registry_kharg_rt_yanbu.csv | {n4} | — | {_fsize(path4)} | Source: tank_registry.py; RT whitelist: diagnostics/stage3/whitelist_results.json |",
    f"| tvt_kharg_rt_yanbu.csv | {n5} | {dates5[0] if dates5 else '?'}–{dates5[-1] if dates5 else '?'} | {_fsize(path5)} | volume_timeseries (Kharg) + terminal_volume_timeseries (RT, Yanbu) |",
    f"| calibration_params.csv | {n6} | — | {_fsize(path6)} | Live percentiles from DB data; model: scripts/volume.py:41 |",
    f"| anchors.csv | {n7} | — | {_fsize(path7a)} | Available: {', '.join(sorted(set(r['source'] for r in anchor_rows))) if anchor_rows else 'none'} |",
    f"| missing_anchors.txt | — | — | {_fsize(path7b)} | Anchor files not found in repo |",
    f"",
    f"## Known Caveats",
    f"",
    f"1. **DESCENDING/NULL in terminal_observations** — orbit_direction column may have NULLs",
    f"   for rows ingested before the orbit column was added. Use `relative_orbit_cdse` for",
    f"   authoritative orbit assignment.",
    f"2. **Yanbu DESC-50/52 orphans** — ~40 dates in CDSE catalog have relOrbit 50 or 52",
    f"   (2014–2017); these are in terminal_observations but not in ASC-14 series.",
    f"   Flag: `orbit_direction_cdse=DESCENDING AND relative_orbit_cdse IN (50,52)`.",
    f"3. **TVT Yanbu orphans** — terminal_volume_timeseries rows for Yanbu may include",
    f"   volumes computed from mixed-orbit data (before per-orbit calibration was applied).",
    f"4. **CDSE date-match** — one CDSE scene per date is assigned by majority-vote on",
    f"   relative_orbit. Dates with multiple passes (rare) may have ambiguous assignment.",
    f"5. **Kharg raw_observations** has no relative_orbit column in DB; orbit info comes",
    f"   entirely from CDSE catalog match.",
    f"6. **Calibration params** — Kharg uses global percentiles (no orbit split);",
    f"   RT/Yanbu use per-orbit split when orbit_direction column exists.",
    f"",
    f"## SQL / Script Extracts",
    f"",
    f"```sql",
    f"-- raw_observations (Kharg)",
    f"SELECT date, tank_id, db_mean, created_at FROM raw_observations ORDER BY date;",
    f"",
    f"-- terminal_observations (RT)",
    f"SELECT date, tank_id, db_mean, orbit_direction, relative_orbit, created_at",
    f"FROM terminal_observations WHERE terminal_id = 'ras_tanura' ORDER BY date;",
    f"",
    f"-- terminal_observations (Yanbu)",
    f"SELECT date, tank_id, db_mean, orbit_direction, relative_orbit, created_at",
    f"FROM terminal_observations WHERE terminal_id = 'yanbu' ORDER BY date;",
    f"```",
]

readme_path = os.path.join(OUT_DIR, "README.md")
with open(readme_path, "w") as f:
    f.write("\n".join(readme_lines))
print(f"  → {readme_path}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"Export complete: {OUT_DIR}")
print(f"{'='*60}")
print(f"{'File':<40} {'Rows':>8} {'Period'}")
print(f"{'-'*60}")
for fname, n, period in [
    ("kharg_raw_observations.csv",      n1, f"{dates1[0]}–{dates1[-1]}"),
    ("rt_terminal_observations.csv",    n2, f"{dates2[0] if dates2 else '?'}–{dates2[-1] if dates2 else '?'}"),
    ("yanbu_terminal_observations.csv", n3, period3),
    ("registry_kharg_rt_yanbu.csv",     n4, "—"),
    ("tvt_kharg_rt_yanbu.csv",          n5, f"{dates5[0] if dates5 else '?'}–{dates5[-1] if dates5 else '?'}"),
    ("calibration_params.csv",          n6, "—"),
    ("anchors.csv",                     n7, "—"),
]:
    print(f"  {fname:<38} {n:>8}   {period}")
print(f"\nMissing anchors ({len(missing_anchors)}):")
for m in missing_anchors:
    print(f"  - {m}")
