"""
TZ-11 — correlation of Iranian upstream terminals with Kharg.
READ-ONLY: only SELECT queries against Supabase. Writes CSV/PNG to analysis/tz11/.

Index per terminal x orbit x date:
  z = (db_mean - mean_tank_orbit) / std_tank_orbit   (full history of tank on that orbit)
  idx = sum(w*z)/sum(w),  w = radius_m^2 * h_max
Weekly: ISO-week mean of idx. Diffs: week-to-week on a continuous weekly grid
(diff across a missing week = NaN).
"""
import os, sys, math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from supabase import create_client

sys.path.insert(0, os.path.dirname(__file__))
from tank_registry import LOCATIONS

OUT = os.path.join(os.path.dirname(__file__), "..", "analysis", "tz11")
os.makedirs(OUT, exist_ok=True)
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

TERMINALS = [  # (label, wide-prefix, candidate terminal_ids in priority order)
    ("kharg",         "kharg",      ["kharg"]),
    ("goreh_omid",    "goreh_omid", ["goreh_omid", "goreh"]),
    ("jask",          "jask",       ["jask_v2", "jask"]),
    ("bahregan",      "bahregan",   ["bahregan", "bahregan_v2"]),
    ("ras_tanura_v2", "rt_v2",      ["ras_tanura_v2"]),
]
WIDE_COLS = ["kharg_35", "kharg_101", "goreh_omid_35", "goreh_omid_101",
             "jask_57", "jask_93", "bahregan_35", "bahregan_101", "rt_v2_35", "rt_v2_101"]
TABLES = ["terminal_observations", "raw_observations"]

REG = {}
for loc in LOCATIONS:
    for t in loc.get("tanks", []):
        REG[t["id"]] = (t.get("radius_m"), t.get("h_max_m"))


def first(cols, cands):
    for c in cands:
        if c in cols:
            return c
    return None


def probe_cols(table):
    try:
        r = sb.table(table).select("*").limit(1).execute().data
        return list(r[0].keys()) if r else []
    except Exception as e:
        print(f"  [probe] {table}: ERROR {e}")
        return []


def count(table, tid):
    try:
        r = sb.table(table).select("tank_id", count="exact").eq("terminal_id", tid).limit(1).execute()
        return r.count or 0
    except Exception as e:
        print(f"  [count] {table}/{tid}: ERROR {str(e)[:120]}")
        return 0


def fetch(table, tid):
    rows, off = [], 0
    while True:
        b = (sb.table(table).select("*").eq("terminal_id", tid)
             .order("date").order("tank_id").range(off, off + 999).execute().data)
        rows.extend(b)
        if len(b) < 1000:
            break
        off += 1000
    return pd.DataFrame(rows)


print("=" * 70)
print("SCHEMA PROBE")
COLS = {t: probe_cols(t) for t in TABLES}
for t, c in COLS.items():
    print(f"  {t}: {c}")

summary_lines = []
series = {}   # col name -> pd.Series (index: date) daily index

