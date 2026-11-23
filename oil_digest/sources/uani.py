"""UANI Tanker Tracker (monthly Iran export estimate) and Iran Shipping Updates (several a week).

Monthly: https://www.unitedagainstnucleariran.com/tanker-tracker shows the latest month and its total
("October Total: 15806") plus a link to the month's report. Updates: links on /analysis.
"""
from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from ..core import Context, Message, clip, esc, get_text, link, num, ru_month, trend, MONTHS_EN, MONTHS_RU3

BASE = "https://www.unitedagainstnucleariran.com"
TRACKER = BASE + "/tanker-tracker"
UPDATES = BASE + "/iran-shipping-update"   # list of daily Iran Shipping Updates, newest first
MONTH_AJAX = BASE + "/tbf_tanker_tracker/ajax/ajax_chart?nid={nid}"   # what the tracker page itself calls for older months
SRC = "uani"


def collect(ctx: Context):
    yield from _monthly(ctx)
    yield from _updates(ctx)


def collect_monthly(ctx: Context):
    yield from _monthly(ctx)


def collect_updates(ctx: Context):
    yield from _updates(ctx)


# ----------------------------------------------------------------------------- monthly tracker
def _parse_month(s: str) -> tuple[int, int] | None:
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d\d)", s)
    return (MONTHS_EN.index(m.group(1).lower()) + 1, int(m.group(2))) if m else None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", " ".join(text.split())) if len(s.strip()) > 40]


