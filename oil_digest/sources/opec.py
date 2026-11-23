"""OPEC Monthly Oil Market Report: crude production by country based on secondary sources (tb/d).

opec.org refuses automated requests (HTTP 403). The module does not work around that:
it checks the site once, and if blocked it reads the newest MOMR PDF the user drops into `inbox/`
(any file name containing "momr", case-insensitive). Without a file it sends one reminder per month
from the 12th (MOMR usually comes out mid-month).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..core import Blocked, Context, Message, delta, esc, get_text, num, ru_month, trend, MONTHS_EN3, MONTHS_RU3

MOMR_PAGE = "https://www.opec.org/opec_web/en/publications/338.htm"
SRC = "opec"

COUNTRIES = {  # name in MOMR table -> (flag, Russian name)
    "Algeria": ("🇩🇿", "Алжир"), "Congo": ("🇨🇬", "Конго"), "Equatorial Guinea": ("🇬🇶", "Экв. Гвинея"),
    "Gabon": ("🇬🇦", "Габон"), "IR Iran": ("🇮🇷", "Иран"), "Iran": ("🇮🇷", "Иран"), "Iraq": ("🇮🇶", "Ирак"),
    "Kuwait": ("🇰🇼", "Кувейт"), "Libya": ("🇱🇾", "Ливия"), "Nigeria": ("🇳🇬", "Нигерия"),
    "Saudi Arabia": ("🇸🇦", "Сауд. Аравия"), "UAE": ("🇦🇪", "ОАЭ"), "Venezuela": ("🇻🇪", "Венесуэла"),
}
ALWAYS = ("Иран", "Ирак", "Сауд. Аравия")
NUM = r"-?\d[\d,]*\.?\d*"


def parse_secondary_sources(text: str) -> dict:
    """Parse the 'OPEC crude oil production based on secondary sources, tb/d' table from MOMR text.
    Rows look like: 'Saudi Arabia 9,013 9,213 ... 8,412 8,725 313' -> latest month = second-to-last, change = last."""
    i = text.find("based on secondary sources")
    if i < 0:
        raise ValueError("MOMR: secondary-sources table not found")
    block = text[i: i + 4000]
    hdr = re.findall(r"\b([A-Z][a-z]{2})(\d{2})\b", block[:600])
    if not hdr:
        raise ValueError("MOMR: month header not found")
    mon, yy = hdr[-1]
    # monthly columns (e.g. Aug26 Sep26 Oct26), oldest first; quarters like 3Q26 do not match the pattern
    cols = [(MONTHS_EN3.index(a.lower()) + 1, 2000 + int(b)) for a, b in hdr if a.lower() in MONTHS_EN3]
    out = {"month": MONTHS_EN3.index(mon.lower()) + 1, "year": 2000 + int(yy), "rows": {}, "cols": cols}
    names = sorted(list(COUNTRIES) + ["Total OPEC"], key=len, reverse=True)
    for line in block.splitlines():
        line = line.strip()
        for n in names:
            if line.startswith(n + " "):
                vals = [float(v.replace(",", "")) for v in re.findall(NUM, line[len(n):])]
                if len(vals) >= 2:
                    k = len(cols)
                    hist = vals[-(k + 1):-1] if k and len(vals) >= k + 1 else [vals[-2]]
                    out["rows"][n] = {"tbd": vals[-2], "chg": vals[-1], "hist": hist}
                break
    if "Total OPEC" not in out["rows"] or len(out["rows"]) < 6:
        raise ValueError("MOMR: too few rows parsed")
    return out


def _pdf_text(path: Path) -> str:
    import pdfplumber  # optional dependency
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages if "secondary sources" in (p.extract_text() or ""))


def format_message(d: dict, source_note: str) -> str:
    rows = {COUNTRIES[k][1]: (COUNTRIES[k][0], v) for k, v in d["rows"].items() if k in COUNTRIES}
    tot = d["rows"]["Total OPEC"]
    labels = [MONTHS_RU3[m - 1] for m, _ in d.get("cols", [])][-len(tot.get("hist", [])):] or [""]
    series = lambda v: list(zip(labels, [x / 1000 for x in v.get("hist", [v["tbd"]])]))
    lines = [f"🛢 <b>ОПЕК MOMR · {ru_month(d['month'], d['year'])} · добыча по вторичным источникам</b>"]
    if len(tot.get("hist", [])) > 1:
        lines.append("Вся ОПЕК, млн б/с: " + trend(series(tot), 2))
        lines.append(f"<i>по странам: {' → '.join(labels)}, млн б/с</i>")
    else:
        lines.append(f"Вся ОПЕК: <b>{num(tot['tbd'] / 1000, 2)} млн б/с</b> ({delta(tot['chg'] / 1000, 2)} м/м)")
    movers = sorted(rows.items(), key=lambda kv: -abs(kv[1][1]["chg"]))
    show = [k for k, _ in movers[:3]] + [k for k in ALWAYS if k in rows]
    shown = []
    for name in show:
        if name in shown:
            continue
        flag, v = rows[name]
        if len(v.get("hist", [])) > 1:
            body = trend([("", x) for _, x in series(v)], 2)
        else:
            body = f"{num(v['tbd'] / 1000, 2)} {delta(v['chg'] / 1000, 2)}"
        lines.append(f"{flag} {esc(name)}: {body}")
        shown.append(name)
    lines.append(f"<i>{esc(source_note)}</i>")
    return "\n".join(lines)


def collect(ctx: Context):
    try:
        get_text(MOMR_PAGE)
        blocked = False          # site answered: still no stable PDF link format to rely on, so use inbox as well
    except Blocked:
        blocked = True
    pdfs = sorted((p for p in ctx.inbox.glob("*.pdf") if "momr" in p.name.lower()), key=lambda p: p.stat().st_mtime)
    for p in pdfs[-1:]:
        h = hashlib.sha1(p.read_bytes()).hexdigest()[:12]
        d = parse_secondary_sources(_pdf_text(p))
        key = f"{d['year']}-{d['month']:02d}"
        if ctx.state.is_seen(SRC, key):
            continue
        yield Message(SRC, key, format_message(d, f"Из файла {p.name}"), None,
                      {"total_tbd": d["rows"]["Total OPEC"]["tbd"], "file_sha1": h})
        return
    t = ctx.today
    want_key = f"manual:{t.year}-{t.month:02d}"
    py, pm = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)   # MOMR of month M reports month M-1
    have_this_month = ctx.state.is_seen(SRC, f"{py}-{pm:02d}")
    if t.day >= 12 and not have_this_month and not ctx.state.is_seen(SRC, want_key):
        why = "opec.org не пускает автоматические запросы" if blocked else "файла отчёта нет"
        yield Message(SRC, want_key,
                      "🛢 <b>ОПЕК MOMR</b>: нужен ручной шаг.\n"
                      f"{esc(why)}. Скачайте свежий MOMR (PDF) с {MOMR_PAGE} и положите в папку <code>inbox/</code> "
                      "с «momr» в имени — сводка придёт при следующем запуске.", MOMR_PAGE)
