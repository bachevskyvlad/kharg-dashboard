from __future__ import annotations

import datetime as dt
import html
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

log = logging.getLogger("oil_digest")

USER_AGENT = "oil-digest/0.1 (personal research script)"
TG_LIMIT = 4096
BBL_PER_TONNE = 7.33  # average crude, used where a source reports tonnes

MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
MONTHS_EN3 = [m[:3] for m in MONTHS_EN]
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
             "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_RU_DAT = ["январю", "февралю", "марту", "апрелю", "маю", "июню", "июлю",
                 "августу", "сентябрю", "октябрю", "ноябрю", "декабрю"]
MONTHS_RU3 = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


class Blocked(Exception):
    """The site refused an automated request (401/403/429). Never work around it."""


# ---------------------------------------------------------------- HTTP (polite: one request per host every few seconds)
_last_call: dict[str, float] = {}


def http_get(url: str, *, headers: dict | None = None, min_interval: float = 3.0,
             timeout: int = 60, ok_status: tuple[int, ...] = ()) -> requests.Response:
    host = url.split("/")[2]
    wait = min_interval - (time.monotonic() - _last_call.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    r = requests.get(url, headers=h, timeout=timeout)
    _last_call[host] = time.monotonic()
    if r.status_code in (401, 403, 429):
        raise Blocked(f"HTTP {r.status_code} for {url}")
    if r.status_code != 304 and r.status_code not in ok_status:
        r.raise_for_status()
    return r


def get_text(url: str, **kw) -> str:
    r = http_get(url, **kw)
    r.encoding = r.encoding or "utf-8"
    return r.text


# ---------------------------------------------------------------- formatting helpers
def esc(s: Any) -> str:
    return html.escape(str(s), quote=False)


def num(x: float | None, nd: int = 1) -> str:
    """Russian style: 1 234,5"""
    if x is None:
        return "—"
    s = f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s


def delta(x: float | None, nd: int = 1, suffix: str = "") -> str:
    if x is None:
        return ""
    if round(x, nd) == 0:
        return "="
    arrow = "▲" if x > 0 else "▼"
    return f"{arrow}{num(abs(x), nd)}{suffix}"


def signed(x: float | None, nd: int = 1) -> str:
    if x is None:
        return "—"
    return ("+" if x > 0 else "−" if x < 0 else "") + num(abs(x), nd)


def trend(points: list[tuple[str, float | None]], nd: int = 1, prefix: str = "", suffix: str = "",
          show_delta: bool = True, sign: bool = False) -> str:
    """Last few values, oldest first, newest in bold, plus the change from the previous one:
    [("авг", 49.7), ("сен", 50.8), ("окт", 15.8)] -> 'авг 49,7 → сен 50,8 → <b>окт 15,8</b> (▼35,0)'.
    Points with None are dropped; an empty label prints the value alone."""
    pts = [(lbl, v) for lbl, v in points if v is not None]
    if not pts:
        return "—"
    fmt = lambda lbl, v: (f"{lbl} " if lbl else "") + f"{prefix}{(signed if sign else num)(v, nd)}{suffix}"
    parts = [esc(fmt(l, v)) for l, v in pts[:-1]] + [f"<b>{esc(fmt(*pts[-1]))}</b>"]
    s = " → ".join(parts)
    if show_delta and len(pts) > 1:
        s += f" ({delta(pts[-1][1] - pts[-2][1], nd)})"
    return s


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'


def clip(s: str, n: int = 220) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def ru_month(m: int, y: int | None = None, short: bool = False) -> str:
    name = (MONTHS_RU3 if short else MONTHS_RU)[m - 1]
    return f"{name} {y}" if y else name


def days_in_month(y: int, m: int) -> int:
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - dt.date(y, m, 1)).days


# ---------------------------------------------------------------- message and state
@dataclass
class Message:
    source: str                    # "uani", "china", "opec", "jodi", "iea", "system"
    key: str                       # unique per source: what this message is about (e.g. "2026-10")
    text: str                      # Telegram HTML (parse_mode="HTML")
    url: str | None = None
    payload: dict = field(default_factory=dict)   # numbers behind the text, stored on commit

    def __post_init__(self):
        if len(self.text) > TG_LIMIT:
            self.text = self.text[: TG_LIMIT - 1] + "…"


