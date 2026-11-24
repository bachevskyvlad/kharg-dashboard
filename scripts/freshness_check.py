"""
freshness_check.py — Check data freshness for all 9 pipeline sources.

Sources and SLAs (§B2):
  kharg_tanks         volume_timeseries               ≤8 days
  goreh_omid          terminal_volume_timeseries      ≤8 days
  bahregan            terminal_volume_timeseries      ≤8 days
  jask_tanks          terminal_volume_timeseries      ≤8 days
  anchorage_daily     anchorage_daily                 ≤8 days
  anchorage_queue     anchorage_queue                 ≤8 days
  berths              berth_occupancy.json            ≤5 days
  hormuz_queue        hormuz_passes                   ≤5 days
  gulf_exit           gulf_exit_passes (all zones)    ≤8 days

Sends a Markdown table to Telegram on completion.
Exits with code 1 if any source exceeds its SLA.

Usage:
  python scripts/freshness_check.py
  python scripts/freshness_check.py --no-fail   # report only, never exit 1
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, datetime

SB_URL  = os.environ.get("SUPABASE_URL") or ""
SB_KEY  = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY") or ""
TG_BOT  = os.environ.get("TG_BOT", "")
TG_CHAT = os.environ.get("TG_CHAT", "")

TODAY   = date.today()


def _sb_get(path):
    if not SB_URL or not SB_KEY:
        return []
    url = f"{SB_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [warn] SB GET {path}: {e}")
        return []


def _last_date_in_table(table, date_col="date", filters=None):
    q = f"{table}?select={date_col}&order={date_col}.desc&limit=1"
    if filters:
        for k, v in filters.items():
            q += f"&{k}=eq.{v}"
    rows = _sb_get(q)
    if rows:
        return rows[0].get(date_col)
    return None


def _last_date_berths():
    """Read berth_occupancy.json and return the last pass date."""
    for path in ["data/berth_occupancy.json", "product/data/berth_occupancy.json"]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                passes = data.get("passes") or []
                if passes:
                    return max(p.get("date", "") for p in passes if p.get("date"))
            except Exception as e:
                print(f"  [warn] berths read {path}: {e}")
    # Fallback: try Supabase
    return _last_date_in_table("berth_occupancy", date_col="date")


def _days_ago(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        return (TODAY - d).days
    except Exception:
        return None


def _tg_send(text):
    if not TG_BOT or not TG_CHAT:
        print("[TG-SKIP]", text[:80])
        return
    payload = json.dumps({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_BOT}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[TG-ERR] {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fail", action="store_true",
                    help="Report only; never exit 1")
    args = ap.parse_args()

    sources = [
        {
            "name": "kharg_tanks",
            "sla":  8,
            "last": _last_date_in_table("volume_timeseries"),
        },
        {
            "name": "goreh_omid",
            "sla":  8,
            "last": _last_date_in_table("terminal_volume_timeseries",
                                        filters={"terminal_id": "goreh_omid"}),
        },
        {
            "name": "bahregan",
            "sla":  8,
            "last": _last_date_in_table("terminal_volume_timeseries",
                                        filters={"terminal_id": "bahregan"}),
        },
        {
            "name": "jask_tanks",
            "sla":  8,
            "last": _last_date_in_table("terminal_volume_timeseries",
                                        filters={"terminal_id": "jask"}),
        },
        {
            "name": "anchorage_daily",
            "sla":  8,
            "last": _last_date_in_table("anchorage_daily"),
        },
        {
            "name": "anchorage_queue",
            "sla":  8,
            "last": _last_date_in_table("anchorage_queue"),
        },
        {
            "name": "berths",
            "sla":  5,
            "last": _last_date_berths(),
        },
        {
            "name": "hormuz_queue",
            "sla":  5,
            "last": _last_date_in_table("hormuz_passes"),
        },
        {
            "name": "gulf_exit",
            "sla":  8,
            "last": _last_date_in_table("gulf_exit_passes"),
        },
    ]

    lines_tg  = [f"<b>Freshness check {TODAY}</b>"]
    lines_tg.append("")
    lines_tg.append(f"{'Source':<20} {'Last date':<12} {'Age':>5}  SLA  Status")
    lines_tg.append("─" * 58)

    any_breach = False
    for src in sources:
        age  = _days_ago(src["last"])
        last = src["last"][:10] if src["last"] else "UNKNOWN"
        age_str = str(age) if age is not None else "?"
        ok = (age is not None) and (age <= src["sla"])
        status = "✅" if ok else "❌"
        if not ok:
            any_breach = True

        line = f"{src['name']:<20} {last:<12} {age_str:>5}d / {src['sla']}d {status}"
        print(line)
        lines_tg.append(line)

    summary = "✅ All sources within SLA" if not any_breach else "❌ SLA breach detected"
    print(summary)
    lines_tg.append("")
    lines_tg.append(summary)

    _tg_send("\n".join(lines_tg))

    if any_breach and not args.no_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