for label, prefix, cands in TERMINALS:
    print("=" * 70)
    print(f"TERMINAL {label}")
    best = None
    for table in TABLES:
        if "terminal_id" not in COLS[table]:
            continue
        for tid in cands:
            n = count(table, tid)
            print(f"  candidate {table}.terminal_id={tid}: rows={n}")
            if n and best is None:
                best = (table, tid)
        if best:
            break
    if best is None:
        line = f"{label}: EMPTY (no rows for {cands} in {TABLES})"
        print("  " + line)
        summary_lines.append(line)
        continue

    table, tid = best
    df = fetch(table, tid)
    cols = list(df.columns)
    c_orb = first(cols, ["orbit", "relative_orbit", "rel_orbit", "orbit_id", "track"])
    c_rad = first(cols, ["radius_m", "radius"])
    c_h = first(cols, ["h_max", "h_max_m"])
    c_pres = first(cols, ["tank_present_guess"])
    print(f"  source={table}/{tid}  rows={len(df)}  orbit_col={c_orb} radius_col={c_rad} "
          f"h_max_col={c_h} tank_present_guess={c_pres}")

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["orbit"] = pd.to_numeric(df[c_orb], errors="coerce").astype("Int64") if c_orb else pd.NA
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    reg_r = df["tank_id"].map(lambda k: REG.get(k, (None, None))[0])
    reg_h = df["tank_id"].map(lambda k: REG.get(k, (None, None))[1])
    df["radius_m"] = pd.to_numeric(df[c_rad], errors="coerce") if c_rad else np.nan
    df["h_max"] = pd.to_numeric(df[c_h], errors="coerce") if c_h else np.nan
    n_reg_r = int(df["radius_m"].isna().sum()); n_reg_h = int(df["h_max"].isna().sum())
    df["radius_m"] = df["radius_m"].fillna(pd.to_numeric(reg_r, errors="coerce"))
    df["h_max"] = df["h_max"].fillna(pd.to_numeric(reg_h, errors="coerce"))
    print(f"  radius_m filled from tank_registry: {n_reg_r} rows; h_max filled from registry: {n_reg_h} rows")
    print(f"  rows still without weight: {int((df['radius_m'].isna() | df['h_max'].isna()).sum())}")

    keep = ["terminal_id", "date", "orbit", "tank_id", "db_mean", "radius_m", "h_max"]
    if c_pres:
        df["tank_present_guess"] = pd.to_numeric(df[c_pres], errors="coerce")
        keep.append("tank_present_guess")
    df["terminal_id"] = tid
    df[keep].sort_values(["date", "orbit", "tank_id"]).to_csv(
        os.path.join(OUT, f"pertank_{label}.csv"), index=False)

    nn = df["db_mean"].notna().mean() if len(df) else float("nan")
    line = (f"{label} [{table}/{tid}]: dates={df['date'].nunique()} "
            f"period={df['date'].min().date()}..{df['date'].max().date()} "
            f"tanks={df['tank_id'].nunique()} rows={len(df)} db_mean_nonnull={nn:.4f}")
    print("  " + line)
    summary_lines.append(line)
    for orb, g in df.groupby("orbit", dropna=False):
        l2 = (f"    orbit={orb}: dates={g['date'].nunique()} "
              f"period={g['date'].min().date()}..{g['date'].max().date()} "
              f"tanks={g['tank_id'].nunique()} rows={len(g)} db_mean_nonnull={g['db_mean'].notna().mean():.4f}")
        print(l2)
        summary_lines.append(l2)

    # ── index ────────────────────────────────────────────────────────────
    d = df[df["db_mean"].notna()].copy()
    if c_pres:
        before = len(d)
        d = d[~(d["tank_present_guess"] == 0)]
        l3 = f"    tank_present_guess==0 excluded: {before - len(d)} rows"
        print(l3); summary_lines.append(l3)
    elif label == "jask":
        l3 = "    tank_present_guess column: ABSENT (no exclusion applied)"
        print(l3); summary_lines.append(l3)
    d = d[d["radius_m"].notna() & d["h_max"].notna()]
    g = d.groupby(["tank_id", "orbit"])["db_mean"]
    d["mu"] = g.transform("mean"); d["sd"] = g.transform("std")
    d = d[d["sd"] > 0]
    d["z"] = (d["db_mean"] - d["mu"]) / d["sd"]
    d["w"] = d["radius_m"] ** 2 * d["h_max"]
    d["wz"] = d["w"] * d["z"]
    agg = d.groupby(["orbit", "date"]).agg(wz=("wz", "sum"), w=("w", "sum"), n_tanks=("tank_id", "nunique"))
    agg["idx"] = agg["wz"] / agg["w"]
    agg = agg.reset_index()
    agg.insert(0, "terminal", label)
    agg[["terminal", "orbit", "date", "idx", "n_tanks"]].to_csv(
        os.path.join(OUT, f"daily_index_{label}.csv"), index=False)
    for orb, a in agg.groupby("orbit"):
        name = f"{prefix}_{orb}"
        series[name] = a.set_index("date")["idx"]
        print(f"    index {name}: {len(a)} dates")

