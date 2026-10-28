"""
tg_quality.py — Signal quality second Telegram message.

For each terminal with new > 0 in ingest_summary.json:
  - fetch db_mean for the new date from raw_observations (kharg) or
    terminal_observations (others)
  - compute mean, std, n tanks
  - compare against 4-week rolling median for that terminal
  - flag outlier tanks (|db - tank_median| > 2*tank_std over 4w)

Usage:
  python scripts/tg_quality.py          # dry-run (print only)
  python scripts/tg_quality.py --send   # send to Telegram
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

_sb_url = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
_sb_key = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

from supabase import create_client
sb = create_client(_sb_url, _sb_key)

COUNTRY_FLAG = {
    "IR": "🇮🇷", "IQ": "🇮🇶", "SA": "🇸🇦", "AE": "🇦🇪",
    "QA": "🇶🇦", "KW": "🇰🇼", "BH": "🇧🇭", "RU": "🇷🇺",
}
COUNTRY_ORDER = ["IR", "IQ", "SA", "AE", "QA", "KW", "BH", "RU"]

WEEKS_HISTORY = 4   # weeks of history for baseline
OUTLIER_SIGMA = 2.0 # std multiplier for per-tank outlier flag


def _fetch_rows(tid, since_date):
    """Return db_mean rows for terminal since since_date."""
    table = "raw_observations" if tid == "kharg" else "terminal_observations"
    eq_col = None if tid == "kharg" else "terminal_id"

    q = sb.table(table).select("date,tank_id,db_mean").gte("date", since_date)
    if eq_col:
        q = q.eq(eq_col, tid)
    rows = []
    offset = 0
    while True:
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def _quality(tid, new_date_str):
    """Compute quality metrics for terminal on new_date_str."""
    since = (datetime.strptime(new_date_str, "%Y-%m-%d")
             - timedelta(weeks=WEEKS_HISTORY)).strftime("%Y-%m-%d")
    rows = _fetch_rows(tid, since)
    if not rows:
        return None

    from collections import defaultdict
    by_date = defaultdict(list)
    by_tank = defaultdict(list)
    for r in rows:
        d = r["date"][:10]
        v = r.get("db_mean")
        if v is not None:
            by_date[d].append(v)
            by_tank[r["tank_id"]].append(v)

    new_vals = by_date.get(new_date_str, [])
    if not new_vals:
        return None

    # Current pass stats
    n = len(new_vals)
    mean_db = sum(new_vals) / n
    std_db  = (sum((v - mean_db)**2 for v in new_vals) / n) ** 0.5 if n > 1 else 0.0

    # 4-week baseline: median across all dates except new_date
    hist_all = [v for d, vs in by_date.items() if d != new_date_str for v in vs]
    if hist_all:
        hist_sorted = sorted(hist_all)
        mid = len(hist_sorted) // 2
        baseline = (hist_sorted[mid] if len(hist_sorted) % 2
                    else (hist_sorted[mid-1] + hist_sorted[mid]) / 2)
    else:
        baseline = None

    delta = round(mean_db - baseline, 2) if baseline is not None else None

    # Per-tank outlier count: tanks on new_date where |db - tank_4w_median| > 2σ
    outliers = 0
    for tank_id, hist_vs in by_tank.items():
        hist_excl = [v for v in hist_vs]  # includes new date; find new val
        # new val for this tank
        tank_new = next(
            (r["db_mean"] for r in rows
             if r["date"][:10] == new_date_str and r["tank_id"] == tank_id
             and r.get("db_mean") is not None),
            None
        )
        if tank_new is None or len(hist_excl) < 3:
            continue
        hist_excl_only = [v for r in rows
                          if r["date"][:10] != new_date_str
                          and r["tank_id"] == tank_id
                          and (v := r.get("db_mean")) is not None]
        if len(hist_excl_only) < 2:
            continue
        t_sorted = sorted(hist_excl_only)
        t_mid = len(t_sorted) // 2
        t_med = (t_sorted[t_mid] if len(t_sorted) % 2
                 else (t_sorted[t_mid-1] + t_sorted[t_mid]) / 2)
        t_std = (sum((v - (sum(hist_excl_only)/len(hist_excl_only)))**2
                     for v in hist_excl_only) / len(hist_excl_only)) ** 0.5
        if t_std > 0 and abs(tank_new - t_med) > OUTLIER_SIGMA * t_std:
            outliers += 1

    return {
        "n": n,
        "mean_db": round(mean_db, 1),
        "std_db":  round(std_db, 1),
        "baseline": round(baseline, 1) if baseline is not None else None,
        "delta":   delta,
        "outliers": outliers,
    }


def _fmt_delta(delta):
    if delta is None:
        return "Δ=n/a"
    sign = "+" if delta >= 0 else ""
    arrow = "▲" if delta > 0.2 else ("▼" if delta < -0.2 else "→")
    return f"Δ{sign}{delta} {arrow}"


def _fmt_outlier(outliers, n):
    if outliers == 0:
        return "✅"
    pct = int(outliers / n * 100) if n else 0
    return f"⚠️ {outliers} tank{'s' if outliers>1 else ''} ({pct}%)"


def build_message(summary, run_number, run_id, repo):
    lines = []
    lines.append("📊 <b>OilRadar — Signal Quality</b>")
    lines.append("")

    active = {tid: v for tid, v in summary.items() if v.get("new", 0) > 0}
    if not active:
        lines.append("No new observations this run.")
        return "\n".join(lines)

    # Group by country in order
    by_country = {}
    for tid, v in active.items():
        c = v.get("country", "??")
        by_country.setdefault(c, []).append((tid, v))

    ordered_countries = [c for c in COUNTRY_ORDER if c in by_country]
    ordered_countries += [c for c in by_country if c not in ordered_countries]

    for country in ordered_countries:
        flag = COUNTRY_FLAG.get(country, "🌐")
        for tid, v in by_country[country]:
            name = v["name"]
            new_date = v.get("new_date")  # injected below
            if not new_date:
                continue

            q = _quality(tid, new_date)
            if q is None:
                lines.append(f"{flag} <b>{name}</b>  {new_date}  <i>no db data</i>")
                continue

            base_str = (f"vs 4w={q['baseline']} dB  {_fmt_delta(q['delta'])}"
                        if q["baseline"] is not None else "no baseline")

            lines.append(
                f"{flag} <b>{name}</b>  <code>{new_date}</code>\n"
                f"  db={q['mean_db']} dB  σ={q['std_db']}  n={q['n']}  "
                f"{base_str}  {_fmt_outlier(q['outliers'], q['n'])}"
            )

    lines.append("")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"📅 <code>{ts}</code>")
    if run_number and run_id and repo:
        lines.append(f'🔗 <a href="https://github.com/{repo}/actions/runs/{run_id}">Run #{run_number}</a>')

    return "\n".join(lines)


def _inject_new_dates(summary):
    """Add new_date to each terminal with new > 0 by querying latest date."""
    for tid, v in summary.items():
        if v.get("new", 0) == 0:
            continue
        table = "raw_observations" if tid == "kharg" else "terminal_observations"
        q = sb.table(table).select("date").order("date", desc=True).limit(1)
        if tid != "kharg":
            q = q.eq("terminal_id", tid)
        rows = q.execute().data
        if rows:
            v["new_date"] = rows[0]["date"][:10]


def _send(text, bot_url, chat_id):
    data = urllib.parse.urlencode({"chat_id": chat_id, "parse_mode": "HTML", "text": text})
    req  = urllib.request.Request(bot_url, data=data.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    summary_path = "data/ingest_summary.json"
    if not os.path.exists(summary_path):
        sys.exit(f"FATAL: {summary_path} not found")

    with open(summary_path) as f:
        summary = json.load(f)

    _inject_new_dates(summary)

    run_number = os.environ.get("GITHUB_RUN_NUMBER", "")
    run_id     = os.environ.get("GITHUB_RUN_ID", "")
    repo       = os.environ.get("GITHUB_REPOSITORY", "")

    text = build_message(summary, run_number, run_id, repo)

    print("=== tg_quality message ===")
    print(text)
    print("===========================")

    if args.send:
        tg_bot  = os.environ.get("TG_BOT", "")
        tg_chat = os.environ.get("TG_CHAT", "")
        if not tg_bot or not tg_chat:
            sys.exit("FATAL: TG_BOT and TG_CHAT must be set")
        _send(text, tg_bot, tg_chat)
        print("Sent to Telegram.")
    else:
        print("(dry-run, not sent)")


if __name__ == "__main__":
    main()