def parse_tracker(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    middle = soup.select_one(".tt-pager-middle")
    ym = _parse_month(middle.get_text(" ") if middle else "")
    total = soup.select_one("#holder-total-bpd")
    t = re.search(r"Total:\s*([\d,]+)", total.get_text() if total else "")
    blog = soup.select_one("#holder-related-blog")
    a = blog.find("a") if blog else None
    pdf = blog.find("a", href=re.compile(r"\.pdf$", re.I)) if blog else None
    date = re.search(r"([A-Z][a-z]+ \d{1,2}, 20\d\d)", blog.get_text(" ") if blog else "")
    if not (ym and t):
        raise ValueError("UANI tracker layout changed: month or total not found")
    return {
        "month": ym[0], "year": ym[1], "bpd": int(t.group(1).replace(",", "")),
        "report_url": BASE + a["href"] if a and a["href"].startswith("/") else (a["href"] if a else TRACKER),
        "pdf_url": pdf["href"] if pdf else None,
        "published": dt.datetime.strptime(date.group(1), "%B %d, %Y").date().isoformat() if date else None,
    }


def month_options(html: str) -> list[str]:
    """Node ids of the months in the tracker's date dropdown, newest first."""
    soup = BeautifulSoup(html, "html.parser")
    sel = soup.select_one("#filter-date-independant")
    return [o["value"] for o in sel.find_all("option")] if sel else []


def parse_report(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("article") or soup
    paras = [p.get_text(" ", strip=True) for p in body.find_all("p")]
    text = " ".join(paras)
    sents = _sentences(text)
    at_line = any(re.search(r"cross(es|ing)? the (declared )?U\.?S\.? blockade line", s) for s in sents)
    picks = []
    for s in sents:  # the most informative sentences: what got out, how much is stuck
        if re.search(r"(?i)standardi[sz]ed|presented in|calculated when|click here|read more", s):
            continue
        if re.search(r"(?i)\b(only one|single shipment|near zero|approximately \d+ tankers|at least \d+ tankers|\d+ million barrels)", s):
            picks.append(clip(s, 200))
        if len(picks) == 2:
            break
    return {"counted_at_blockade_line": at_line, "highlights": picks}


def _monthly(ctx: Context):
    page = get_text(TRACKER)
    d = parse_tracker(page)
    key = f"monthly:{d['year']}-{d['month']:02d}"
    if ctx.state.is_seen(SRC, key):
        return
    d.update(parse_report(get_text(d["report_url"])))
    n = ctx.config.get("history_points", 3)
    hist = []
    for nid in month_options(page)[1:n]:          # the months before the newest one
        try:
            hist.append(parse_tracker(get_text(MONTH_AJAX.format(nid=nid))))
        except Exception:
            pass
    pts = sorted(hist, key=lambda x: (x["year"], x["month"])) + [d]
    lines = [f"🇮🇷 <b>UANI · экспорт нефти Ирана</b>",
             f"{ru_month(d['month'], d['year']).capitalize()}: <b>{num(d['bpd'] / 1000, 1)} тыс. б/с</b>"]
    if hist:
        lines.append("Динамика, тыс. б/с: " + trend([(MONTHS_RU3[p["month"] - 1], p["bpd"] / 1000) for p in pts], 1))
    if d["counted_at_blockade_line"]:
        lines.append("<i>Считается по танкерам, пересёкшим линию блокады США, а не по погрузкам.</i>")
    for h in d["highlights"]:
        lines.append(f"• <i>{esc(h)}</i>")
    tail = link(d["report_url"], "Отчёт")
    if d.get("pdf_url"):
        tail += " · " + link(d["pdf_url"], "PDF")
    if d.get("published"):
        tail += f" · опубл. {dt.date.fromisoformat(d['published']).strftime('%d.%m.%Y')}"
    lines.append(tail)
    yield Message(SRC, key, "\n".join(lines), d["report_url"], {"bpd": d["bpd"], "month": d["month"], "year": d["year"]})


# ----------------------------------------------------------------------------- shipping updates
def list_updates(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=re.compile(r"/analysis/iran-(war-)?shipping-update-\w")):
        h = a["href"]
        if h not in seen:
            seen.add(h)
            out.append((BASE + h if h.startswith("/") else h, a.get_text(" ", strip=True)))
    return out


def parse_update(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("h1") or soup.find("title")).get_text(" ", strip=True)
    text = " ".join(p.get_text(" ", strip=True) for p in (soup.find("article") or soup).find_all(["p", "li"]))
    text = " ".join(text.split())
    text = re.sub(r"\s+([,.;:)])", r"\1", text)      # get_text(" ") leaves "IMO 123) , which"
    g = lambda pat: (re.search(pat, text) or [None, None])[1]
    date = re.search(r"([A-Z][a-z]+ \d{1,2}, 20\d\d)", title) or re.search(r"([A-Z][a-z]+ \d{1,2}, 20\d\d)", text)
    cands = [s for s in _sentences(text) if re.search(r"(?i)ship-to-ship|\bSTS\b|\bfed\b|transferred", s) and re.search(r"\d", s)]
    sts = next((s for s in cands if "barrels" in s), cands[0] if cands else None)   # prefer the one with ship names and volume
    return {
        "title": title,
        "date": dt.datetime.strptime(date.group(1), "%B %d, %Y").date().isoformat() if date else None,
        "laden": g(r"at least (\d+) tankers laden"),
        "breached": g(r"approximately (\d+) (?:small )?tankers[^.]{0,40}(?:passed|departed|breached)"),
        "no_crude_breach": bool(re.search(r"has not tracked any tanker laden with Iranian crude", text)),
        "transits": g(r"averaged ([\d.]+) vessels per day"),
        "sts": clip(sts, 230) if sts else None,
    }


def _parsed_update(ctx: Context, url: str) -> dict:
    """Parsed updates are cached in the state, so older ones are not downloaded again."""
    cache = ctx.state.get(SRC, "update_cache", {})
    if url not in cache:
        cache[url] = parse_update(get_text(url))
        cache = dict(list(cache.items())[-20:])
        ctx.state.set(SRC, "update_cache", cache)
    return cache[url]


def _upd_label(d: dict) -> str:
    return dt.date.fromisoformat(d["date"]).strftime("%d.%m") if d.get("date") else "?"


def _updates(ctx: Context):
    items = list_updates(get_text(UPDATES))
    if not items:
        raise ValueError("UANI: no shipping updates listed")
    url = items[0][0]                      # only the newest one: daily updates would flood the chat
    if ctx.state.is_seen(SRC, "update:" + url):
        return
    n = ctx.config.get("history_points", 3)
    d = _parsed_update(ctx, url)
    hist = []
    for u, _ in items[1:n]:
        try:
            hist.append(_parsed_update(ctx, u))
        except Exception:
            pass
    pts = list(reversed(hist)) + [d]       # oldest first
    series = lambda k: [(_upd_label(p), float(p[k]) if p.get(k) else None) for p in pts]
    when = dt.date.fromisoformat(d["date"]).strftime("%d.%m.%Y") if d["date"] else ""
    lines = [f"🛰 <b>UANI · Iran Shipping Update {when}</b>"]
    if d["laden"]:
        lines.append("• Гружёных танкеров вдоль берега Ирана, не меньше: " + trend(series("laden"), 0))
    if d["breached"]:
        lines.append(f"• Прошли линию блокады с 14.07: ~{d['breached']} малых танкеров"
                     + ("; с сырой нефтью — ни одного" if d["no_crude_breach"] else ""))
    elif d["no_crude_breach"]:
        lines.append("• Танкеров с сырой нефтью, прошедших линию блокады: ни одного")
    if d["transits"]:
        lines.append("• Ормуз, проходы при содействии США, судов/сут: " + trend(series("transits"), 1))
    if d["sts"]:
        lines.append(f"• Перевалка: <i>{esc(d['sts'])}</i>")
    if hist:
        lines.append("<i>Цепочка — три последних апдейта UANI.</i>")
    lines.append(link(url, "Читать"))
    yield Message(SRC, "update:" + url, "\n".join(lines), url, {"laden": d["laden"], "date": d["date"]})