# ── weekly ───────────────────────────────────────────────────────────────
print("=" * 70)
print("WEEKLY")
wk = {}
for name, s in series.items():
    monday = pd.to_datetime(s.index) - pd.to_timedelta(s.index.weekday, unit="D")
    wk[name] = s.groupby(monday).mean()
all_cols = WIDE_COLS + sorted(c for c in wk if c not in WIDE_COLS)
if wk:
    start = min(v.index.min() for v in wk.values()); end = max(v.index.max() for v in wk.values())
    grid = pd.date_range(start, end, freq="W-MON")
else:
    grid = pd.DatetimeIndex([])
W = pd.DataFrame(index=grid)
for c in all_cols:
    W[c] = wk[c].reindex(grid) if c in wk else np.nan
iso = grid.isocalendar()
weeklab = [f"{y}-W{w:02d}" for y, w in zip(iso["year"], iso["week"])]
out = W.copy()
out.insert(0, "week_start", grid.date)
out.insert(0, "week", weeklab)
out.to_csv(os.path.join(OUT, "weekly_levels.csv"), index=False, float_format="%.5f")
for c in all_cols:
    print(f"  {c}: non-empty weeks={int(W[c].notna().sum())}")


def corr_mat(df):
    cols = list(df.columns)
    rm = pd.DataFrame(index=cols, columns=cols, dtype=object)
    long = []
    for a in cols:
        for b in cols:
            m = df[a].notna() & df[b].notna()
            n = int(m.sum())
            r = float(np.corrcoef(df.loc[m, a], df.loc[m, b])[0, 1]) if n >= 3 and \
                df.loc[m, a].std() > 0 and df.loc[m, b].std() > 0 else float("nan")
            rm.loc[a, b] = f"r={r:.3f}; n={n}" if not math.isnan(r) else f"r=NA; n={n}"
            long.append({"a": a, "b": b, "r": r, "n": n})
    return rm, pd.DataFrame(long)


D = W.diff()  # NaN across gaps because grid is continuous
D26 = D[D.index.year == 2026]  # diff for first 2026 week uses last 2025 week (adjacent)
for tag, lv, df_ in [("corr_levels", W, None), ("corr_diffs", D, None),
                     ("corr_2026_levels", W[W.index.year == 2026], None),
                     ("corr_2026_diffs", D26, None)]:
    rm, lg = corr_mat(lv)
    rm.to_csv(os.path.join(OUT, f"{tag}.csv"))
    lg.to_csv(os.path.join(OUT, f"{tag}_long.csv"), index=False, float_format="%.5f")
    print("=" * 70)
    print(tag)
    print(rm.loc[[c for c in all_cols if c.startswith("kharg")]].T.to_string())

# ── plot ─────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(15, 7), dpi=150)
for c in all_cols:
    s = W[c].dropna()
    if len(s) < 2:
        continue
    zn = (s - s.mean()) / s.std()
    ax.plot(zn.index, zn.values, marker=".", ms=3, lw=1.6 if c.startswith("kharg") else 1.0,
            label=f"{c} (n={len(s)})")
ax.axhline(0, color="grey", lw=0.5)
ax.set_title("TZ-11: weekly terminal level index, each series z-normalised")
ax.set_ylabel("z (per series)"); ax.legend(ncol=2, fontsize=8); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "weekly_overlay.png")); plt.close(fig)

with open(os.path.join(OUT, "summary.txt"), "w") as f:
    f.write("\n".join(summary_lines) + "\n")
    f.write("\nnon-empty weeks per column:\n")
    for c in all_cols:
        f.write(f"  {c}: {int(W[c].notna().sum())}\n")
print("DONE")
