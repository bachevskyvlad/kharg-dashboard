"""China Customs (GACC), preliminary release, table (6) "China's Major Imports by Quantity and Value (in USD)".

Row "Crude petroleum oils": quantity in 10,000 tonnes and value in USD million for the month and year-to-date.
Country-by-country crude volumes are not in these tables (only in the Interactive Tables, which are not automated here).
"""
from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from ..core import BBL_PER_TONNE, Context, Message, days_in_month, signed, get_text, link, num, ru_month, trend, MONTHS_EN3, MONTHS_RU3

PRELIM = "http://english.customs.gov.cn/statics/report/preliminary.html"
TABLE_TITLE = "Major Imports by Quantity and Value (in USD)"
SRC = "china"


def _f(s: str) -> float | None:
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def month_links(html: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if tds and TABLE_TITLE in tds[0].get_text(" ", strip=True):
            out = []
            for a in tr.find_all("a"):
                m = a.get_text(strip=True).rstrip(".").lower()[:3]
                if m in MONTHS_EN3:
                    out.append((MONTHS_EN3.index(m) + 1, a["href"]))
            return out
    raise ValueError("China Customs layout changed: table (6) not found")


def parse_month_table(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = next((t for t in soup.find_all(string=re.compile(r"Major Imports by Quantity and Value, \w+ 20\d\d"))), "")
    m = re.search(r"Value, (\w{3})\w* (20\d\d)", title)
    if not m:
        raise ValueError("China Customs layout changed: title not found")
    month, year = MONTHS_EN3.index(m.group(1).lower()) + 1, int(m.group(2))
    pub = re.search(r"(20\d\d)/(\d{1,2})/(\d{1,2})", soup.get_text(" "))
    head = crude = None
    for tr in soup.find_all("tr"):
        c = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if "QTY Unit" in c:
            head = c
        if c and c[0].startswith("Crude petroleum oils"):
            crude = c
    if not crude or len(crude) < 10:
        raise ValueError("China Customs layout changed: crude row not found")
    period = head[head.index("QTY Unit") + 1] if head else str(month)      # "10" or "1-2" (Jan-Feb combined)
    first = int(period.split("-")[0]) if re.match(r"^\d+(-\d+)?$", period) else month
    days = sum(days_in_month(year, k) for k in range(first, month + 1))
    ytd_days = sum(days_in_month(year, k) for k in range(1, month + 1))
    qty, val, qty_ytd, _, qty_ytd_prev, _, yoy_q, _ = (_f(x) for x in crude[2:10])
    t = qty * 1e4
    return {
        "year": year, "month": month, "period": period,
        "mt": t / 1e6, "mbd": t * BBL_PER_TONNE / days / 1e6,
        "usd_bbl": val * 1e6 / (t * BBL_PER_TONNE) if val else None,
        "ytd_mbd": qty_ytd * 1e4 * BBL_PER_TONNE / ytd_days / 1e6, "ytd_yoy_pct": yoy_q,
        "published": f"{pub.group(1)}-{int(pub.group(2)):02d}-{int(pub.group(3)):02d}" if pub else None,
    }


def _label(d: dict) -> str:
    if d["period"] == str(d["month"]):
        return MONTHS_RU3[d["month"] - 1]
    return f"янв–{MONTHS_RU3[d['month'] - 1]}"     # Jan–Feb are released combined


def collect(ctx: Context):
    links = month_links(get_text(PRELIM))
    if not links:
        return
    month, url = links[-1]
    d = parse_month_table(get_text(url))
    key = f"{d['year']}-{d['month']:02d}"
    if ctx.state.is_seen(SRC, key):
        return
    n = ctx.config.get("history_points", 3)
    hist = []                                   # earlier months of the same year, from the same release page
    for _, u in links[-n:-1]:
        try:
            hist.append(parse_month_table(get_text(u)))
        except Exception:
            pass
    pts = hist + [d]
    lines = ["🇨🇳 <b>Таможня КНР · импорт сырой нефти</b>",
             f"{ru_month(d['month'], d['year']).capitalize() if d['period'] == str(d['month']) else 'Янв–' + ru_month(d['month'], d['year'], short=True)}: "
             f"<b>{num(d['mbd'], 2)} млн б/с</b> ({num(d['mt'], 1)} млн т)"]
    if hist:
        lines.append("Динамика, млн б/с: " + trend([(_label(p), p["mbd"]) for p in pts], 2))
    lines.append(f"С начала года: {num(d['ytd_mbd'], 2)} млн б/с, {signed(d['ytd_yoy_pct'], 1)}% г/г")
    if d["usd_bbl"]:
        prices = [(_label(p), p["usd_bbl"]) for p in pts if p["usd_bbl"]]
        lines.append("Цена импорта, $/барр: " + (trend(prices, 0) if len(prices) > 1 else f"~{num(d['usd_bbl'], 0)}"))
    lines.append("<i>По странам в этой таблице данных нет. Иранская нефть в статистике КНР обычно проходит как малайзийская.</i>")
    tail = link(url, "Таблица")
    if d["published"]:
        tail += f" · опубл. {dt.date.fromisoformat(d['published']).strftime('%d.%m.%Y')}"
    lines.append(tail)
    yield Message(SRC, key, "\n".join(lines), url, {k: d[k] for k in ("mbd", "mt", "usd_bbl", "ytd_mbd", "ytd_yoy_pct")})
