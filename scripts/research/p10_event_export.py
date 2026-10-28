"""
p10_event_export.py — ТЗ-4A: raw export for event analysis (Kharg + Ras Tanura).

No new models. Values come from:
  DB (Supabase, live):  raw_observations (kharg), terminal_observations (ras_tanura),
                        volume_timeseries (kharg daily), terminal_volume_timeseries (RT daily)
  CDSE OData catalog:   every S1 IW GRDH acquisition over each terminal's tank bbox
                        (scene_id, datetime_utc, rel_orbit, direction, platform)
  Section-4 variants:   origin/research/2026-10-auto diagnostics/mega/tank_scene_variants_*.csv.gz
                        (ring15_far, ring15_near, pixel_valid_frac), exact join on
                        (terminal, tank, date, rel_orbit, direction)
  Registry:             scripts/tank_registry.py (main)
  Per-tank barrels:     scripts/volume.compute_volumes — the prod formula, run on the full
                        DB history of the terminal exactly as main_script.py does.

Outputs (diagnostics/event_export/):
  kharg_daily.csv, ras_tanura_daily.csv (DESC-35 only), ras_tanura_asc_daily.csv
  kharg_tank_daily.csv, rt_tank_daily.csv, rt_asc_tank_daily.csv
  tanks_reference.csv, scenes_catalog.csv, summary.txt
"""

import csv, gzip, io, os, subprocess, sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape
from shapely import wkt as shapely_wkt

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from tank_registry import LOCATIONS  # noqa: E402

