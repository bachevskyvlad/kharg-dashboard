"""JODI Oil World Database, primary products, annual CSV for the current year.

Columns: REF_AREA, TIME_PERIOD, ENERGY_PRODUCT, FLOW_BREAKDOWN, UNIT_MEASURE, OBS_VALUE, ASSESSMENT_CODE.
Crude oil (CRUDEOIL): INDPROD production, TOTEXPSB exports, TOTIMPSB imports; unit KBD (else KBBL / days).
A message lists only countries with a new month or a revised number since the last sent message.
"""
from __future__ import annotations

import hashlib
import io
import json

import pandas as pd

from ..core import Context, Message, days_in_month, esc, http_get, link, num, trend, MONTHS_RU3

BASE = "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/"
PAGE = "https://www.jodidata.org/oil/database/data-downloads.aspx"
SRC = "jodi"
FLOWS = {"INDPROD": "добыча", "TOTEXPSB": "экспорт", "TOTIMPSB": "импорт"}
COUNTRIES = {  # ISO2 -> (flag, Russian name); edit via config["jodi_countries"]
    "SA": ("🇸🇦", "Сауд. Аравия"), "IQ": ("🇮🇶", "Ирак"), "AE": ("🇦🇪", "ОАЭ"), "KW": ("🇰🇼", "Кувейт"),
    "IR": ("🇮🇷", "Иран"), "RU": ("🇷🇺", "Россия"), "KZ": ("🇰🇿", "Казахстан"), "NO": ("🇳🇴", "Норвегия"),
    "US": ("🇺🇸", "США"), "BR": ("🇧🇷", "Бразилия"), "CN": ("🇨🇳", "Китай"), "IN": ("🇮🇳", "Индия"),
}


def _csv_url(year: int, current: int) -> str:
    return BASE + (f"primaryyear{year}.csv" if year == current else f"{year}.csv")


def tidy(raw: bytes, countries: list[str]) -> pd.DataFrame:
    J = pd.read_csv(io.BytesIO(raw), dtype=str)
    J = J[(J.ENERGY_PRODUCT == "CRUDEOIL") & J.FLOW_BREAKDOWN.isin(FLOWS) & J.REF_AREA.isin(countries)]
    J = J[J.OBS_VALUE.str.match(r"^-?\d", na=False)].copy()
    J["v"] = J.OBS_VALUE.astype(float)
    kbd = J[J.UNIT_MEASURE == "KBD"][["REF_AREA", "TIME_PERIOD", "FLOW_BREAKDOWN", "v"]]
    kb = J[J.UNIT_MEASURE == "KBBL"][["REF_AREA", "TIME_PERIOD", "FLOW_BREAKDOWN", "v"]].copy()
    kb["v"] = [v / days_in_month(int(p[:4]), int(p[5:7])) for v, p in zip(kb.v, kb.TIME_PERIOD)]
    both = pd.concat([kbd, kb]).drop_duplicates(["REF_AREA", "TIME_PERIOD", "FLOW_BREAKDOWN"], keep="first")
    return both.rename(columns={"REF_AREA": "c", "TIME_PERIOD": "m", "FLOW_BREAKDOWN": "f"})


def snapshot(df: pd.DataFrame) -> dict:
    snap: dict = {}
    for c, m, f, v in df.itertuples(index=False):
        snap.setdefault(c, {}).setdefault(m, {})[f] = round(v, 1)
    return snap


def diff(old: dict, new: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str, str, float, float]]]:
    fresh, revised = [], []
    for c, months in new.items():
        latest = max(months)
        if latest not in old.get(c, {}):
            fresh.append((c, latest))
        for m, flows in months.items():
            for f, v in flows.items():
                o = old.get(c, {}).get(m, {}).get(f)
                if o is not None and abs(v - o) > max(5, 0.01 * abs(o)):
                    revised.append((c, m, f, o, v))
    return fresh, revised


def format_message(new: dict, fresh, revised, names: dict, n: int = 3) -> str:
    lines = ["📊 <b>JODI · сырая нефть, новые данные</b>", f"<i>тыс. б/с · {n} последних месяца по каждой стране</i>"]
    newest = max(max(v) for v in new.values())
    for c, m in sorted(fresh, key=lambda x: list(names).index(x[0]) if x[0] in names else 99):
        flag, name = names.get(c, ("", c))
        months = [k for k in sorted(new[c]) if k <= m][-n:]
        rows = []
        for f, ru in FLOWS.items():
            v = new[c][m].get(f)
            if v is None or v < 100:        # skip tiny flows (e.g. Saudi imports) to keep the message short
                continue
            pts = [(MONTHS_RU3[int(k[5:]) - 1], new[c][k].get(f)) for k in months]
            rows.append(f"   {ru}: " + trend(pts, 0))
        if rows:
            lag = (int(newest[:4]) - int(m[:4])) * 12 + int(newest[5:]) - int(m[5:])
            stale = f" <i>(отстаёт на {lag} мес.)</i>" if lag >= 3 else ""
            lines.append(f"{flag} <b>{esc(name)}</b>{stale}")
            lines.extend(rows)
    if revised:
        r = [f"{names.get(c, ('', c))[1]} {MONTHS_RU3[int(m[5:]) - 1]} {FLOWS[f]} {num(o, 0)}→{num(v, 0)}" for c, m, f, o, v in revised[:5]]
        lines.append("Пересмотры: " + esc("; ".join(r)) + (" …" if len(revised) > 5 else ""))
    lines.append(link(PAGE, "JODI"))
    return "\n".join(lines)


def collect(ctx: Context):
    """Downloads the CSV only when it changed (ETag). The ETag is remembered only after a run that found
    nothing new to send, so a message that failed to send is rebuilt on the next run."""
    names = ctx.config.get("jodi_countries", COUNTRIES)
    y = ctx.today.year
    years = [y - 1, y] if ctx.today.month <= 5 else [y]   # ~2-month lag + 3 months of history
    quiet = ctx.state.get(SRC, "etags_quiet", {})
    frames, etags = [], {}
    for yr in years:
        url = _csv_url(yr, y)
        r = http_get(url, headers={"If-None-Match": quiet[url]} if url in quiet else None, timeout=120)
        if r.status_code == 304:
            etags[url] = quiet[url]
            continue
        etags[url] = r.headers.get("ETag") or hashlib.sha1(r.content).hexdigest()[:12]
        frames.append(tidy(r.content, list(names)))
    if not frames:
        return
    new = snapshot(pd.concat(frames))
    last = ctx.state.last_payload(SRC, "update:") or {}
    fresh, revised = diff(last.get("snapshot", {}), new)
    if not fresh and not revised:
        ctx.state.set(SRC, "etags_quiet", etags)
        return
    key = "update:" + hashlib.sha1(json.dumps(new, sort_keys=True).encode()).hexdigest()[:12]
    yield Message(SRC, key, format_message(new, fresh, revised, names, ctx.config.get("history_points", 3)), PAGE, {"snapshot": new})
