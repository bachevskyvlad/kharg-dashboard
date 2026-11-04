"""
validate_pass.py — Telegram validation dry-run (M1–M5).

Reads data from the last 3 Monitor runs (Supabase + local JSON files),
computes 5 validation metrics, formats Telegram HTML, and prints to stdout.
Does NOT send anything.

Usage:
  python scripts/validate_pass.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Supabase client (requires SUPABASE_URL + SUPABASE_KEY)
# ---------------------------------------------------------------------------
_sb_url = os.environ.get("SUPABASE_URL") or ""
_sb_key = os.environ.get("SUPABASE_KEY") or ""
if not _sb_url or not _sb_key:
    sys.exit("FATAL: SUPABASE_URL and SUPABASE_KEY env vars must be set")

from supabase import create_client
sb = create_client(_sb_url, _sb_key)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_CAPACITY_MN_BBL = 33.2          # Mn bbl (product skill §5)
EXPORT_PULSE_DROP   = 2.0           # Mn bbl threshold
EXPORT_PULSE_DAYS   = (6, 12)       # window days

# SPI thresholds
SPI_NORMAL   = 70.0
SPI_ELEVATED = 85.0


def _fetch_daily(limit=60):
    """Return list of {date, vol_mn_bbl} from kharg_daily, sorted ascending."""
    rows = (
        sb.table("kharg_daily")
        .select("date,vol_mn_bbl")
        .order("date", desc=False)
        .limit(limit)
        .execute()
        .data
    )
    return rows


def _load_data_daily_json():
    """Load data/data_daily.json if present."""
    p = Path("data/data_daily.json")
    if p.exists():
        d = json.loads(p.read_text())
        return list(zip(d.get("dates", []), d.get("values", [])))
    return []


def _last_3_run_dates(rows):
    """Return the 3 most recent distinct dates in the dataset."""
    dates = sorted({r["date"][:10] for r in rows}, reverse=True)
    return dates[:3]


# ---------------------------------------------------------------------------
# M1 — Data freshness: last 3 run dates, gap between them
# ---------------------------------------------------------------------------
def m1_freshness(rows):
    dates = _last_3_run_dates(rows)
    if not dates:
        return None, "no data"
    gaps = []
    for i in range(len(dates) - 1):
        d1 = datetime.strptime(dates[i], "%Y-%m-%d")
        d2 = datetime.strptime(dates[i + 1], "%Y-%m-%d")
        gaps.append((d1 - d2).days)
    latest = dates[0]
    now = datetime.now(timezone.utc).date()
    staleness = (now - datetime.strptime(latest, "%Y-%m-%d").date()).days
    return {
        "last_3": dates,
        "gaps_days": gaps,
        "staleness_days": staleness,
        "ok": staleness <= 7,
    }, None


# ---------------------------------------------------------------------------
# M2 — Tank count per pass: raw_observations, last 3 dates
# ---------------------------------------------------------------------------
def m2_tank_count(last_3_dates):
    if not last_3_dates:
        return None, "no dates"
    rows = (
        sb.table("raw_observations")
        .select("date,tank_id")
        .in_("date", last_3_dates)
        .execute()
        .data
    )
    by_date = {}
    for r in rows:
        d = r["date"][:10]
        by_date[d] = by_date.get(d, 0) + 1
    return {"counts": {d: by_date.get(d, 0) for d in last_3_dates}}, None


# ---------------------------------------------------------------------------
# M3 — Volume range: last 3 dates vol_mn_bbl
# ---------------------------------------------------------------------------
def m3_volume(rows, last_3_dates):
    by_date = {r["date"][:10]: r["vol_mn_bbl"] for r in rows}
    vols = [(d, by_date.get(d)) for d in last_3_dates]
    return {"vols": vols}, None


# ---------------------------------------------------------------------------
# M4 — Export Pulse: any 2.0+ Mn bbl drop in 6–12 days in last 30 rows
# ---------------------------------------------------------------------------
def m4_export_pulse(rows):
    vols = [(r["date"][:10], r["vol_mn_bbl"]) for r in rows if r["vol_mn_bbl"] is not None]
    vols.sort(key=lambda x: x[0])
    pulses = []
    for i in range(len(vols)):
        for j in range(i + 1, len(vols)):
            d0, v0 = vols[i]
            d1, v1 = vols[j]
            gap = (datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d0, "%Y-%m-%d")).days
            if gap < EXPORT_PULSE_DAYS[0]:
                continue
            if gap > EXPORT_PULSE_DAYS[1]:
                break
            drop = v0 - v1
            if drop >= EXPORT_PULSE_DROP:
                pulses.append({"from": d0, "to": d1, "drop_mn": round(drop, 2), "days": gap})
    return {"pulses": pulses[-3:] if pulses else []}, None


# ---------------------------------------------------------------------------
# M5 — SPI: Storage Pressure Index for last 3 dates
# ---------------------------------------------------------------------------
def m5_spi(rows, last_3_dates):
    by_date = {r["date"][:10]: r["vol_mn_bbl"] for r in rows}
    spis = []
    for d in last_3_dates:
        vol = by_date.get(d)
        if vol is not None:
            spi = vol / MAX_CAPACITY_MN_BBL * 100
            if spi >= SPI_ELEVATED:
                label = "CRITICAL"
            elif spi >= SPI_NORMAL:
                label = "ELEVATED"
            else:
                label = "NORMAL"
            spis.append((d, round(vol, 2), round(spi, 1), label))
        else:
            spis.append((d, None, None, "N/A"))
    return {"spis": spis}, None


# ---------------------------------------------------------------------------
# Format Telegram HTML
# ---------------------------------------------------------------------------
def format_telegram(m1, m2, m3, m4, m5):
    lines = []
    lines.append("&#128270; <b>OilRadar — Validation Pass (dry-run)</b>")
    lines.append(f"&#128197; <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</code>")
    lines.append("")

    # M1
    lines.append("<b>M1 — Freshness</b>")
    if m1:
        for d in m1["last_3"]:
            lines.append(f"  &#128197; {d}")
        if m1["gaps_days"]:
            lines.append(f"  gaps: {', '.join(str(g)+'d' for g in m1['gaps_days'])}")
        stale_mark = "&#9989;" if m1["ok"] else "&#10060;"
        lines.append(f"  staleness: {m1['staleness_days']}d {stale_mark}")
    else:
        lines.append("  no data")
    lines.append("")

    # M2
    lines.append("<b>M2 — Tank count per pass</b>")
    if m2:
        for d, cnt in m2["counts"].items():
            mark = "&#9989;" if cnt >= 40 else "&#9888;"
            lines.append(f"  {d}: {cnt} tanks {mark}")
    else:
        lines.append("  no data")
    lines.append("")

    # M3
    lines.append("<b>M3 — Volume (last 3 passes)</b>")
    if m3:
        for d, vol in m3["vols"]:
            v_str = f"{vol:.2f} Mb" if vol is not None else "N/A"
            lines.append(f"  {d}: {v_str}")
    lines.append("")

    # M4
    lines.append("<b>M4 — Export Pulses (last 30d)</b>")
    if m4 and m4["pulses"]:
        for p in m4["pulses"]:
            lines.append(f"  &#128674; {p['from']} → {p['to']}: -{p['drop_mn']} Mb in {p['days']}d")
    else:
        lines.append("  no export pulses detected")
    lines.append("")

    # M5
    lines.append("<b>M5 — Storage Pressure Index</b>")
    if m5:
        for d, vol, spi, label in m5["spis"]:
            if spi is not None:
                if label == "CRITICAL":
                    mark = "&#128308;"
                elif label == "ELEVATED":
                    mark = "&#127992;"
                else:
                    mark = "&#9989;"
                lines.append(f"  {d}: {spi}% ({label}) {mark}")
            else:
                lines.append(f"  {d}: N/A")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=== validate_pass.py dry-run ===\n")

    rows = _fetch_daily(limit=60)
    if not rows:
        print("ERROR: no rows from kharg_daily")
        return

    last3 = _last_3_run_dates(rows)
    print(f"Last 3 dates in kharg_daily: {last3}\n")

    m1, e1 = m1_freshness(rows)
    m2, e2 = m2_tank_count(last3)
    m3, e3 = m3_volume(rows, last3)
    m4, e4 = m4_export_pulse(rows[-30:])
    m5, e5 = m5_spi(rows, last3)

    for label, err in [("M1", e1), ("M2", e2), ("M3", e3), ("M4", e4), ("M5", e5)]:
        if err:
            print(f"WARNING {label}: {err}")

    text = format_telegram(m1, m2, m3, m4, m5)

    print("=== Telegram message (NOT sent) ===\n")
    print(text)
    print("\n=== end ===")


if __name__ == "__main__":
    main()