OUT = REPO / "diagnostics" / "event_export"
OUT.mkdir(parents=True, exist_ok=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
RESEARCH = "origin/research/2026-10-auto"
MEGA = "diagnostics/mega"

BARREL_PER_M3 = 6.2898
TANK_HEIGHT_MIN = 2.0

TERMINALS = {
    "kharg":      {"obs_table": "raw_observations", "tank_prefix": "kharg", "rt": False},
    "ras_tanura": {"obs_table": "terminal_observations", "tank_prefix": "rt", "rt": True},
}

_summary = open(OUT / "summary.txt", "w", buffering=1)


def log(msg=""):
    print(msg, flush=True)
    _summary.write(msg + "\n")


def norm_dir(d):
    d = (str(d or "")).strip().upper()
    if d.startswith("D"):
        return "DESCENDING"
    if d.startswith("A"):
        return "ASCENDING"
    return ""


def norm_orb(o):
    try:
        return int(float(o))
    except (TypeError, ValueError):
        return None


# ── Supabase ────────────────────────────────────────────────────────────────

def sb_fetch_all(table, filters=None):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    rows, offset, page = [], 0, 1000
    while True:
        params = {"select": "*", "limit": page, "offset": offset, "order": "id"}
        for k, v in (filters or {}).items():
            params[k] = f"eq.{v}"
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers,
                         params=params, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return pd.DataFrame(rows)


# ── CDSE catalog ────────────────────────────────────────────────────────────

def tank_bbox_wkt(tanks, buf_deg=0.005):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    x0, x1 = min(lons) - buf_deg, max(lons) + buf_deg
    y0, y1 = min(lats) - buf_deg, max(lats) + buf_deg
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def cdse_catalog(terminal, tanks):
    wkt = tank_bbox_wkt(tanks)
    pts = [Point(t["lon"], t["lat"]) for t in tanks]
    rows = []
    for y in range(2014, datetime.now(timezone.utc).year + 1):
        filt = (f"Collection/Name eq 'SENTINEL-1' and contains(Name,'IW_GRDH') "
                f"and ContentDate/Start ge {y}-01-01T00:00:00.000Z "
                f"and ContentDate/Start lt {y + 1}-01-01T00:00:00.000Z "
                f"and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}')")
        skip, n_year = 0, 0
        while True:
            for attempt in range(4):
                try:
                    r = requests.get(ODATA, params={
                        "$filter": filt, "$top": 1000, "$skip": skip,
                        "$expand": "Attributes", "$orderby": "ContentDate/Start asc"},
                        timeout=120)
                    r.raise_for_status()
                    break
                except Exception as e:
                    if attempt == 3:
                        raise
                    print(f"  retry {terminal} {y} skip={skip}: {e}", flush=True)
            items = r.json().get("value", [])
            for it in items:
                a = {x["Name"]: x.get("Value") for x in it.get("Attributes", [])}
                geom = None
                if it.get("GeoFootprint"):
                    geom = shape(it["GeoFootprint"])
                elif it.get("Footprint"):
                    geom = shapely_wkt.loads(it["Footprint"].split(";")[-1].rstrip("'"))
                n_in = sum(geom.covers(p) for p in pts) if geom is not None else np.nan
                start = it.get("ContentDate", {}).get("Start", "")
                rows.append({
                    "terminal": terminal,
                    "date": start[:10],
                    "datetime_utc": start,
                    "scene_id": it.get("Name", ""),
                    "rel_orbit": norm_orb(a.get("relativeOrbitNumber")),
                    "direction": norm_dir(a.get("orbitDirection")),
                    "platform": f"{a.get('platformShortName', '')}{a.get('platformSerialIdentifier', '')}",
                    "n_tanks_in_footprint": n_in,
                })
            n_year += len(items)
            if len(items) < 1000:
                break
            skip += 1000
        print(f"  CDSE {terminal} {y}: {n_year} products", flush=True)

    cat = pd.DataFrame(rows)
    if cat.empty:
        return cat
    cat["rel_orbit"] = cat["rel_orbit"].astype("Int64")
    # One acquisition can exist as both .SAFE and _COG.SAFE (and reprocessed copies):
    # key = mission_mode_type_start_stop_absorbit_datatake; prefer the COG name.
    cat["acq_key"] = cat["scene_id"].str.replace("_COG", "", regex=False).str.split("_").str[:9].str.join("_")
    cat["_cog"] = cat["scene_id"].str.contains("_COG")
    n0 = len(cat)
    cat = (cat.sort_values(["acq_key", "_cog"], ascending=[True, False])
              .drop_duplicates("acq_key").drop(columns=["_cog"]))
    print(f"  CDSE {terminal}: {n0} products → {len(cat)} unique acquisitions", flush=True)
    return cat.sort_values("datetime_utc").reset_index(drop=True)


# ── Section-4 ring15 variants ───────────────────────────────────────────────

def load_s4(terminal):
    r = subprocess.run(["git", "ls-tree", "--name-only", f"{RESEARCH}:{MEGA}/"],
                       capture_output=True, text=True, cwd=REPO)
    names = [n for n in r.stdout.split()
             if n.startswith(f"tank_scene_variants_{terminal}") and n.endswith(".csv.gz")]
    frames = []
    for n in sorted(names):
        b = subprocess.run(["git", "show", f"{RESEARCH}:{MEGA}/{n}"], capture_output=True, cwd=REPO).stdout
        try:
            df = pd.read_csv(io.BytesIO(gzip.decompress(b)),
                             usecols=["terminal", "tank_id", "date", "relOrbit", "direction",
                                      "ring15_far", "ring15_near", "pixel_valid_frac"])
        except Exception as e:
            print(f"  s4 skip {n}: {e}", flush=True)
            continue
        frames.append(df)
        print(f"  s4 {n}: {len(df)} rows", flush=True)
    if not frames:
        return pd.DataFrame(columns=["tank_id", "date", "rel_orbit", "orbit_direction",
                                     "ring15_far_dB", "ring15_near_dB", "pixel_valid_frac"])
    s4 = pd.concat(frames, ignore_index=True)
    s4["rel_orbit"] = s4["relOrbit"].map(norm_orb).astype("Int64")
    s4["orbit_direction"] = s4["direction"].map(norm_dir)
    s4["date"] = s4["date"].astype(str).str[:10]
    s4 = s4.drop_duplicates(["tank_id", "date", "rel_orbit", "orbit_direction"])
    return s4.rename(columns={"ring15_far": "ring15_far_dB", "ring15_near": "ring15_near_dB"})[
        ["tank_id", "date", "rel_orbit", "orbit_direction",
         "ring15_far_dB", "ring15_near_dB", "pixel_valid_frac"]]


# ── Prod volume formula (scripts/volume.py, global p5/p95 per terminal) ─────

def prod_barrels(obs, reg_tanks, loc):
    """Per-row barrels with the prod formula over the full DB history of the terminal.

    Calibration p5/p95 is taken over enabled tanks only (all orbits pooled) — this is what
    reproduces the DB vol_mn_bbl (checked on the 2026-10-26 snapshot: median |Δ| 0.008 Mn bbl
    for RT, 0.009 for Kharg). Disabled tanks get barrels with the same calibration.
    """
    en = {t["id"]: bool(t.get("enabled", True)) for t in reg_tanks}
    rad = {t["id"]: t["radius_m"] for t in reg_tanks}
    hmx = {t["id"]: t["h_max_m"] for t in reg_tanks}
    r_m = pd.to_numeric(obs.get("radius_m"), errors="coerce") if "radius_m" in obs else pd.Series(np.nan, index=obs.index)
    h_m = pd.to_numeric(obs.get("h_max"), errors="coerce") if "h_max" in obs else pd.Series(np.nan, index=obs.index)
    r_m = r_m.fillna(obs["tank_id"].map(rad))
    h_m = h_m.fillna(obs["tank_id"].map(hmx))
    db = obs["db_mean"]
    db_en = db[obs["tank_id"].map(en)]
    lo = loc.get("db_min") if loc.get("db_min") is not None else db_en.quantile(0.05)
    hi = loc.get("db_max") if loc.get("db_max") is not None else db_en.quantile(0.95)
    vv = np.clip(db, lo, hi)
    fr = (hi - vv) / (hi - lo)
    h = TANK_HEIGHT_MIN + fr * (h_m - TANK_HEIGHT_MIN)
    barrels = np.pi * r_m ** 2 * h * BARREL_PER_M3
    clipped = (db <= lo) | (db >= hi)
    return barrels, clipped, float(lo), float(hi)


# ── Main assembly per terminal ──────────────────────────────────────────────

def build_terminal(tid, cfg, loc, cat, vol_db):
    tanks = loc["tanks"]
    reg = pd.DataFrame([{
        "tank_id": t["id"], "r": t["radius_m"], "h": t["h_max_m"],
        "enabled": bool(t.get("enabled", True)),
        "cap_bbl": np.pi * t["radius_m"] ** 2 * t["h_max_m"] * BARREL_PER_M3,
    } for t in tanks])
    total_cap = reg["cap_bbl"].sum()

    log(f"\n── {tid}: DB {cfg['obs_table']}")
    obs = sb_fetch_all(cfg["obs_table"], {"terminal_id": tid} if cfg["rt"] else None)
    if "terminal_id" in obs.columns and not cfg["rt"]:
        obs = obs[obs["terminal_id"].fillna(tid) == tid]
    log(f"  rows={len(obs)} columns={list(obs.columns)}")
    obs["date"] = obs["date"].astype(str).str[:10]
    obs["db_mean"] = pd.to_numeric(obs["db_mean"], errors="coerce")
    obs = obs[obs["tank_id"].isin(reg["tank_id"])].copy()
    obs["db_rel_orbit"] = obs["relative_orbit"].map(norm_orb) if "relative_orbit" in obs else None
    obs["db_direction"] = obs["orbit_direction"].map(norm_dir) if "orbit_direction" in obs else ""
    obs["barrels"], obs["clipped"], lo, hi = prod_barrels(obs, tanks, loc)
    log(f"  prod calibration (p5/p95, enabled tanks, full DB history, all orbits): dB_min={lo:.4f} dB_max={hi:.4f}")

    # ── assign every DB (date, orbit-tag) group to a catalog acquisition ────
    cat_t = cat.copy()
    # per (date, rel_orbit, direction) keep the acquisition covering most tanks
    cat_t = (cat_t.sort_values(["date", "rel_orbit", "direction", "n_tanks_in_footprint", "datetime_utc"],
                               ascending=[True, True, True, False, True])
                  .drop_duplicates(["date", "rel_orbit", "direction"]))
    by_date = {d: g for d, g in cat_t.groupby("date")}
    default_dir = "DESCENDING" if not cfg["rt"] else None  # Kharg prod pipeline = descending only

    groups = obs.groupby(["date", obs["db_rel_orbit"].fillna(-1), obs["db_direction"].fillna("")])
    assign = {}
    for (d, o, dr), _ in groups:
        o = None if o == -1 else int(o)
        cand = by_date.get(d)
        status = "no_catalog_scene"
        pick = None
        if cand is not None:
            c = cand
            if o is not None:
                c = c[c["rel_orbit"] == o]
            if dr:
                c = c[c["direction"] == dr]
            elif default_dir:
                c = c[c["direction"] == default_dir]
            if len(c) == 1:
                pick, status = c.iloc[0], ("exact" if o is not None else "date_dir")
            elif len(c) > 1:
                c35 = c[(c["rel_orbit"] == 35) & (c["direction"] == "DESCENDING")]
                pick = (c35 if len(c35) else c).iloc[0]
                status = "ambiguous"
        key = (pick["rel_orbit"], pick["direction"]) if pick is not None else (o, dr or "")
        assign[(d, -1 if o is None else o, dr)] = (key, pick, status)

    obs["k_orbit"] = [assign[(d, -1 if pd.isna(o) else int(o), dr)][0][0]
                      for d, o, dr in zip(obs["date"], obs["db_rel_orbit"], obs["db_direction"].fillna(""))]
    obs["k_dir"] = [assign[(d, -1 if pd.isna(o) else int(o), dr)][0][1]
                    for d, o, dr in zip(obs["date"], obs["db_rel_orbit"], obs["db_direction"].fillna(""))]
    obs["db_match"] = [assign[(d, -1 if pd.isna(o) else int(o), dr)][2]
                       for d, o, dr in zip(obs["date"], obs["db_rel_orbit"], obs["db_direction"].fillna(""))]
    obs["k_orbit"] = obs["k_orbit"].map(norm_orb).astype("Int64")
    obs["k_dir"] = obs["k_dir"].fillna("")

    # mean per (date, orbit, tank) — aggregate_daily semantics
    obs = obs.merge(reg[["tank_id", "r", "enabled", "cap_bbl"]], on="tank_id", how="left")
    tank_obs = (obs.groupby(["date", "k_orbit", "k_dir", "tank_id"], dropna=False)
                   .agg(db_mean=("db_mean", "mean"), barrels=("barrels", "mean"),
                        clipped=("clipped", "max"), db_match=("db_match", "first"))
                   .reset_index())

    # ── daily keys: catalog acquisitions of DB orbits + DB groups w/o catalog scene ──
    db_orbits = set(zip(tank_obs["k_orbit"], tank_obs["k_dir"]))
    db_orbits = {(o, d) for o, d in db_orbits if o is not None and not pd.isna(o)}
    keys = cat_t[[ (o, d) in db_orbits for o, d in zip(cat_t["rel_orbit"], cat_t["direction"])]][
        ["date", "rel_orbit", "direction", "datetime_utc", "scene_id", "platform"]].copy()
    keys = keys.rename(columns={"direction": "orbit_direction"})
    keys["in_catalog"] = True
    extra = (tank_obs[["date", "k_orbit", "k_dir"]].drop_duplicates()
             .rename(columns={"k_orbit": "rel_orbit", "k_dir": "orbit_direction"}))
    extra = extra.merge(keys[["date", "rel_orbit", "orbit_direction"]], how="left", indicator=True)
    extra = extra[extra["_merge"] == "left_only"].drop(columns="_merge")
    extra["in_catalog"] = False
    keys = pd.concat([keys, extra], ignore_index=True)

    # ── daily aggregates ─────────────────────────────────────────────────────
    s4 = load_s4(tid)
    to = tank_obs.rename(columns={"k_orbit": "rel_orbit", "k_dir": "orbit_direction"})
    to = to.merge(reg[["tank_id", "r", "enabled", "cap_bbl"]], on="tank_id", how="left")
    to = to.merge(s4, on=["tank_id", "date", "rel_orbit", "orbit_direction"], how="left")
    ok = to[to["db_mean"].notna()]
    agg = ok.groupby(["date", "rel_orbit", "orbit_direction"], dropna=False).apply(lambda g: pd.Series({
        "n_tanks": len(g),
        "coverage_capacity_share": g["cap_bbl"].sum() / total_cap,
        "vol_mn_bbl_enabled_only": g.loc[g["enabled"], "barrels"].sum() / 1e6,
        "vol_mn_bbl_r60_only": g.loc[g["r"] == 60, "barrels"].sum() / 1e6,
        "mean_db": g["db_mean"].mean(),
        "median_db": g["db_mean"].median(),
        "clip_frac": g["clipped"].mean(),
        "pixel_valid_frac_median": g["pixel_valid_frac"].median(),
        "db_match": g["db_match"].iloc[0],
    }), include_groups=False).reset_index()

    daily = keys.merge(agg, on=["date", "rel_orbit", "orbit_direction"], how="left")
    # DB vol_mn_bbl is stored per date (no orbit); attach it only where the DB has tank rows
    daily = daily.merge(vol_db, on="date", how="left")
    daily.loc[daily["n_tanks"].isna(), "vol_mn_bbl"] = np.nan
    daily["n_tanks"] = daily["n_tanks"].fillna(0).astype(int)
    daily["n_tanks_expected"] = len(reg)
    daily["db_match"] = daily["db_match"].fillna("not_in_db")
    daily = daily.sort_values(["date", "rel_orbit"]).reset_index(drop=True)

    cols = ["date", "datetime_utc", "scene_id", "rel_orbit", "orbit_direction", "platform",
            "n_tanks", "n_tanks_expected", "coverage_capacity_share",
            "vol_mn_bbl", "vol_mn_bbl_enabled_only", "vol_mn_bbl_r60_only",
            "mean_db", "median_db", "clip_frac", "pixel_valid_frac_median",
            "db_match", "in_catalog"]
    daily = daily[cols]

    # consistency check: prod-formula sum vs DB vol_mn_bbl
    chk = daily.dropna(subset=["vol_mn_bbl", "vol_mn_bbl_enabled_only"])
    if len(chk):
        d = (chk["vol_mn_bbl_enabled_only"] - chk["vol_mn_bbl"]).abs()
        log(f"  check Σbarrels(enabled, prod formula) vs DB vol_mn_bbl: n={len(chk)} "
            f"median|Δ|={d.median():.3f} p95|Δ|={d.quantile(.95):.3f} max|Δ|={d.max():.3f} Mn bbl")

    # ── per-tank grid: every daily row × every registry tank ─────────────────
    grid = daily[["date", "datetime_utc", "scene_id", "rel_orbit", "orbit_direction"]].merge(
        reg[["tank_id", "enabled"]], how="cross")
    tank = grid.merge(to[["date", "rel_orbit", "orbit_direction", "tank_id", "db_mean",
                          "ring15_far_dB", "ring15_near_dB", "barrels", "pixel_valid_frac"]],
                      on=["date", "rel_orbit", "orbit_direction", "tank_id"], how="left")
    # ring15 also for dates the DB lacks (section-4 measured them from the same scene)
    miss = tank["ring15_far_dB"].isna()
    if miss.any():
        fill = tank.loc[miss, ["date", "rel_orbit", "orbit_direction", "tank_id"]].merge(
            s4, on=["tank_id", "date", "rel_orbit", "orbit_direction"], how="left")
        for c in ["ring15_far_dB", "ring15_near_dB", "pixel_valid_frac"]:
            tank.loc[miss, c] = fill[c].values
    tank = tank[["date", "datetime_utc", "scene_id", "rel_orbit", "orbit_direction", "tank_id",
                 "db_mean", "ring15_far_dB", "ring15_near_dB", "barrels", "pixel_valid_frac", "enabled"]]
    return daily, tank


def describe(name, df):
    d = df["date"].dropna()
    log(f"  {name:<28} rows={len(df):>7}  period={d.min()} … {d.max()}")


def main():
    log(f"p10_event_export  run {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}")
    locs = {l["id"]: l for l in LOCATIONS}

    # tanks_reference
    ref = pd.DataFrame([{
        "tank_id": t["id"], "terminal": tid, "r": t["radius_m"], "h": t["h_max_m"],
        "lat": t["lat"], "lon": t["lon"], "enabled": bool(t.get("enabled", True)),
        "geom_source": t.get("geom_source", ""),
    } for tid in TERMINALS for t in locs[tid]["tanks"]])

    log("\n── Volume tables")
    vk = sb_fetch_all("volume_timeseries")
    vk = vk[(vk["aggregation"] == "daily") & (vk["smoothed"].astype(str).str.lower() == "false")]
    vr = sb_fetch_all("terminal_volume_timeseries", {"terminal_id": "ras_tanura"})
    vr = vr[(vr["aggregation"] == "daily") & (vr["smoothed"].astype(str).str.lower() == "false")]
    vols = {}
    for tid, v in [("kharg", vk), ("ras_tanura", vr)]:
        v = v.assign(date=v["date"].astype(str).str[:10])
        v = v.sort_values("id").drop_duplicates("date", keep="last")
        vols[tid] = v[["date", "vol_mn_bbl"]].assign(vol_mn_bbl=lambda x: pd.to_numeric(x["vol_mn_bbl"]))
        log(f"  {tid}: {len(vols[tid])} daily DB volume rows")

    log("\n── CDSE catalog")
    cats = {tid: cdse_catalog(tid, locs[tid]["tanks"]) for tid in TERMINALS}

    res = {tid: build_terminal(tid, TERMINALS[tid], locs[tid], cats[tid], vols[tid]) for tid in TERMINALS}

    # scenes_catalog with in_db flag (DB has tank rows matched to this acquisition)
    sc_parts = []
    for tid in TERMINALS:
        c = cats[tid].copy()
        daily = res[tid][0]
        in_db = set(daily.loc[daily["n_tanks"] > 0, "scene_id"].dropna())
        c["in_db"] = c["scene_id"].isin(in_db)
        sc_parts.append(c)
    scenes = pd.concat(sc_parts, ignore_index=True)[
        ["terminal", "date", "datetime_utc", "scene_id", "rel_orbit", "direction", "platform",
         "in_db", "n_tanks_in_footprint"]]

    kd, kt = res["kharg"]
    rd, rt = res["ras_tanura"]
    is35 = lambda df: (df["rel_orbit"] == 35) & (df["orbit_direction"] == "DESCENDING")
    isasc = lambda df: df["orbit_direction"] == "ASCENDING"
    files = {
        "kharg_daily.csv": kd,
        "ras_tanura_daily.csv": rd[is35(rd)],
        "ras_tanura_asc_daily.csv": rd[isasc(rd)],
        "kharg_tank_daily.csv": kt,
        "rt_tank_daily.csv": rt[is35(rt)],
        "rt_asc_tank_daily.csv": rt[isasc(rt)],
        "tanks_reference.csv": ref,
        "scenes_catalog.csv": scenes,
    }

    log("\n══ PRE-DELIVERY CHECKS ══")
    log("\n1) Rows and period per file")
    empty = []
    for name, df in files.items():
        if name == "tanks_reference.csv":
            log(f"  {name:<28} rows={len(df):>7}  " +
                ", ".join(f"{k}={v}" for k, v in df.groupby('terminal').size().items()))
        else:
            describe(name, df)
        if len(df) == 0:
            empty.append(name)

    log("\n2) Non-empty vol_mn_bbl share by terminal × rel_orbit (daily rows)")
    for tid, d in [("kharg", kd), ("ras_tanura", rd)]:
        for (o, dr), g in d.groupby(["rel_orbit", "orbit_direction"], dropna=False):
            log(f"  {tid:<11} orbit={o!s:>5} {dr!s:<11} rows={len(g):>5}  "
                f"vol non-null={g['vol_mn_bbl'].notna().sum():>5} ({g['vol_mn_bbl'].notna().mean():.3f})")
        o_other = d[~is35(d) & ~isasc(d)]
        if tid == "ras_tanura" and len(o_other):
            log(f"  NOTE: {len(o_other)} RT rows on other DESC orbits "
                f"{sorted(set(o_other['rel_orbit'].dropna()))} are in neither RT daily file")

    log("\n3) Dates: CDSE catalog vs DB")
    for tid, d in [("kharg", kd), ("ras_tanura", rd)]:
        c = cats[tid]
        db_dates = set(d.loc[d["n_tanks"] > 0, "date"])
        cat_dates = set(c["date"])
        log(f"  {tid}: catalog dates (all orbits)={len(cat_dates)}  DB dates={len(db_dates)}  "
            f"DB∩catalog={len(db_dates & cat_dates)}  DB-only={len(db_dates - cat_dates)}")
        for (o, dr), g in c.groupby(["rel_orbit", "direction"]):
            dd = d[(d["rel_orbit"] == o) & (d["orbit_direction"] == dr) & (d["n_tanks"] > 0)]
            log(f"     orbit {o:>3} {dr:<11} catalog dates={g['date'].nunique():>4}  in DB={dd['date'].nunique():>4}")
        log(f"     db_match: {d.loc[d['n_tanks'] > 0, 'db_match'].value_counts().to_dict()}")

    if empty:
        log(f"\nFATAL: empty files {empty} — nothing written")
        sys.exit(1)
    for name, df in files.items():
        df.to_csv(OUT / name, index=False)
    log(f"\nWrote {len(files)} files to {OUT}")


if __name__ == "__main__":
    main()
