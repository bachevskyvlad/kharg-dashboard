"""On which days each source is checked.

The bot calls Digest.collect() once a day (09:00 Europe/Berlin). Each source is checked only inside its release
window, plus every Monday outside it as a safety net for a late release. Windows are a few days wider than
the dates actually observed, so a slightly early or late release is still caught the same day.

Observed release dates (checked 23.11.2026 on the sources themselves):
  UANI Tanker Tracker (month M-1)   03.06, 10.07, 07.08, 08.09, 07.10, 10.11.2026          -> days 1-14
  China Customs preliminary (M-1)   06.03 (Jan-Feb), 08.04, 08.05, 10.06, 15.07, 10.08,
                                    09.09, 15.10, 10.11.2026                               -> days 5-18
  IEA Oil Market Report             11.09, 14.10, 13.11.2026 (2025: 11-17 of each month)   -> days 9-18
  OPEC MOMR                         usually the 10th-16th; the PDF is dropped into inbox/ by hand,
                                    so the inbox is read every day from the 9th            -> days 9-31
  JODI World Database CSV           last change 22.11.2026; updated about once a month,
                                    the check is a cheap conditional request (HTTP 304)    -> days 14-28
  UANI Iran Shipping Update         published on US working days                          -> Mon-Fri
Change WINDOWS or pass Digest(config={"schedule": {...}}) to override.
"""
from __future__ import annotations

import datetime as dt

# source -> {"days": (first, last) day of month} or {"weekdays": {0..6}} (0 = Monday)
WINDOWS: dict[str, dict] = {
    "uani_monthly": {"days": (1, 14)},
    "uani_updates": {"weekdays": {0, 1, 2, 3, 4}},
    "china": {"days": (5, 18)},
    "iea": {"days": (9, 18)},
    "opec": {"days": (9, 31)},
    "jodi": {"days": (14, 28)},
}
SAFETY_NET_WEEKDAY = 0   # outside its window every source is still checked on Mondays


def is_due(source: str, day: dt.date, windows: dict | None = None) -> bool:
    w = (windows or WINDOWS).get(source)
    if w is None:
        return True
    if "weekdays" in w:
        return day.weekday() in w["weekdays"]
    first, last = w["days"]
    return first <= day.day <= last or day.weekday() == SAFETY_NET_WEEKDAY


def plan(month_start: dt.date, windows: dict | None = None) -> dict[str, list[int]]:
    """Days of a month on which each source is checked (for the README / a sanity check)."""
    out: dict[str, list[int]] = {}
    d = month_start.replace(day=1)
    while d.month == month_start.month:
        for s in (windows or WINDOWS):
            if is_due(s, d, windows):
                out.setdefault(s, []).append(d.day)
        d += dt.timedelta(days=1)
    return out
