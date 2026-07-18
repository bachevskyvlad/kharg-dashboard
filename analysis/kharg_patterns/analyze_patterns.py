#!/usr/bin/env python3
"""
Kharg per-tank interpretation layer — pattern research.

Pulls full per-tank history from Supabase (raw_observations), reconstructs
fill% / volume with the production method, clusters tanks by geography and
behaviour, detects rotation episodes (one cluster draws while another fills
with the terminal roughly flat), measures what happens 3/7/14 days after each,
and answers the key operational questions.

Writes everything to analysis/kharg_patterns/:
  - tank_fill_matrix.csv     (date x tank fill%)
  - tank_vol_matrix.csv      (date x tank volume, Mn bbl)
  - tank_stats.csv           (per-tank behaviour stats + classification)
  - episodes.csv             (rotation episodes + forward outcomes)
  - RESULTS.md               (human-readable findings + signal dictionary)

Runs in GitHub Actions (Supabase is unreachable from the dev sandbox).
Every stage is wrapped so partial results still get written on failure.
"""
import os, json, math, traceback
from datetime import timedelta
import numpy as np
import pandas as pd

try:
    from supabase import create_client
except Exception:
    create_client = None

OUT = os.path.dirname(os.path.abspath(__file__))
SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

BARREL_PER_M3 = 6.2898
TANK_HEIGHT_MIN = 2.0
PCTL_LO, PCTL_HI = 0.05, 0.95

# Known labels from the product skill
KNOWN_LOADING = {"t60_2", "t55_17"}
KNOWN_RESERVE = {"t55_31", "t60_1"}
KNOWN_IDLE = {"t55_5", "t55_10"}

md = []  # RESULTS.md accumulator
def log(s=""):
    print(s)
    md.append(s)

def flush():
    with open(os.path.join(OUT, "RESULTS.md"), "w") as f:
        f.write("\n".join(md) + "\n")

# ----------------------------------------------------------------------
# Reconstruct tank coordinates exactly as scripts/main_script.py builds them
# so we can assign geography (id = t{radius}_{running index}).
# ----------------------------------------------------------------------
def build_tanks():
    tanks = []
    for lat, lon in [
        (29.22173338156137,50.31654819949878),(29.221638820210273,50.315043243946754),
        (29.221840470329855,50.31352911803618),(29.223156804822317,50.315849029771684),
        (29.222897954869325,50.3144195158663)]:
        tanks.append({"id": f"t60_{len(tanks)}","lat":lat,"lon":lon,"radius":60,"h_max":18})
    for lat, lon in [
        (29.23596908236423,50.31262041733899),(29.236450714039865,50.31093940154909),
        (29.237953299445927,50.31084946173491),(29.239068986858886,50.30968216852048),
        (29.238926386438376,50.306814019231595),(29.237608159424354,50.30726790052075),
        (29.236277626607237,50.307724616432196),(29.23632825886285,50.30930379724654),
        (29.234972729277782,50.3081865326845),(29.23511372342508,50.30981988700097),
        (29.233643820114974,50.30863801777735),(29.233830846966335,50.31028094525233),
        (29.23144045674206,50.308740382712706),(29.231783644202153,50.310385461009616),
        (29.232076421746672,50.312234207587345),(29.23021527314446,50.3101614506557),
        (29.228871179764596,50.309966015820464)]:
        tanks.append({"id": f"t55_{len(tanks)}","lat":lat,"lon":lon,"radius":55,"h_max":17})
    for lat, lon in [
        (29.22952889859548,50.312187062458655),(29.22976318244905,50.313501526357314),
        (29.229963868790236,50.31481471193477),(29.228423649589555,50.31244141506387),
        (29.228616543309535,50.313733669284346),(29.22881135666992,50.31501996070619),
        (29.22720915866873,50.312698386632235),(29.227435720069668,50.31399368455909),
        (29.227640813983122,50.31526721678495)]:
        tanks.append({"id": f"t40_{len(tanks)}","lat":lat,"lon":lon,"radius":40,"h_max":14})
    tanks.append({"id":"t55_31","lat":29.232346117474552,"lon":50.313766336270064,"radius":55,"h_max":17})
    for lat, lon in [
        (29.230717804109947,50.31192906218232),(29.230947410364493,50.31324186520479),
        (29.231135117157258,50.31454377331308)]:
        tanks.append({"id": f"t40_{len(tanks)}","lat":lat,"lon":lon,"radius":40,"h_max":14})
    for lat, lon in [
        (29.226211162396392,50.31288089406942),(29.226383532289553,50.313809940357444),
        (29.226542892768347,50.31474811903658),(29.226689095834363,50.31565041182445),
        (29.22515641565613,50.31310639099006),(29.22532307473051,50.31405678957588),
        (29.225474230400657,50.31496277701283),(29.22561536435356,50.315886541434466),
        (29.22435349448338,50.313281303050275),(29.224512933127766,50.31422412891253),
        (29.224656215116465,50.31513496008376),(29.22481810164328,50.316063441149545)]:
        tanks.append({"id": f"t25_{len(tanks)}","lat":lat,"lon":lon,"radius":25,"h_max":12})
    return {t["id"]: t for t in tanks}

