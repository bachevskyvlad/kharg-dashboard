"""
hormuz_queue_backfill.py — Backfill Hormuz queue metric 2024-01-01 → today.

Reads SAR passes from Microsoft Planetary Computer (sentinel-1-rtc, sign_inplace).
Applies hormuz_detect.py v2 logic and hormuz_track.py v1.1 (with length tolerance).

Writes to Supabase tables (requires SUPABASE_URL + SUPABASE_SERVICE_KEY):
  hormuz_passes  — one row per SAR pass
  hormuz_ships   — one row per detected ship per pass
  hormuz_weekly  — weekly aggregates with AIS from IMF PortWatch

Validation (--validate flag):
  - Monthly aggregates vs reference_monthly.csv (±10% on months with pairs≥6)
  - Fail-pass rate < 3%
  - v1.0 vs v1.1 stay_share comparison

Usage:
  python hormuz_queue_backfill.py --dry-run          # detect only, no write
  python hormuz_queue_backfill.py --validate         # validate then backfill
  python hormuz_queue_backfill.py --commit           # full backfill + write
  python hormuz_queue_backfill.py --weekly           # weekly update (new passes only)
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

import numpy as np

# ── credentials ───────────────────────────────────────────────────────────────
SB_URL  = os.environ.get("SUPABASE_URL", "https://yoekhkczwdeyauwarsyy.supabase.co")
# Project uses SUPABASE_KEY for service-role writes (same convention as all other scripts)
SB_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY") or ""
TG_BOT          = os.environ.get("TG_BOT", "")
TG_CHAT         = os.environ.get("TG_CHAT", "")

DIAG = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "hormuz")

# ── SAR geometry (from hormuz_detect.py) ──────────────────────────────────────
AOI   = (56.30, 26.84, 56.60, 27.03)
ZONE  = (56.33, 26.86, 56.58, 27.02)
RES   = 20.0
W     = int((AOI[2]-AOI[0])*111320*math.cos(math.radians(26.94))/RES)
H     = int((AOI[3]-AOI[1])*111320/RES)
ORBITS = {166, 57}


# ── Telegram alert ────────────────────────────────────────────────────────────
def tg_alert(text):
    if not TG_BOT or not TG_CHAT:
        print("[TG-SKIP]", text)
        return
    payload = json.dumps({"chat_id": TG_CHAT, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_BOT}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("[TG-ERR]", e)


# ── Supabase helpers ──────────────────────────────────────────────────────────
def _sb_headers():
    return {
        "apikey": SB_SERVICE_KEY,
        "Authorization": f"Bearer {SB_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


_ON_CONFLICT = {
    "hormuz_passes":  "date,orbit",
    "hormuz_weekly":  "week",
    # hormuz_ships has no unique constraint — use delete+insert pattern
}


def sb_upsert(table, rows, dry_run=False):
    if dry_run:
        print(f"[DRY] upsert {len(rows)} rows → {table}")
        return
    if not SB_SERVICE_KEY:
        tg_alert("⚠️ Hormuz: SUPABASE_SERVICE_KEY not set — skipping write")
        print("SKIP: no SUPABASE_SERVICE_KEY")
        return
    conflict = _ON_CONFLICT.get(table, "")
    url = f"{SB_URL}/rest/v1/{table}" + (f"?on_conflict={conflict}" if conflict else "")
    payload = json.dumps(rows).encode()
    req = urllib.request.Request(url, data=payload, headers=_sb_headers(), method="POST")
    for attempt in range(4):
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            if resp.status in (200, 201):
                print(f"  ✓ upsert {len(rows)} → {table}")
                return
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  HTTP {e.code}: {body[:200]}")
        except Exception as e:
            print(f"  err: {e}")
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to upsert to {table} after 4 attempts")


def sb_fetch(table, select="*", filters=None):
    if not SB_SERVICE_KEY:
        return []
    url = f"{SB_URL}/rest/v1/{table}?select={select}"
    if filters:
        for k, v in filters.items():
            url += f"&{k}=eq.{v}"
    url += "&limit=10000"
    req = urllib.request.Request(url, headers={
        "apikey": SB_SERVICE_KEY,
        "Authorization": f"Bearer {SB_SERVICE_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print(f"[SB-FETCH-ERR] {table}: {e}")
        return []


# ── SAR detection (hormuz_detect.py v2 logic) ─────────────────────────────────
def detect_pass(item, land_mask, static_mask, dry_run=False):
    """Run v2 detector on one STAC item. Returns pass record dict or None."""
    try:
        import rasterio
        from rasterio.vrt import WarpedVRT
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds
        from scipy import ndimage
    except ImportError as e:
        sys.exit(f"Missing dependency: {e}\npip install rasterio scipy planetary_computer pystac_client")

    T = from_bounds(*AOI, W, H)
    try:
        with rasterio.open(item.assets["vv"].href) as s:
            with WarpedVRT(s, crs="EPSG:4326", transform=T, width=W, height=H,
                           resampling=Resampling.average, nodata=np.nan) as v:
                a = v.read(1)
    except Exception as e:
        print(f"  [READ-ERR] {item.id}: {e}")
        return None

    valid = np.isfinite(a) & (a > 0)
    zc0 = int((ZONE[0]-AOI[0])/(AOI[2]-AOI[0])*W)
    zc1 = int((ZONE[2]-AOI[0])/(AOI[2]-AOI[0])*W)
    zr0 = int((AOI[3]-ZONE[3])/(AOI[3]-AOI[1])*H)
    zr1 = int((AOI[3]-ZONE[1])/(AOI[3]-AOI[1])*H)
    cov = valid[zr0:zr1, zc0:zc1].mean()
    if cov < 0.90:
        return None  # coverage < 90%

    db  = 10 * np.log10(np.where(valid, a, 1e-6))
    sea = valid & ~land_mask & ~static_mask
    med = np.median(db[sea])
    sd  = np.std(db[sea][db[sea] < med + 6])
    thr = med + max(10, 5 * sd)
    if not np.isfinite(thr):
        return None

    lab, n = ndimage.label((db > thr) & sea)
    lengths, positions = [], []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        if len(ys) < 4 or len(ys) > 500:
            continue
        cy, cx = ys.mean(), xs.mean()
        if not (zr0 <= cy < zr1 and zc0 <= cx < zc1):
            continue
        q = np.c_[xs, ys].astype(float)
        q -= q.mean(0)
        _, evecs = np.linalg.eigh(np.cov(q.T))
        length = int((np.ptp(q @ evecs[:, -1]) + 1) * RES)
        lengths.append(length)
        lat = round(float(AOI[3] - (cy + .5) / H * (AOI[3] - AOI[1])), 5)
        lon = round(float(AOI[0] + (cx + .5) / W * (AOI[2] - AOI[0])), 5)
        positions.append((lat, lon, length))

    return dict(
        date=item.datetime.strftime("%Y-%m-%d"),
        orbit=item.properties.get("sat:relative_orbit"),
        time=item.datetime.strftime("%H:%M"),
        sat=item.id[:3],
        n_all=len(lengths),
        n150=sum(l >= 150 for l in lengths),
        n250=sum(l >= 250 for l in lengths),
        thr=round(float(thr), 1),
        ships=positions,
        fail=(len(lengths) < 10),
    )


# ── Tracking (hormuz_track.py v1.1) ───────────────────────────────────────────
MATCH_KM = 0.8


def _dist(a, b):
    return math.hypot((a[1]-b[1])*111.32*math.cos(math.radians(26.95)),
                      (a[0]-b[0])*111.32)


def _same(a, b, len_tol=None):
    if _dist(a, b) >= MATCH_KM:
        return False
    if len_tol is None:
        return True
    return abs(a[2]-b[2]) <= len_tol * max(a[2], b[2])


def track_pair(prev, cur, len_tol=0.25):
    """Return (large, stay, gone) for pair of passes. len_tol=None → v1.0."""
    S  = [s for s in cur["ships"]  if s[2] >= 250]
    C  = [s for s in cur["ships"]  if s[2] >= 200]
    Pb = [s for s in prev["ships"] if s[2] >= 250]
    P  = [s for s in prev["ships"] if s[2] >= 200]
    stay = sum(1 for s in S  if any(_same(s, p, len_tol) for p in P))
    gone = sum(1 for p in Pb if not any(_same(p, c, len_tol) for c in C))
    return len(S), stay, gone


# ── AIS from IMF PortWatch ─────────────────────────────────────────────────────
PORTWATCH_URL = (
    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
    "?where=portid%3D'chokepoint6'&outFields=date_,n_tanker,n_total"
    "&f=json&resultRecordCount=5000"
)


def fetch_ais(start_date: str, end_date: str) -> dict:
    """Return {date_str: {n_tanker, n_total}} from PortWatch."""
    try:
        with urllib.request.urlopen(PORTWATCH_URL, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"[AIS-ERR] {e}")
        return {}
    result = {}
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        # date_ is epoch ms
        d = datetime.utcfromtimestamp(a["date_"] / 1000).strftime("%Y-%m-%d")
        if start_date <= d <= end_date:
            result[d] = {"n_tanker": a.get("n_tanker"), "n_total": a.get("n_total")}
    return result


def weekly_ais_avg(ais_daily: dict, week_start: str, week_end: str):
    vals_tanker, vals_total = [], []
    d = datetime.strptime(week_start, "%Y-%m-%d").date()
    end = datetime.strptime(week_end, "%Y-%m-%d").date()
    while d <= end:
        s = d.strftime("%Y-%m-%d")
        if s in ais_daily:
            t = ais_daily[s]["n_tanker"]
            tt = ais_daily[s]["n_total"]
            if t is not None:
                vals_tanker.append(t)
            if tt is not None:
                vals_total.append(tt)
        d += timedelta(days=1)
    return (
        round(sum(vals_tanker)/len(vals_tanker), 2) if vals_tanker else None,
        round(sum(vals_total)/len(vals_total), 2) if vals_total else None,
    )


# ── Reference data ────────────────────────────────────────────────────────────
def load_reference_monthly():
    path = os.path.join(DIAG, "reference_monthly.csv")
    rows = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            rows[r["date"][:7]] = r
    return rows


def load_reference_passes():
    path = os.path.join(DIAG, "reference_passes.csv")
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


# ── Main backfill logic ───────────────────────────────────────────────────────
def run_detection(start: str, end: str):
    """Fetch and detect all S1 passes from start to end. Returns list of pass dicts."""
    try:
        import pystac_client
        import planetary_computer as pc
    except ImportError as e:
        sys.exit(f"Missing: {e}\npip install pystac_client planetary_computer")

    land   = np.load(os.path.join(DIAG, "mask_land.npy"))
    static = np.load(os.path.join(DIAG, "mask_static.npy"))
    assert land.shape == (H, W), f"mask_land shape mismatch: {land.shape}"

    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
    )
    items = sorted(
        cat.search(
            collections=["sentinel-1-rtc"],
            bbox=list(ZONE),
            datetime=f"{start}/{end}",
            max_items=2000,
        ).items(),
        key=lambda x: x.datetime,
    )
    print(f"  Found {len(items)} STAC items for {start}→{end}")

    seen, passes = set(), []
    for item in items:
        orb = item.properties.get("sat:relative_orbit")
        if orb not in ORBITS:
            continue
        key = (item.datetime.strftime("%Y-%m-%d"), orb)
        if key in seen:
            continue
        seen.add(key)
        print(f"  Detecting {key[0]} orbit={orb} ...", end=" ", flush=True)
        rec = detect_pass(item, land, static)
        if rec is None:
            print("SKIP (cov<90%)")
            continue
        status = "FAIL" if rec["fail"] else "ok"
        print(f"n_all={rec['n_all']} n250={rec['n250']} [{status}]")
        passes.append(rec)

    return passes


def compute_tracking(passes, len_tol=0.25):
    """Add gap/stay/gone to passes in-place (v1.1 with len_tol). Returns passes."""
    valid = [p for p in passes if not p.get("fail")]
    for i, cur in enumerate(valid):
        cur["gap"] = None
        cur["stay"] = None
        cur["gone"] = None
        if i == 0:
            continue
        prev = valid[i-1]
        d_cur  = datetime.strptime(cur["date"],  "%Y-%m-%d")
        d_prev = datetime.strptime(prev["date"], "%Y-%m-%d")
        gap = (d_cur - d_prev).days
        if 1 <= gap <= 6:
            large, stay, gone = track_pair(prev, cur, len_tol=len_tol)
            cur["gap"]   = gap
            cur["stay"]  = stay
            cur["gone"]  = gone
    return passes


def aggregate_weekly(passes, ais_daily):
    """Produce weekly records from passes. Week = ISO Monday-based."""
    from collections import defaultdict
    weeks = defaultdict(lambda: {"passes": [], "pairs": []})

    valid = [p for p in passes if not p.get("fail")]
    for p in valid:
        d = datetime.strptime(p["date"], "%Y-%m-%d").date()
        # ISO week Monday
        iso = d - timedelta(days=d.weekday())
        weeks[iso.isoformat()]["passes"].append(p)

    rows = []
    for ws, wdata in sorted(weeks.items()):
        ps = wdata["passes"]
        paired = [p for p in ps if p.get("gap") is not None]
        large_vals = [p.get("n250", 0) for p in ps]
        n_all_vals = [p.get("n_all", 0) for p in ps]
        stay_vals  = [p["stay"] for p in paired if p.get("stay") is not None]
        gone_vals  = [p["gone"] for p in paired if p.get("gone") is not None]
        gap_vals   = [p["gap"]  for p in paired if p.get("gap")  is not None]

        large_mean = round(sum(large_vals)/len(large_vals), 2) if large_vals else None
        n_all_mean = round(sum(n_all_vals)/len(n_all_vals), 2) if n_all_vals else None
        gone_per_day = (
            round(sum(gone_vals)/sum(gap_vals), 3)
            if gap_vals and sum(gap_vals) > 0 else None
        )
        stay_share = (
            round(sum(stay_vals)/sum(large_vals), 3)
            if large_vals and sum(large_vals) > 0 and stay_vals else None
        )
        dwell_days = (
            round(large_mean / gone_per_day, 2)
            if large_mean and gone_per_day and gone_per_day > 0 else None
        )

        we = (datetime.strptime(ws, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
        n_tanker_avg, n_total_avg = weekly_ais_avg(ais_daily, ws, we)

        rows.append(dict(
            week=ws,
            passes=len(ps),
            pairs=len(paired),
            large_avg=large_mean,
            all_avg=n_all_mean,
            stay_share=stay_share,
            gone_per_day=gone_per_day,
            dwell_days=dwell_days,
            n_tanker=n_tanker_avg,
        ))
    return rows


def aggregate_monthly(passes):
    """Aggregate passes to monthly records (for validation)."""
    from collections import defaultdict
    months = defaultdict(lambda: {"passes": [], "paired": []})
    valid = [p for p in passes if not p.get("fail")]
    for p in valid:
        m = p["date"][:7]
        months[m]["passes"].append(p)
        if p.get("gap") is not None:
            months[m]["paired"].append(p)
    rows = {}
    for m, md in months.items():
        ps = md["passes"]
        paired = md["paired"]
        large_vals = [p.get("n250", 0) for p in ps]
        gone_vals  = [p["gone"] for p in paired if p.get("gone") is not None]
        gap_vals   = [p["gap"]  for p in paired if p.get("gap")  is not None]
        large_mean = sum(large_vals)/len(large_vals) if large_vals else None
        gone_per_day = (
            sum(gone_vals)/sum(gap_vals)
            if gap_vals and sum(gap_vals) > 0 else None
        )
        rows[m] = dict(
            passes=len(ps),
            pairs=len(paired),
            large=round(large_mean, 2) if large_mean is not None else None,
            gone_per_day=round(gone_per_day, 3) if gone_per_day is not None else None,
        )
    return rows


# ── Validation ────────────────────────────────────────────────────────────────
def validate(passes_v11, passes_v10):
    """
    Validate computed passes against reference_monthly.csv.
    Returns (ok: bool, report: str).
    """
    ref = load_reference_monthly()
    agg_v11 = aggregate_monthly(passes_v11)  # with length tol (v1.1)
    agg_v10 = aggregate_monthly(passes_v10)  # without length tol (v1.0, for reference check)

    total_passes = len(passes_v11)
    fail_passes  = sum(1 for p in passes_v11 if p.get("fail"))
    fail_rate    = fail_passes / total_passes if total_passes else 0

    lines = []
    lines.append("=== HORMUZ QUEUE VALIDATION REPORT ===")
    lines.append(f"Total passes detected: {total_passes}")
    lines.append(f"Fail passes (n_all<10): {fail_passes} ({fail_rate:.1%})")
    lines.append("")

    errors = []

    # 1. Fail rate
    if fail_rate >= 0.03:
        errors.append(f"FAIL: fail_pass_rate={fail_rate:.1%} ≥ 3%")
    else:
        lines.append(f"✓ Fail rate {fail_rate:.1%} < 3%")

    # 2. Monthly comparison (v1.0 vs reference, months with pairs≥6)
    lines.append("")
    lines.append("Monthly comparison (v1.0 logic, ±10% tolerance, pairs≥6):")
    lines.append(f"{'Month':<10} {'ref_large':>9} {'got_large':>9} {'diff%':>7} {'ref_gpd':>8} {'got_gpd':>8} {'diff%':>7} {'pairs':>5}")
    month_errors = []
    for m, rv in sorted(ref.items()):
        ref_pairs = int(float(rv.get("pairs") or 0))
        if ref_pairs < 6:
            continue
        if m not in agg_v10:
            continue
        got = agg_v10[m]
        # large
        try:
            rl = float(rv["large"]); gl = got["large"]
        except (KeyError, TypeError, ValueError):
            continue
        if gl is None:
            continue
        pct_l = (gl - rl) / rl * 100 if rl else 0
        # gone_per_day
        try:
            rg = float(rv["gone_per_day"]); gg = got["gone_per_day"]
        except (KeyError, TypeError, ValueError):
            rg = None; gg = None
        pct_g = (gg - rg) / rg * 100 if (rg and gg) else None

        ok_l = abs(pct_l) <= 10
        ok_g = pct_g is None or abs(pct_g) <= 10

        marker = "" if (ok_l and ok_g) else "⚠"
        lines.append(
            f"{m:<10} {rl:>9.2f} {gl:>9.2f} {pct_l:>+7.1f}%"
            f" {rg if rg else 'N/A':>8} {gg if gg else 'N/A':>8}"
            f" {pct_g:>+7.1f}%" if pct_g is not None else
            f"{m:<10} {rl:>9.2f} {gl:>9.2f} {pct_l:>+7.1f}%"
            f" {'N/A':>8} {'N/A':>8} {'N/A':>8}"
            + f"  {got['pairs']:>5} {marker}"
        )
        if not ok_l:
            month_errors.append(f"{m}: large diff {pct_l:+.1f}%")
        if not ok_g:
            month_errors.append(f"{m}: gone_per_day diff {pct_g:+.1f}%")

    if month_errors:
        for e in month_errors:
            errors.append(f"FAIL: {e}")
    else:
        lines.append("✓ All checked months within ±10%")

    # 3. v1.0 vs v1.1 stay_share impact
    lines.append("")
    lines.append("v1.0 vs v1.1 (length tolerance) — stay_share by month:")
    lines.append(f"{'Month':<10} {'v1.0_ss':>8} {'v1.1_ss':>8} {'delta':>7}")

    agg_v10_all = aggregate_monthly(passes_v10)
    agg_v11_all = aggregate_monthly(passes_v11)

    for m in sorted(set(agg_v10_all) | set(agg_v11_all)):
        paired_v10 = [p for p in passes_v10 if p["date"][:7] == m and p.get("gap") is not None]
        paired_v11 = [p for p in passes_v11 if p["date"][:7] == m and p.get("gap") is not None]
        large_v10 = sum(p.get("n250", 0) for p in [q for q in passes_v10 if q["date"][:7] == m and not q.get("fail")])
        large_v11 = sum(p.get("n250", 0) for p in [q for q in passes_v11 if q["date"][:7] == m and not q.get("fail")])
        stay_v10 = sum(p.get("stay", 0) or 0 for p in paired_v10)
        stay_v11 = sum(p.get("stay", 0) or 0 for p in paired_v11)
        ss10 = round(stay_v10/large_v10, 3) if large_v10 else None
        ss11 = round(stay_v11/large_v11, 3) if large_v11 else None
        delta = round(ss11 - ss10, 3) if (ss10 and ss11) else None
        lines.append(f"{m:<10} {ss10 if ss10 else 'N/A':>8} {ss11 if ss11 else 'N/A':>8} {delta if delta else 'N/A':>7}")

    lines.append("")
    if errors:
        lines.append("❌ VALIDATION FAILED:")
        for e in errors:
            lines.append(f"  {e}")
        ok = False
    else:
        lines.append("✅ VALIDATION PASSED")
        ok = True

    return ok, "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run",  action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--commit",   action="store_true")
    ap.add_argument("--weekly",   action="store_true",
                    help="Only process passes since latest in DB")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end",   default=date.today().isoformat())
    args = ap.parse_args()

    if args.weekly and not SB_SERVICE_KEY:
        tg_alert("⚠️ Hormuz weekly: SUPABASE_SERVICE_KEY not set — aborting write")
        print("ABORT: no SUPABASE_SERVICE_KEY for weekly run")
        sys.exit(1)

    start = args.start
    end   = args.end

    if args.weekly:
        # Find last pass date in DB
        rows = sb_fetch("hormuz_passes", select="date", filters={})
        if rows:
            last = max(r["date"] for r in rows)
            start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            print(f"Weekly mode: fetching from {start}")
        else:
            print("No existing passes; full backfill")

    dry_run = args.dry_run or not (args.commit or args.weekly)

    print(f"Detecting SAR passes {start} → {end} ...")
    passes_v11 = run_detection(start, end)

    if args.validate or args.commit:
        # Also compute v1.0 (no length tolerance) for validation report
        import copy
        passes_v10_base = run_detection(start, end)
        passes_v10 = compute_tracking(passes_v10_base, len_tol=None)
        passes_v11 = compute_tracking(passes_v11, len_tol=0.25)

        ok, report = validate(passes_v11, passes_v10)
        print(report)

        report_path = os.path.join(DIAG, "validation_report.txt")
        with open(report_path, "w") as f:
            f.write(report)
        print(f"Validation report saved: {report_path}")

        if not ok and args.commit:
            print("Validation failed — aborting write.")
            sys.exit(2)
    else:
        passes_v11 = compute_tracking(passes_v11, len_tol=0.25)

    # Fetch AIS
    print("Fetching AIS from IMF PortWatch ...")
    ais_daily = fetch_ais(start, end)
    print(f"  Got {len(ais_daily)} AIS days")

    # Build weekly
    weekly_rows = aggregate_weekly(passes_v11, ais_daily)
    print(f"  Built {len(weekly_rows)} weekly records")

    # Prepare DB rows
    pass_rows = []
    ship_rows = []
    for p in passes_v11:
        pass_rows.append(dict(
            date=p["date"],
            orbit=p["orbit"],
            time_utc=p["time"],
            sat=p["sat"],
            n_all=p["n_all"],
            n150=p["n150"],
            n250=p["n250"],
            thr_db=p["thr"],
            gap_days=p.get("gap"),
            stay=p.get("stay"),
            gone=p.get("gone"),
            fail=p.get("fail", False),
        ))
        for lat, lon, length in p["ships"]:
            ship_rows.append(dict(
                pass_date=p["date"],
                pass_orbit=p["orbit"],
                lat=lat,
                lon=lon,
                length_m=length,
            ))

    if dry_run:
        print(f"\n[DRY] Would upsert {len(pass_rows)} passes, "
              f"{len(ship_rows)} ships, {len(weekly_rows)} weekly rows")
        # Print sample weekly
        for r in weekly_rows[-3:]:
            print(" ", r)
        return

    # Write to Supabase
    if pass_rows:
        sb_upsert("hormuz_passes", pass_rows)
    if ship_rows:
        # hormuz_ships has no unique constraint: delete existing rows for
        # these pass_dates, then insert fresh
        ship_dates = list({r["pass_date"] for r in ship_rows})
        for d in ship_dates:
            del_url = (f"{SB_URL}/rest/v1/hormuz_ships"
                       f"?pass_date=eq.{d}")
            req = urllib.request.Request(
                del_url, headers=_sb_headers(), method="DELETE")
            try:
                urllib.request.urlopen(req, timeout=30)
            except Exception as e:
                print(f"  del ships {d}: {e}")
        for i in range(0, len(ship_rows), 500):
            sb_upsert("hormuz_ships", ship_rows[i:i+500])
    if weekly_rows:
        sb_upsert("hormuz_weekly", weekly_rows)

    print(f"\n✓ Done: {len(pass_rows)} passes, {len(ship_rows)} ships, "
          f"{len(weekly_rows)} weekly rows")


if __name__ == "__main__":
    main()
