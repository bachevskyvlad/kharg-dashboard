"""
seed_hormuz_from_reference.py — Seed hormuz_weekly from pre-computed reference data.

Reads diagnostics/hormuz/reference_passes.csv (240 passes, 2024-01-01→2026-11-08),
computes weekly aggregates (tracking v1.1 logic is already in the reference),
fetches AIS from IMF PortWatch, writes to Supabase hormuz_weekly.

No SAR access needed — uses validated reference data.

Usage:
  python scripts/seed_hormuz_from_reference.py --commit
  python scripts/seed_hormuz_from_reference.py --dry-run
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta

SB_URL = os.environ.get("SUPABASE_URL", "https://yoekhkczwdeyauwarsyy.supabase.co")
SB_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or ""
TG_BOT  = os.environ.get("TG_BOT", "")
TG_CHAT = os.environ.get("TG_CHAT", "")

DIAG = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "hormuz")


def tg(text):
    if not TG_BOT or not TG_CHAT:
        return
    payload = json.dumps({"chat_id": TG_CHAT, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_BOT}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def sb_upsert(table, rows):
    if not SB_KEY:
        print("ABORT: SUPABASE_KEY not set")
        sys.exit(1)
    url = f"{SB_URL}/rest/v1/{table}"
    headers = {
        "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        req = urllib.request.Request(url, data=json.dumps(batch).encode(), headers=headers, method="POST")
        for attempt in range(4):
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                if resp.status in (200, 201, 204):
                    print(f"  ✓ {len(batch)} rows → {table}")
                    break
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                print(f"  HTTP {e.code}: {body[:300]}")
                raise RuntimeError(f"HTTP {e.code} on {table}: {body[:200]}")
            except Exception as e:
                print(f"  err: {e}")
            time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Failed to upsert to {table}")


def load_passes():
    rows = []
    with open(os.path.join(DIAG, "reference_passes.csv")) as f:
        for r in csv.DictReader(f):
            n_all = float(r["n_all"]) if r["n_all"] else 0
            rows.append({
                "date":  r["date"],
                "orbit": int(r["orbit"]),
                "n_all": n_all,
                "large": float(r["large"]) if r["large"] else 0,
                "gap":   float(r["gap"])   if r.get("gap")  and r["gap"]  else None,
                "stay":  float(r["stay"])  if r.get("stay") and r["stay"] else None,
                "gone":  float(r["gone"])  if r.get("gone") and r["gone"] else None,
                "fail":  n_all < 10,
            })
    return rows


def fetch_ais(start, end):
    url = (
        "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
        "Daily_Chokepoints_Data/FeatureServer/0/query"
        "?where=portid%3D'chokepoint6'&outFields=date_,n_tanker,n_total"
        "&f=json&resultRecordCount=5000"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"[AIS-WARN] {e} — continuing without AIS")
        return {}
    result = {}
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        d = datetime.utcfromtimestamp(a["date_"] / 1000).strftime("%Y-%m-%d")
        if start <= d <= end:
            result[d] = {"n_tanker": a.get("n_tanker"), "n_total": a.get("n_total")}
    print(f"  AIS: {len(result)} days loaded")
    return result


def weekly_ais(ais_daily, ws, we):
    t_vals, tt_vals = [], []
    d = datetime.strptime(ws, "%Y-%m-%d").date()
    end = datetime.strptime(we, "%Y-%m-%d").date()
    while d <= end:
        s = d.strftime("%Y-%m-%d")
        if s in ais_daily:
            t  = ais_daily[s]["n_tanker"]
            tt = ais_daily[s]["n_total"]
            if t  is not None: t_vals.append(t)
            if tt is not None: tt_vals.append(tt)
        d += timedelta(days=1)
    return (
        round(sum(t_vals)/len(t_vals),  2) if t_vals  else None,
        round(sum(tt_vals)/len(tt_vals), 2) if tt_vals else None,
    )


def aggregate_weekly(passes, ais_daily):
    weeks = defaultdict(list)
    for p in passes:
        if p["fail"]:
            continue
        d = datetime.strptime(p["date"], "%Y-%m-%d").date()
        ws = (d - timedelta(days=d.weekday())).isoformat()
        weeks[ws].append(p)

    rows = []
    for ws in sorted(weeks):
        ps = weeks[ws]
        paired = [p for p in ps if p.get("gap") is not None]

        large_vals = [p["large"] for p in ps]
        n_all_vals = [p["n_all"]  for p in ps]
        stay_vals  = [p["stay"]   for p in paired if p.get("stay") is not None]
        gone_vals  = [p["gone"]   for p in paired if p.get("gone") is not None]
        gap_vals   = [p["gap"]    for p in paired if p.get("gap")  is not None]

        large_mean   = round(sum(large_vals)/len(large_vals), 2) if large_vals else None
        n_all_mean   = round(sum(n_all_vals)/len(n_all_vals), 2) if n_all_vals else None
        gone_per_day = (round(sum(gone_vals)/sum(gap_vals), 3)
                        if gap_vals and sum(gap_vals) > 0 else None)
        stay_share   = (round(sum(stay_vals)/sum(large_vals), 3)
                        if large_vals and sum(large_vals) > 0 and stay_vals else None)
        dwell_days   = (round(large_mean / gone_per_day, 2)
                        if large_mean and gone_per_day and gone_per_day > 0 else None)

        we = (datetime.strptime(ws, "%Y-%m-%d").date() + timedelta(days=6)).isoformat()
        n_tanker, _ = weekly_ais(ais_daily, ws, we)

        row = dict(
            week=ws,
            passes=len(ps),
            pairs=len(paired),
            large_avg=large_mean,
            all_avg=n_all_mean,
            stay_share=stay_share,
            gone_per_day=gone_per_day,
            dwell_days=dwell_days,
        )
        if n_tanker is not None:
            row["n_tanker"] = n_tanker
        rows.append(row)
    return rows


def build_pass_rows(passes):
    return [dict(
        date=p["date"],
        orbit=p["orbit"],
        n_all=int(round(p["n_all"])),
        n_large=int(round(p["large"])),
    ) for p in passes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--commit",  action="store_true")
    args = ap.parse_args()
    dry = args.dry_run or not args.commit

    print("Loading reference passes...")
    passes = load_passes()
    valid  = [p for p in passes if not p["fail"]]
    print(f"  {len(passes)} total, {len(valid)} valid, "
          f"{sum(1 for p in passes if p['fail'])} fail")

    start = min(p["date"] for p in passes)
    end   = max(p["date"] for p in passes)
    print(f"  Range: {start} → {end}")

    print("Fetching AIS from IMF PortWatch...")
    ais = fetch_ais(start, end)

    print("Computing weekly aggregates...")
    weekly = aggregate_weekly(passes, ais_daily=ais)
    print(f"  {len(weekly)} weeks")

    if dry:
        print("\n[DRY RUN] Sample (last 5 weeks):")
        for r in weekly[-5:]:
            print(" ", r)
        print(f"\nWould write: hormuz_passes={len(passes)}, hormuz_weekly={len(weekly)}")
        return

    tg(f"\U0001f6f0 Hormuz seed started — {len(passes)} passes, {len(weekly)} weeks")

    print("\nWriting hormuz_passes...")
    sb_upsert("hormuz_passes", build_pass_rows(passes))

    print("Writing hormuz_weekly...")
    sb_upsert("hormuz_weekly", weekly)

    tg(f"✅ Hormuz seeded: {len(weekly)} weeks, {start}→{end}")
    print(f"\n✓ Done: {len(passes)} passes, {len(weekly)} weekly rows")


if __name__ == "__main__":
    main()
