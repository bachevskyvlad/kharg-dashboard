"""Local test run: python -m oil_digest [--only uani,china] [--state PATH] [--inbox DIR] [--out DIR] [--commit]
                                      [--today YYYY-MM-DD] [--all-days] [--plan]

Prints the messages that would be sent. Nothing is sent anywhere. --commit marks them as sent in the state file.
"""
import argparse
import datetime as dt
import logging
import re
from pathlib import Path

from .core import Digest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="oil_digest_state.sqlite")
    ap.add_argument("--inbox", default="inbox")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="", help="also write each message as an .html file here")
    ap.add_argument("--commit", action="store_true", help="mark printed messages as sent")
    ap.add_argument("--today", default="")
    ap.add_argument("--all-days", action="store_true", help="ignore the release schedule, check every source")
    ap.add_argument("--plan", action="store_true", help="print on which days of this month each source is checked")
    a = ap.parse_args()
    if a.plan:
        from .schedule import plan
        day = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
        for src, days in plan(day).items():
            print(f"{src:13s} {', '.join(map(str, days))}")
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    d = Digest(a.state, a.inbox, sources=[s for s in a.only.split(",") if s] or None,
               today=dt.date.fromisoformat(a.today) if a.today else None, use_schedule=not a.all_days)
    msgs = d.collect()
    for m in msgs:
        print("=" * 60, f"\n[{m.source} · {m.key} · {len(m.text)} chars]\n")
        print(re.sub(r"<[^>]+>", "", m.text))
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            Path(a.out, f"{m.source}_{re.sub(r'[^A-Za-z0-9_-]+', '_', m.key)[-60:]}.html").write_text(m.text, encoding="utf-8")
        if a.commit:
            d.commit(m)
    print(f"\n{len(msgs)} message(s)")


if __name__ == "__main__":
    main()
