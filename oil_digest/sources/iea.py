"""IEA Oil Market Report: only the free public page (highlights). Full report and data are paid.

URL pattern: https://www.iea.org/reports/oil-market-report-<month>-<year>; published mid-month.
Numbers are pulled from the highlight sentences with patterns; anything not found is simply left out.
"""
from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from ..core import Context, Message, clip, esc, http_get, link, num, ru_month, signed, trend, MONTHS_EN, MONTHS_RU3

SRC = "iea"
URL = "https://www.iea.org/reports/oil-market-report-{m}-{y}"
NEG = r"(?:fall|fell|decline|declined|drop|dropped|contract|contracted|drew|tumbled|plunged|plunge|lower|edged down|slumped|slump)"
VERB = (r"(rise|rose|grow|grew|increase|increased|climb|climbed|rebound|rebounded|rebounding|surged|built|edged up|"
        r"fall|fell|decline|declined|drop|dropped|contract|contracted|drew|tumbled|plunged|edged down|slumped)")
# IEA wording changes from month to month, so each figure has several patterns; the first match wins.
# All verified on the September, October and November 2026 highlights.


def _sign(verb: str) -> int:
    return -1 if re.match(NEG, verb) else 1


def parse_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("time")
    paras = [p.get_text(" ", strip=True) for p in (soup.find("main") or soup).find_all("p")]
    text = " ".join(" ".join(paras).split())
    d: dict = {"published": t["datetime"][:10] if t and t.has_attr("datetime") else None}
    # annual demand change (present in every report) and, when stated, the level
    for pat in (rf"World oil demand is (?:now )?(?:forecast|expected|set|projected) to {VERB} by ([\d.]+) mb/d in (20\d\d)",
                rf"World oil demand is (?:now )?(?:forecast|expected|set|projected) to {VERB} by [\d.]+ mb/d [^.]{{0,40}}? and ([\d.]+) mb/d on average in (20\d\d)"):
        m = re.search(pat, text)
        if m:
            d["demand"] = {"year": int(m.group(3)), "chg": _sign(m.group(1)) * float(m.group(2))}
            lvl = re.match(r",? to ([\d.]+) mb/d", text[m.end(): m.end() + 30])
            lvl = lvl or re.search(rf"(?:drop|decline|fall|increase|rise|growth) in global (?:oil )?demand of {m.group(2)} mb/d,? to ([\d.]+) mb/d", text)
            if lvl:
                d["demand"]["level"] = float(lvl.group(1))
            n = re.search(rf"before {VERB} by ([\d.]+) mb/d in (20\d\d)", text[m.end(): m.end() + 200])
            if n:
                d["demand_next"] = {"year": int(n.group(3)), "chg": _sign(n.group(1)) * float(n.group(2))}
            break
    m = re.search(rf"(?:global|world) oil (?:supply|production) {VERB} by (?:a further |another )?([\d.]+) mb/d (?:m-o-m )?to ([\d.]+) mb/d in (\w+)", text, re.I)
    if m:
        d["supply"] = {"chg": _sign(m.group(1)) * float(m.group(2)), "level": float(m.group(3)), "month": m.group(4)}
    m = re.search(rf"refinery runs {VERB} by ([\d.]+) mb/d m-o-m to (?:a [\w -]+ of )?([\d.]+) mb/d in (\w+)", text, re.I)
    if m:
        d["runs"] = {"chg": _sign(m.group(1)) * float(m.group(2)), "level": float(m.group(3)), "month": m.group(4)}
    m = re.search(rf"(?:observed|global) (?:oil )?inventories {VERB} by (?:a further |another )?([\d.]+) mb(?: in (\w+))?", text, re.I)
    if m:
        d["stocks"] = {"chg": _sign(m.group(1)) * float(m.group(2)), "month": m.group(3)}
    m = (re.search(r"Dated[^.]{0,200}?\$([\d.]+)/bbl at the time of writing", text)
         or re.search(r"At the time of writing, (?:North Sea )?Dated was trading (?:around|at|near) \$([\d.]+)/bbl", text))
    if m:
        d["dated"] = float(m.group(1))
    d["iran"] = [clip(s, 200) for s in re.split(r"(?<=[.!?])\s+", text)
                 if len(s) >= 60 and re.search(r"\bIranian\b|\bIran['’]s\b", s)][:2]   # Iranian supply/exports, not "the war in Iran"
    return d


def _month_en_to_ru(name: str) -> str:
    n = name.lower()
    return ru_month(MONTHS_EN.index(n) + 1) if n in MONTHS_EN else name


def _ru3(name: str) -> str:
    n = name.lower()
    return MONTHS_RU3[MONTHS_EN.index(n)] if n in MONTHS_EN else name