def fetch_raw():
    if create_client is None:
        raise RuntimeError("supabase client not installed")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows, step, off = [], 1000, 0
    while True:
        r = sb.table("raw_observations").select(
            "date,tank_id,radius_m,h_max,db_mean").range(off, off+step-1).execute()
        d = r.data or []
        rows.extend(d)
        if len(d) < step:
            break
        off += step
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df = df.dropna(subset=["db_mean"]).drop_duplicates(["date","tank_id"])
    return df

def main():
    log("# Kharg Per-Tank Interpretation Layer — Findings\n")
    coords = build_tanks()

    log("## Step 1 — Data")
    df = fetch_raw()
    dmin = df["db_mean"].quantile(PCTL_LO); dmax = df["db_mean"].quantile(PCTL_HI)
    df["clip"] = df["db_mean"].clip(dmin, dmax)
    df["fill_ratio"] = (dmax - df["clip"]) / (dmax - dmin)
    df["fill_pct"] = (df["fill_ratio"] * 100).round(2)
    hmax = df["tank_id"].map(lambda t: coords.get(t, {}).get("h_max", 15))
    rad = df["tank_id"].map(lambda t: coords.get(t, {}).get("radius", 40))
    df["h_cur"] = TANK_HEIGHT_MIN + df["fill_ratio"] * (hmax - TANK_HEIGHT_MIN)
    df["vol_mn"] = (math.pi * rad**2 * df["h_cur"] * BARREL_PER_M3) / 1e6
    ntanks = df["tank_id"].nunique()
    log(f"- Rows: {len(df):,} | tanks: {ntanks} | dates: {df['date'].nunique()} "
        f"({df['date'].min().date()} → {df['date'].max().date()})")
    log(f"- Global clip: dB[{dmin:.2f}, {dmax:.2f}] (5/95 pctl)\n")

    fill = df.pivot_table(index="date", columns="tank_id", values="fill_pct").sort_index()
    vol = df.pivot_table(index="date", columns="tank_id", values="vol_mn").sort_index()
    fill.to_csv(os.path.join(OUT, "tank_fill_matrix.csv"))
    vol.to_csv(os.path.join(OUT, "tank_vol_matrix.csv"))
    total = vol.sum(axis=1)
    flush()

    # ---- Step 2: clustering ----
    log("## Step 2 — Clusters")
    lats = {t: coords[t]["lat"] for t in coords}
    q1, q2 = np.quantile(list(lats.values()), [1/3, 2/3])
    def geo(t):
        la = lats.get(t)
        if la is None: return "unknown"
        return "north" if la >= q2 else ("south" if la < q1 else "central")
    geog = {t: geo(t) for t in fill.columns}

    stats = []
    for t in fill.columns:
        s = fill[t].dropna()
        if s.empty: continue
        stats.append(dict(tank=t, geo=geog.get(t,"?"),
                          radius=coords.get(t,{}).get("radius"),
                          mean_fill=round(s.mean(),1), std_fill=round(s.std(),1),
                          min_fill=round(s.min(),1), max_fill=round(s.max(),1),
                          n=len(s)))
    st = pd.DataFrame(stats).set_index("tank")

    def classify(row):
        t = row.name
        if t in KNOWN_RESERVE: return "reserve"
        if t in KNOWN_LOADING: return "loading"
        if t in KNOWN_IDLE:    return "idle"
        if row["mean_fill"] >= 85 and row["std_fill"] <= 6: return "reserve"
        if row["mean_fill"] <= 20 and row["std_fill"] <= 6: return "idle"
        if row["std_fill"] >= st["std_fill"].quantile(0.75): return "loading"
        return "swing"
    st["type"] = st.apply(classify, axis=1)
    st.to_csv(os.path.join(OUT, "tank_stats.csv"))
    log("Type counts: " + ", ".join(f"{k}={v}" for k,v in st["type"].value_counts().items()))
    log("Geo counts: " + ", ".join(f"{k}={v}" for k,v in st["geo"].value_counts().items()) + "\n")
    for typ in ["reserve","loading","idle","swing"]:
        members = list(st[st["type"]==typ].index)
        log(f"- **{typ}** ({len(members)}): {', '.join(members)}")
    log("")
    flush()

    # cluster volume series (fill-forward across irregular passes for deltas)
    volf = vol.ffill()
    def cluster_series(mask_type=None, mask_geo=None):
        cols = [t for t in vol.columns if (mask_type is None or st.loc[t,"type"]==mask_type)
                and (mask_geo is None or geog.get(t)==mask_geo)]
        return volf[cols].sum(axis=1) if cols else pd.Series(dtype=float)

    south = cluster_series(mask_geo="south"); north = cluster_series(mask_geo="north")
    central = cluster_series(mask_geo="central")
    loading_c = cluster_series(mask_type="loading"); reserve_c = cluster_series(mask_type="reserve")

    # ---- Step 3: rotation episodes ----
    log("## Step 3 — Rotation episodes (one cluster draws, another fills, terminal ~flat)")
    dates = list(volf.index)
    d_tot = total.reindex(volf.index).ffill().diff()
    clusters = {"south":south,"north":north,"central":central}
    deltas = {k: v.diff() for k,v in clusters.items()}
    tot_std = d_tot.std()
    FLAT = 0.4      # Mn bbl terminal move considered "flat"
    MOVE = 0.3      # Mn bbl cluster move considered material
    episodes = []
    for i, dt in enumerate(dates):
        if i == 0: continue
        tot = d_tot.get(dt, np.nan)
        if pd.isna(tot) or abs(tot) > FLAT: continue
        draw = [(k, deltas[k].get(dt)) for k in clusters]
        drawer = [(k,x) for k,x in draw if pd.notna(x) and x <= -MOVE]
        filler = [(k,x) for k,x in draw if pd.notna(x) and x >= MOVE]
        if drawer and filler:
            episodes.append(dict(date=dt, terminal_delta=round(tot,3),
                drawer=";".join(f"{k}{x:+.2f}" for k,x in drawer),
                filler=";".join(f"{k}{x:+.2f}" for k,x in filler)))
    log(f"- Terminal daily-move std ≈ {tot_std:.2f} Mn bbl; flat<{FLAT}, cluster move>{MOVE}")
    log(f"- Episodes found: {len(episodes)}\n")

    # ---- Step 4: forward outcomes ----
    def nearest_after(series, dt, days):
        target = dt + timedelta(days=days)
        sub = series[series.index >= dt]
        if sub.empty: return np.nan
        idx = sub.index[np.argmin(np.abs((sub.index - target).days))]
        return series.get(idx)
    ep_rows = []
    for e in episodes:
        dt = e["date"]; base = total.reindex(volf.index).ffill().get(dt)
        row = dict(**{k:(v.date().isoformat() if k=="date" else v) for k,v in e.items()})
        for h in (3,7,14):
            tot_h = nearest_after(total.reindex(volf.index).ffill(), dt, h)
            south_h = nearest_after(south, dt, h)
            row[f"tot_chg_{h}d"] = round((tot_h-base),3) if pd.notna(tot_h) and pd.notna(base) else None
            s0 = south.get(dt)
            row[f"south_chg_{h}d"] = round((south_h-s0),3) if pd.notna(south_h) and pd.notna(s0) else None
        ep_rows.append(row)
    ep_df = pd.DataFrame(ep_rows)
    if not ep_df.empty:
        ep_df.to_csv(os.path.join(OUT, "episodes.csv"), index=False)
        drew_after = (ep_df["tot_chg_14d"] < -0.5).sum()
        log(f"- Of {len(ep_df)} episodes, {drew_after} were followed by a terminal drawdown "
            f">0.5 Mn bbl within ~14d ({100*drew_after/max(len(ep_df),1):.0f}%).")
        log("\n| date | terminal Δ | drawer | filler | tot Δ7d | tot Δ14d |")
        log("|---|---|---|---|---|---|")
        for _,r in ep_df.iterrows():
            log(f"| {r['date']} | {r['terminal_delta']:+.2f} | {r['drawer']} | {r['filler']} "
                f"| {r.get('tot_chg_7d')} | {r.get('tot_chg_14d')} |")
    else:
        log("- No rotation episodes matched the thresholds (terminal rarely stays flat while clusters swap).")
    log("")
    flush()

    # ---- Step 5: external cross-check (partial — export pulses as internal proxy) ----
    log("## Step 5 — Cross-check")
    tt = total.reindex(volf.index).ffill()
    pulses = []
    idx = list(tt.index)
    for i in range(len(idx)):
        for j in range(i+1, len(idx)):
            dd = (idx[j]-idx[i]).days
            if dd < 6: continue
            if dd > 12: break
            drop = tt.iloc[i]-tt.iloc[j]
            if drop >= 2.0:
                pulses.append((idx[i].date().isoformat(), idx[j].date().isoformat(), round(drop,2), dd))
    log(f"- Export-pulse events (≥2 Mn bbl drop in 6–12d): {len(pulses)}")
    for p in pulses[:20]:
        log(f"  - {p[0]} → {p[1]}: −{p[2]} Mn bbl ({p[3]}d)")
    log("- Full external overlay (tanker AIS / EIA / news) requires those feeds; flagged for manual overlay.\n")
    flush()

    # ---- Step 6: key questions ----
    log("## Step 6 — Key questions")
    # (a) how often tanks stall (no material change across >=14 day span)
    stalls = {}
    for t in fill.columns:
        s = fill[t].dropna()
        if len(s) < 3: continue
        cnt = 0
        for a,b in zip(s.index[:-1], s.index[1:]):
            if (b-a).days >= 12 and abs(s[b]-s[a]) < 3:
                cnt += 1
        stalls[t] = cnt
    stall_series = pd.Series(stalls).sort_values(ascending=False)
    log(f"- **Tanks fully stalling** (≥12d gap with <3% fill change): most frequent — "
        + ", ".join(f"{t}({c})" for t,c in stall_series.head(6).items()))
    # (b) always-full reserve / always-working
    res = st[st["type"]=="reserve"].sort_values("mean_fill", ascending=False)
    wrk = st[st["type"]=="loading"].sort_values("std_fill", ascending=False)
    log(f"- **Always full (reserve)**: " + ", ".join(f"{t}({st.loc[t,'mean_fill']:.0f}%)" for t in res.index))
    log(f"- **Always working (loading)**: " + ", ".join(f"{t}(σ{st.loc[t,'std_fill']:.0f})" for t in wrk.index))
    # (c) leaders: cross-correlation of tank fill-change vs terminal total change at lag
    tchg = tt.diff()
    leaders = {}
    for t in fill.columns:
        s = vol[t].ffill().diff().reindex(tt.index)
        if s.notna().sum() < 8: continue
        best_lag, best_c = 0, 0
        for lag in range(0,4):
            c = s.shift(lag).corr(tchg)  # tank leads terminal by `lag` steps
            if pd.notna(c) and c > best_c:
                best_c, best_lag = c, lag
        if best_lag >= 1 and best_c > 0.3:
            leaders[t] = (best_lag, round(best_c,2))
    log("- **Leader tanks** (move ~1 pass before the terminal, corr>0.3): "
        + (", ".join(f"{t}(lag{v[0]},r{v[1]})" for t,v in sorted(leaders.items(), key=lambda x:-x[1][1])[:6])
           if leaders else "none clearly ahead of aggregate"))
    # (d) cyclicality: mean peak-to-peak on active tanks
    log("- **Cyclicality**: assessed per active tank via fill oscillation (see tank_stats.csv std_fill).")
    log("")
    flush()

    # ---- Step 7: signal dictionary ----
    log("## Step 7 — Signal dictionary (pattern → interpretation → confirmations)\n")
    log("| # | Pattern | Interpretation | Confirmed on |")
    log("|---|---|---|---|")
    log(f"| 1 | Loading cluster ({', '.join(sorted(KNOWN_LOADING))}) fill drops >5% pass-on-pass | Active VLCC offtake underway | {int((fill[list(KNOWN_LOADING & set(fill.columns))].diff()< -5).sum().sum())} pass-events |")
    log(f"| 2 | Terminal drop ≥2 Mn bbl in 6–12d | Export pulse (probable tanker loading) | {len(pulses)} events |")
    log(f"| 3 | Reserve tanks ({', '.join(sorted(KNOWN_RESERVE))}) flat & >85% | Strategic hold, not in rotation | {int(st.loc[[t for t in KNOWN_RESERVE if t in st.index],'std_fill'].le(6).sum())}/{len(KNOWN_RESERVE)} tanks stable |")
    log(f"| 4 | Idle tanks ({', '.join(sorted(KNOWN_IDLE))}) mean fill <20% for full period | Offline / out of service | {int(st.loc[[t for t in KNOWN_IDLE if t in st.index],'mean_fill'].le(20).sum())}/{len(KNOWN_IDLE)} confirmed |")
    log(f"| 5 | Cluster rotation (one draws, one fills, terminal flat) | Internal repositioning ahead of offtake | {len(episodes)} episodes |")
    log(f"| 6 | Leader tank moves before aggregate | Early tell for terminal direction | {len(leaders)} candidate leaders |")
    log(f"| 7 | Tank stall ≥12d no change | Tank parked / filled-and-held | {int((stall_series>0).sum())} tanks show ≥1 stall |")
    log("")
    log("_Generated by analysis/kharg_patterns/analyze_patterns.py_")
    flush()
    log("DONE")
    flush()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("\n\n## ERROR\n```")
        log(str(e))
        log(traceback.format_exc())
        log("```")
        flush()
        raise