class State:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.execute("create table if not exists seen(source text, key text, payload text, ts text, primary key(source, key))")
        self.db.execute("create table if not exists kv(source text, name text, value text, primary key(source, name))")
        self.db.commit()

    def is_seen(self, source: str, key: str) -> bool:
        return self.db.execute("select 1 from seen where source=? and key=?", (source, key)).fetchone() is not None

    def mark(self, source: str, key: str, payload: dict | None = None):
        self.db.execute("insert or replace into seen values(?,?,?,?)",
                        (source, key, json.dumps(payload or {}, ensure_ascii=False), dt.datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def last_payload(self, source: str, prefix: str = "") -> dict | None:
        row = self.db.execute("select payload from seen where source=? and key like ? order by ts desc limit 1",
                              (source, prefix + "%")).fetchone()
        return json.loads(row[0]) if row else None

    def last_sent_at(self) -> dt.datetime | None:
        row = self.db.execute("select max(ts) from seen where source!='system'").fetchone()
        return dt.datetime.fromisoformat(row[0]) if row and row[0] else None

    def get(self, source: str, name: str, default: Any = None) -> Any:
        row = self.db.execute("select value from kv where source=? and name=?", (source, name)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, source: str, name: str, value: Any):
        self.db.execute("insert or replace into kv values(?,?,?)", (source, name, json.dumps(value, ensure_ascii=False)))
        self.db.commit()


@dataclass
class Context:
    state: State
    inbox: Path
    config: dict
    today: dt.date


SourceFn = Callable[[Context], Iterable[Message]]


class Digest:
    """Runs every source, returns only messages that were not sent yet. Call commit(msg) after a successful send."""

    def __init__(self, state_path: str | Path = "oil_digest_state.sqlite", inbox_dir: str | Path = "inbox",
                 sources: list[str] | None = None, config: dict | None = None, today: dt.date | None = None,
                 postprocess: Callable[[Message], Message] | None = None, use_schedule: bool = True):
        """postprocess: optional hook applied to every message, e.g. an LLM call that translates the
        English quotes to Russian or shortens the text further. Must return a Message (same source/key).
        use_schedule: check each source only on its release days (see schedule.py). False = check everything."""
        from .sources import ALIASES, REGISTRY
        self.state = State(state_path)
        self.inbox = Path(inbox_dir)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.config = config or {}
        self.today = today or dt.date.today()
        names = [x for n in (sources or list(REGISTRY)) for x in ALIASES.get(n, [n])]
        self.sources: dict[str, SourceFn] = {n: REGISTRY[n] for n in names}
        self.postprocess = postprocess
        self.use_schedule = use_schedule

    def due_sources(self) -> list[str]:
        from .schedule import is_due
        return [n for n in self.sources
                if not self.use_schedule or is_due(n, self.today, self.config.get("schedule"))]

    def collect(self) -> list[Message]:
        ctx = Context(self.state, self.inbox, self.config, self.today)
        out: list[Message] = []
        due = self.due_sources()
        log.info("sources due today (%s): %s", self.today, ", ".join(due) or "none")
        for name, fn in self.sources.items():
            if name not in due:
                continue
            try:
                msgs = [m for m in fn(ctx) if not self.state.is_seen(m.source, m.key)]
                self.state.set(name, "fail_count", 0)
                out.extend(msgs)
            except Blocked as e:
                log.warning("%s blocked: %s", name, e)
                out.extend(self._failure(name, f"сайт не пускает автоматический запрос ({e})"))
            except Exception as e:  # layout change, network, parsing
                log.exception("%s failed", name)
                out.extend(self._failure(name, f"{type(e).__name__}: {e}"))
        out.extend(self._heartbeat(out))
        if self.postprocess:
            out = [self.postprocess(m) for m in out]
        return out

    def commit(self, msg: Message):
        self.state.mark(msg.source, msg.key, msg.payload)

    # -- a source is reported only after two failed runs in a row, and at most once a day
    def _failure(self, name: str, why: str) -> list[Message]:
        n = self.state.get(name, "fail_count", 0) + 1
        self.state.set(name, "fail_count", n)
        if n < 2:
            return []
        key = f"error:{name}:{self.today.isoformat()}"
        if self.state.is_seen("system", key):
            return []
        return [Message("system", key, f"⚠️ <b>{esc(name)}</b>: не удалось получить данные {n} раз подряд.\n<i>{esc(clip(why, 300))}</i>")]

    def _heartbeat(self, pending: list[Message]) -> list[Message]:
        if self.today.weekday() != 0 or pending:
            return []
        last = self.state.last_sent_at()
        if last and (dt.datetime.now() - last).days < 7:
            return []
        y, w, _ = self.today.isocalendar()
        return [Message("system", f"heartbeat:{y}-W{w:02d}", "🟢 За неделю нового не вышло. Источники проверяются ежедневно.")]
