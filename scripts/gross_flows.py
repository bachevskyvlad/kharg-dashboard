"""
gross_flows.py — Kharg gross inflow / outflow between consecutive SAR passes.

Replaces the Supabase-side logic (RPC refresh_gross_flows() + matview
kharg_gross_flows), which is DEPRECATED (see README.md). The database no longer
computes anything: this script reads raw_observations, computes in Python and
upserts into a plain table, kharg_gross_flows_v2 (on_conflict = date_to).

Calibration — the ONLY difference from the legacy matview:
  legacy matview : dB_min/dB_max = p5/p95 per tank (each tank its own scale)
  this script    : volume.compute_volumes() — global p5/p95 over all tanks and
                   all dates, i.e. exactly the barrels behind volume_timeseries.
  Consequence: sum over tanks of (net_mb) == Δ volume_timeseries daily between
  the same two dates (when the same tanks are observed on both dates).

Per pass (date_from → date_to = consecutive Kharg observation dates):
  Δ_i            = barrels_i(date_to) − barrels_i(date_from)   [tanks seen on both]
  gross_out_mb   = Σ max(−Δ_i, 0) / 1e6
  gross_in_mb    = Σ max( Δ_i, 0) / 1e6
  net_mb         = gross_in_mb − gross_out_mb
  tanks_draining = #(Δ_i < 0),  tanks_filling = #(Δ_i > 0)
  days           = date_to − date_from (calendar days)
Values rounded to 4 decimals, same as the legacy matview.

Usage:
  python scripts/gross_flows.py                # compute + upsert kharg_gross_flows_v2
  python scripts/gross_flows.py --dry-run      # compute only, no writes
  python scripts/gross_flows.py --compare 20   # print legacy vs v2 for last 20 pairs
"""

import argparse

import numpy as np
import pandas as pd

from volume import compute_volumes

TABLE_V2 = "kharg_gross_flows_v2"
TABLE_LEGACY = "kharg_gross_flows"   # deprecated matview, read-only, for --compare
COLS = ["date_from", "date_to", "days", "gross_out_mb", "gross_in_mb",
        "net_mb", "tanks_draining", "tanks_filling"]