def format_message(d: dict, month: int, year: int, url: str, hist: list[tuple[int, int, dict]] | None = None) -> str:
    """hist: earlier reports, oldest first, as (year, month, parsed page)."""
    reps = (hist or []) + [(year, month, d)]
    by_report = lambda get: [(MONTHS_RU3[m - 1], get(r)) for _, m, r in reps]
    when = dt.date.fromisoformat(d["published"]).strftime("%d.%m") if d.get("published") else ""
    lines = [f"🌍 <b>МЭА · Oil Market Report, {ru_month(month, year)}</b>" + (f" ({when})" if when else "")]
    if "demand" in d:
        yr = d["demand"]["year"]
        lvl = d["demand"].get("level")
        s = f"Спрос {yr}: " + (f"<b>{num(lvl, 1)} млн б/с</b> ({signed(d['demand']['chg'], 1)} г/г)" if lvl
                               else f"<b>{signed(d['demand']['chg'], 1)} млн б/с г/г</b>")
        if "demand_next" in d:
            s += f" · {d['demand_next']['year']}: {signed(d['demand_next']['chg'], 1)}"
        lines.append(s)
        pts = by_report(lambda r: r["demand"]["chg"] if r.get("demand", {}).get("year") == yr else None)
        if sum(v is not None for _, v in pts) > 1:
            lines.append(f"   прогноз г/г на {yr} в отчётах: " + trend(pts, 1, show_delta=False, sign=True))
    if "supply" in d:
        # earlier months as first reported; the m/m change is the IEA's own (it includes later revisions)
        pts = [(_ru3(r["supply"]["month"]), r["supply"]["level"]) for _, _, r in reps if "supply" in r]
        lines.append("Предложение, млн б/с: " + trend(pts, 1, show_delta=False) + f" ({signed(d['supply']['chg'], 1)} м/м)")
    if "runs" in d:
        lines.append(f"Переработка, {_month_en_to_ru(d['runs']['month'])}: {num(d['runs']['level'], 1)} млн б/с ({signed(d['runs']['chg'], 1)} м/м)")
    if "stocks" in d:
        pts = [(_ru3(r["stocks"].get("month") or MONTHS_EN[(m - 2) % 12]), r["stocks"]["chg"]) for _, m, r in reps if "stocks" in r]
        if len(pts) > 1:
            lines.append("Запасы в мире, изменение за месяц, млн барр.: " + trend(pts, 0, show_delta=False, sign=True))
        else:
            lines.append(f"Запасы: {signed(d['stocks']['chg'], 0)} млн барр. за месяц")
    if "dated" in d:
        pts = by_report(lambda r: r.get("dated"))
        if sum(v is not None for _, v in pts) > 1:
            lines.append("Brent Dated на дату отчёта, $/барр: " + trend(pts, 0))
        else:
            lines.append(f"Brent Dated: ${num(d['dated'], 0)}/барр на дату отчёта")
    for s in d.get("iran", []):
        lines.append(f"🇮🇷 <i>{esc(s)}</i>")
    lines.append(link(url, "Отчёт (открытая часть)"))
    return "\n".join(lines)


def _ym(y: int, m: int, back: int) -> tuple[int, int]:
    k = y * 12 + (m - 1) - back
    return k // 12, k % 12 + 1


def _report(ctx: Context, y: int, m: int) -> dict | None:
    """Parsed report page, cached in the state; None if the page does not exist."""
    cache = ctx.state.get(SRC, "reports", {})
    key = f"{y}-{m:02d}"
    if key in cache:
        return cache[key]
    r = http_get(URL.format(m=MONTHS_EN[m - 1], y=y), ok_status=(404,))
    if r.status_code == 404:
        return None
    d = parse_page(r.text)
    cache[key] = {k: v for k, v in d.items() if k != "iran"}
    ctx.state.set(SRC, "reports", dict(sorted(cache.items())[-12:]))
    return d


def collect(ctx: Context):
    t = ctx.today
    for back in (0, 1):  # this month's report, else last month's
        y, m = _ym(t.year, t.month, back)
        url = URL.format(m=MONTHS_EN[m - 1], y=y)
        key = f"{y}-{m:02d}"
        if ctx.state.is_seen(SRC, key):
            return
        r = http_get(url, ok_status=(404,))
        if r.status_code == 404:
            continue
        d = parse_page(r.text)
        if not any(k in d for k in ("demand", "supply", "stocks")):
            raise ValueError("IEA page layout changed: no headline numbers found")
        hist = []
        for b in range(ctx.config.get("history_points", 3) - 1, 0, -1):
            hy, hm = _ym(y, m, b)
            try:
                h = _report(ctx, hy, hm)
            except Exception:
                h = None
            if h:
                hist.append((hy, hm, h))
        yield Message(SRC, key, format_message(d, m, y, url, hist), url, {k: v for k, v in d.items() if k != "iran"})
        return