def compute_gross_flows(raw: pd.DataFrame) -> pd.DataFrame:
    """
    raw: raw_observations rows with columns date, tank_id, radius_m, h_max, db_mean.
    Pass ALL history — calibration is global (same contract as compute_volumes).
    Returns DataFrame with COLS, one row per consecutive pair of dates.
    """
    df = raw[["date", "tank_id", "radius_m", "h_max", "db_mean"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df, _, _ = compute_volumes(df)

    # Same per-(day, tank) reduction as volume.aggregate_daily
    per_tank = (df.groupby(["date", "tank_id"])["barrels"].mean()
                  .unstack("tank_id").sort_index())
    if len(per_tank) < 2:
        return pd.DataFrame(columns=COLS)

    delta = per_tank.diff().iloc[1:]          # NaN where tank missing on either date
    d_from = per_tank.index[:-1]
    d_to = per_tank.index[1:]

    out = pd.DataFrame({
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "days": (d_to - d_from).days.astype(int),
        "gross_out_mb": (-delta.clip(upper=0)).sum(axis=1).to_numpy() / 1e6,
        "gross_in_mb": delta.clip(lower=0).sum(axis=1).to_numpy() / 1e6,
        "tanks_draining": (delta < 0).sum(axis=1).to_numpy().astype(int),
        "tanks_filling": (delta > 0).sum(axis=1).to_numpy().astype(int),
    })
    out["net_mb"] = out["gross_in_mb"] - out["gross_out_mb"]
    for c in ("gross_out_mb", "gross_in_mb", "net_mb"):
        out[c] = out[c].round(4)
    return out[COLS].reset_index(drop=True)


def _fetch_all(sb, table, select, order):
    rows, offset = [], 0
    while True:
        batch = (sb.table(table).select(select).order(order)
                   .range(offset, offset + 999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        offset += 1000


def load_raw(sb) -> pd.DataFrame:
    return pd.DataFrame(_fetch_all(sb, "raw_observations",
                                   "id,date,tank_id,radius_m,h_max,db_mean", "id"))


def upsert_gross_flows(sb, flows: pd.DataFrame, chunk=500):
    """Upsert into kharg_gross_flows_v2 keyed by date_to. Non-fatal on error."""
    records = [{k: (v.item() if isinstance(v, np.generic) else v) for k, v in r.items()}
               for r in flows[COLS].to_dict("records")]
    try:
        for i in range(0, len(records), chunk):
            sb.table(TABLE_V2).upsert(records[i:i + chunk], on_conflict="date_to").execute()
        print(f"  Supabase {TABLE_V2}: {len(records)} rows upserted")
    except Exception as e:
        print(f"  [!] {TABLE_V2} upsert failed: {e}")


def compare_with_legacy(new: pd.DataFrame, old: pd.DataFrame, n=20) -> pd.DataFrame:
    """Side-by-side legacy matview vs v2 for the last n pairs. Report only."""
    m = old.merge(new, on=["date_from", "date_to"], suffixes=("_old", "_v2"))
    m = m.sort_values("date_to").tail(n)

    def pct(a, b):
        a, b = a.astype(float), b.astype(float)
        return np.where(a.abs() > 1e-9, (b - a) / a.abs() * 100, np.nan)

    for c in ("gross_out_mb", "gross_in_mb", "net_mb"):
        m[f"{c}_dpct"] = pct(m[f"{c}_old"], m[f"{c}_v2"])
    return m


def print_comparison(m: pd.DataFrame):
    hdr = (f"{'date_from':>10} {'date_to':>10} | {'out_old':>7} {'out_v2':>7} {'Δ%':>7} | "
           f"{'in_old':>7} {'in_v2':>7} {'Δ%':>7} | {'net_old':>7} {'net_v2':>7} {'Δ%':>8} | "
           f"{'drn o/v2':>8} {'fil o/v2':>8}")
    print(hdr)
    print("-" * len(hdr))
    f = lambda x: "   n/a" if pd.isna(x) else f"{x:+6.1f}"
    for d in m.to_dict("records"):
        print(f"{d['date_from']:>10} {d['date_to']:>10} | "
              f"{d['gross_out_mb_old']:7.3f} {d['gross_out_mb_v2']:7.3f} {f(d['gross_out_mb_dpct']):>7} | "
              f"{d['gross_in_mb_old']:7.3f} {d['gross_in_mb_v2']:7.3f} {f(d['gross_in_mb_dpct']):>7} | "
              f"{d['net_mb_old']:+7.3f} {d['net_mb_v2']:+7.3f} {f(d['net_mb_dpct']):>8} | "
              f"{d['tanks_draining_old']:>3}/{d['tanks_draining_v2']:<4} "
              f"{d['tanks_filling_old']:>3}/{d['tanks_filling_v2']:<4}")
    for c in ("gross_out_mb", "gross_in_mb", "net_mb"):
        s = m[f"{c}_dpct"].abs()
        print(f"  {c:13s}: median |Δ%| = {s.median():6.1f}   max |Δ%| = {s.max():6.1f}")


def run(sb, dry_run=False) -> pd.DataFrame:
    raw = load_raw(sb)
    print(f"[gross_flows] {len(raw)} raw_observations rows")
    flows = compute_gross_flows(raw)
    print(f"[gross_flows] {len(flows)} pass pairs "
          f"({flows['date_from'].iloc[0]} → {flows['date_to'].iloc[-1]})" if len(flows) else
          "[gross_flows] no pairs")
    if not dry_run and len(flows):
        upsert_gross_flows(sb, flows)
    return flows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compare", type=int, default=0,
                    help="print legacy matview vs v2 for the last N pairs (implies --dry-run)")
    args = ap.parse_args()

    from config import sb
    flows = run(sb, dry_run=args.dry_run or args.compare > 0)
    if args.compare:
        old = pd.DataFrame(_fetch_all(sb, TABLE_LEGACY, ",".join(COLS), "date_to"))
        print_comparison(compare_with_legacy(flows, old, args.compare))
