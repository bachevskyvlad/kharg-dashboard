"""
berth_v2_page.py — Generate berth occupancy v2 HTML page.

Reads data/berth_occupancy.json (v2) and diagnostics/berth_v4/validation_report.txt,
generates a self-contained HTML page with:
  - 3 metric cards
  - Previous/Latest pass state (class-coloured boxes)
  - 12-month heatmap (rows=berth/side, cols=passes, color=class)
  - Visit list table
  - Attribution footer

Output: diagnostics/berth_v4/berth_occupancy_v2.html
"""

import json, sys, os
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import defaultdict

ROOT       = Path(__file__).parent.parent
JSON_IN    = ROOT / "data" / "berth_occupancy.json"
SNAPS_JSON = ROOT / "data" / "berth_snapshots.json"
OUT_HTML   = ROOT / "marketing" / "berths.html"


def _cls_color(cls):
    return {"VLCC": "#f87171", "SuezAframax": "#fbbf24"}.get(cls, "#94a3b8")


def _fmt_date(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        return d or "—"


def main():
    if not JSON_IN.exists():
        sys.exit(f"FATAL: {JSON_IN} not found — run berth_v4_history.py first")

    data = json.loads(JSON_IN.read_text())

    # Load snapshots (optional — provides satellite image URLs)
    snaps_data = {}
    if SNAPS_JSON.exists():
        try:
            snaps_data = json.loads(SNAPS_JSON.read_text())
        except Exception:
            pass

    berths   = data.get("berths", ["sea_island", "east_t_head", "southeast_pier"])
    passes   = data.get("passes", [])
    loadings = data.get("loadings", [])
    visits   = data.get("visits", [])
    metrics  = data.get("metrics", {})
    generated = data.get("generated", "")
    version   = data.get("version", "")

    # Sort
    passes  = sorted(passes,  key=lambda p: p.get("date", ""))
    loadings= sorted(loadings, key=lambda l: l.get("date", ""))
    visits  = sorted(visits,   key=lambda v: v.get("start_date", ""))

    # ── Metrics cards ──────────────────────────────────────────────────────────
    l4w     = metrics.get("loadings_4w", {})
    l4w_py  = metrics.get("loadings_4w_py", {})
    last_ship    = metrics.get("last_ship")
    days_since   = metrics.get("days_since_loading")
    avg_days     = metrics.get("avg_visit_days_2025", {})

    l4w_total    = l4w.get("total", 0) if l4w else 0
    l4w_py_total = l4w_py.get("total", 0) if l4w_py else 0
    delta_pct    = ""
    if l4w_py_total:
        d = (l4w_total - l4w_py_total) / l4w_py_total * 100
        delta_pct = f"{'▲' if d >= 0 else '▼'} {abs(d):.0f}% vs year ago"

    card1_title  = "Loadings last 4 weeks"
    card1_val    = str(l4w_total)
    card1_sub    = f"{l4w.get('VLCC',0)} VLCC  {l4w.get('SuezAframax',0)} smaller  {delta_pct}"

    card2_title  = "Last ship"
    if last_ship:
        card2_val = last_ship.get("class", "—")
        card2_sub = (f"{_fmt_date(last_ship.get('date',''))} · {last_ship.get('berth','').replace('_',' ')} · "
                     f"{last_ship.get('len_m','?')}m")
    else:
        card2_val, card2_sub = "—", "no data"

    card3_title  = "Days since last loading"
    card3_val    = str(days_since) if days_since is not None else "—"
    avg_str      = "  ".join(f"{b.replace('_',' ')[:8]} {v}d" for b, v in avg_days.items() if v)
    card3_sub    = f"Avg visit 2025: {avg_str}" if avg_str else ""

    # ── Previous / Latest pass ─────────────────────────────────────────────────
    def _pass_summary(p):
        rows = []
        for b in berths:
            bd = p.get(b, {})
            det = bd.get("det", "—")
            hulls = bd.get("hulls", [])
            if hulls:
                parts = []
                for h in hulls:
                    cls  = h.get("class", "smaller")
                    lm   = h.get("len_m", 0)
                    arr  = "arrived" if h.get("arrived") else "same"
                    parts.append({"cls": cls, "len_m": lm, "arrived": arr,
                                  "color": _cls_color(cls)})
                rows.append({"berth": b, "det": det, "hulls": parts})
            else:
                rows.append({"berth": b, "det": det, "hulls": []})
        vlcc_n  = sum(1 for r in rows for h in r["hulls"] if h["cls"] == "VLCC")
        small_n = sum(1 for r in rows for h in r["hulls"] if h["cls"] != "VLCC")
        total_n = vlcc_n + small_n
        return rows, total_n, vlcc_n, small_n

    prev_pass   = passes[-2] if len(passes) >= 2 else (passes[-1] if passes else {})
    latest_pass = passes[-1] if passes else {}

    prev_rows,   p_tot, p_vlcc, p_sm = _pass_summary(prev_pass)
    latest_rows, l_tot, l_vlcc, l_sm = _pass_summary(latest_pass)

    def _hull_label(h, berth_key):
        cls = h["cls"]
        arr = h["arrived"]
        if berth_key == "southeast_pier":
            return f'{cls} · size n/a · {arr}'
        lm = h["len_m"]
        return f'{cls} · {lm:.0f}m · {arr}'

    def _render_pass_panel(pass_obj, rows, tot, vlcc, sm, label, snap_key):
        d = pass_obj.get("date", "?")
        orb = pass_obj.get("orbit", "?")
        snap = snaps_data.get(snap_key, {})
        img_url = snap.get("url") if snap else None
        lines = [f'<div class="pass-panel"><div class="pass-header">'
                 f'<span class="pass-label">{label}</span>'
                 f'<span class="pass-date">{_fmt_date(d)} orbit {orb}</span>'
                 f'</div>']
        if img_url:
            lines.append(f'<img src="{img_url}" alt="{label} SAR snapshot" class="snap-img">')
        for r in rows:
            b = r["berth"].replace("_", " ")
            bk = r["berth"]
            if r["hulls"]:
                boxes = "".join(
                    f'<span class="hull-box" style="border-color:{h["color"]};color:{h["color"]}">'
                    f'{_hull_label(h, bk)}</span>'
                    for h in r["hulls"]
                )
                lines.append(f'<div class="berth-row"><b>{b}</b>{boxes}</div>')
            else:
                lines.append(f'<div class="berth-row empty"><b>{b}</b>'
                              f'<span class="empty-label">empty</span></div>')
        lines.append(f'<div class="pass-footer">At berths: {tot} '
                     f'(VLCC {vlcc}, smaller {sm})</div>')
        lines.append('</div>')
        return "\n".join(lines)

    prev_html   = _render_pass_panel(prev_pass,   prev_rows,   p_tot, p_vlcc, p_sm, "Previous pass",  "prev")
    latest_html = _render_pass_panel(latest_pass, latest_rows, l_tot, l_vlcc, l_sm, "Latest pass",    "latest")

    # ── 12-month heatmap ───────────────────────────────────────────────────────
    today_dt  = date.today()
    hm_start  = date(today_dt.year - 1, today_dt.month, 1)
    hm_passes = [p for p in passes if p.get("date", "") >= hm_start.isoformat()]

    # Rows = (berth, side), cols = pass dates
    berth_sides = [(b, s) for b in berths for s in [1, -1]]
    pass_dates  = [p["date"] for p in hm_passes]

    # For each (berth, side, pass_date), find class if hull present
    hm_data: dict = {}
    for p in hm_passes:
        for b in berths:
            bd = p.get(b, {})
            for h in bd.get("hulls", []):
                hm_data[(b, h.get("side", 1), p["date"])] = {
                    "cls": h.get("class", "smaller"),
                    "len_m": h.get("len_m", 0),
                    "visit_id": h.get("visit_id", ""),
                }

    cell_w, cell_h = 14, 20
    n_cols = max(len(pass_dates), 1)
    n_rows = len(berth_sides)
    svg_w  = n_cols * cell_w + 120
    svg_h  = n_rows * cell_h + 40

    hm_cells = []
    for ri, (b, s) in enumerate(berth_sides):
        for ci, pd in enumerate(pass_dates):
            x = 120 + ci * cell_w
            y = ri * cell_h
            cell = hm_data.get((b, s, pd))
            if cell:
                fill = _cls_color(cell["cls"])
                tip  = f"{pd} {b} side={'port' if s>0 else 'stbd'}: {cell['cls']} {cell['len_m']:.0f}m {cell['visit_id']}"
            else:
                fill = "#1e293b"
                tip  = f"{pd} {b} side={'port' if s>0 else 'stbd'}: empty"
            hm_cells.append(f'<rect x="{x}" y="{y}" width="{cell_w-1}" height="{cell_h-1}" '
                            f'fill="{fill}" rx="1"><title>{tip}</title></rect>')

    # Row labels
    for ri, (b, s) in enumerate(berth_sides):
        y = ri * cell_h + cell_h * 0.7
        label = b.replace("_", " ") + (" +" if s > 0 else " −")
        hm_cells.insert(0, f'<text x="118" y="{y}" text-anchor="end" '
                         f'font-size="9" fill="var(--tx-muted)">{label}</text>')

    # Column labels (every 4th pass)
    for ci, pd in enumerate(pass_dates):
        if ci % 4 == 0:
            x = 120 + ci * cell_w + cell_w / 2
            y = n_rows * cell_h + 12
            hm_cells.append(f'<text x="{x}" y="{y}" text-anchor="middle" '
                            f'font-size="8" fill="var(--tx-muted)">{pd[5:]}</text>')

    hm_svg = (f'<svg viewBox="0 0 {svg_w} {svg_h}" xmlns="http://www.w3.org/2000/svg" '
              f'style="width:100%;max-width:{svg_w}px;overflow:visible">'
              + "\n".join(hm_cells) + "</svg>")

    # ── Visit list ─────────────────────────────────────────────────────────────
    visit_rows_html = []
    if visits:
        # Group by month; collapse empty weeks
        prev_month = None
        for v in reversed(visits):  # newest first
            d = v.get("start_date", "")
            mo = d[:7] if d else ""
            if mo != prev_month:
                prev_month = mo
                visit_rows_html.append(
                    f'<tr class="month-sep"><td colspan="5">{mo}</td></tr>'
                )
            b     = v.get("berth", "").replace("_", " ")
            cls   = v.get("class_max", v.get("class", "—"))
            lm    = v.get("len_max", v.get("len_m", "?"))
            days  = v.get("visit_days", "?")
            clr   = _cls_color(cls)
            visit_rows_html.append(
                f'<tr><td>{_fmt_date(d)}</td>'
                f'<td>{b}</td>'
                f'<td style="color:{clr}">{cls}</td>'
                f'<td>{lm:.0f}m' if isinstance(lm, float) else f'<td>{lm}m'
                f'</td><td>{days}d</td></tr>'
            )
    else:
        visit_rows_html.append('<tr><td colspan="5">No visit data</td></tr>')

    visit_table = "\n".join(visit_rows_html)

    # ── Validation report tail ────────────────────────────────────────────────
    val_path = ROOT / "diagnostics" / "berth_v4" / "validation_report.txt"
    val_tail = ""
    if val_path.exists():
        lines = val_path.read_text().splitlines()
        # Show last 60 lines
        val_tail = "\n".join(lines[-60:])

    # ── HTML ───────────────────────────────────────────────────────────────────
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kharg Berth Occupancy</title>
<style>
:root {{
  --bg: #0f172a; --surface: #1e293b; --border: #334155;
  --tx: #f1f5f9; --tx-muted: #94a3b8;
  --vlcc: #f87171; --suez: #fbbf24; --small: #94a3b8;
  --radius: 8px; --gap: 1rem;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--tx); font-family: system-ui, sans-serif;
        font-size: 14px; padding: 1.5rem 1rem; max-width: 960px; margin: 0 auto; }}
h1 {{ font-size: 1.3rem; margin-bottom: .3rem; }}
.meta {{ color: var(--tx-muted); font-size: .75rem; margin-bottom: 1.5rem; }}
h2 {{ font-size: .9rem; text-transform: uppercase; letter-spacing: .06em;
      color: var(--tx-muted); margin: 1.5rem 0 .6rem; }}
/* Cards */
.cards {{ display: flex; gap: var(--gap); flex-wrap: wrap; margin-bottom: 1rem; }}
.card {{ flex: 1 1 220px; background: var(--surface); border: 1px solid var(--border);
         border-radius: var(--radius); padding: 1rem; }}
.card-title {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
               color: var(--tx-muted); margin-bottom: .3rem; }}
.card-val {{ font-size: 2rem; font-weight: 700; font-variant-numeric: tabular-nums; }}
.card-sub {{ font-size: .75rem; color: var(--tx-muted); margin-top: .2rem; }}
/* Pass panels */
.pass-panels {{ display: flex; gap: var(--gap); flex-wrap: wrap; }}
.pass-panel {{ flex: 1 1 320px; background: var(--surface); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 1rem; }}
.pass-header {{ display: flex; justify-content: space-between; align-items: baseline;
                margin-bottom: .6rem; }}
.pass-label {{ font-weight: 600; }}
.pass-date  {{ font-size: .75rem; color: var(--tx-muted); }}
.berth-row  {{ display: flex; align-items: center; gap: .4rem; padding: .2rem 0;
               flex-wrap: wrap; font-size: .8rem; }}
.berth-row b {{ min-width: 110px; color: var(--tx-muted); }}
.hull-box {{ border: 1.5px solid; border-radius: 4px; padding: 1px 6px;
             font-size: .7rem; font-weight: 600; white-space: nowrap; }}
.empty-label {{ color: #475569; font-style: italic; }}
.pass-footer {{ margin-top: .6rem; font-size: .75rem; color: var(--tx-muted); }}
/* Heatmap */
.hm-wrap {{ overflow-x: auto; background: var(--surface);
             border: 1px solid var(--border); border-radius: var(--radius);
             padding: .75rem; }}
/* Legend */
.legend {{ display: flex; gap: 1rem; align-items: center; font-size: .75rem;
           margin-bottom: .4rem; flex-wrap: wrap; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
/* Visit table */
.tbl-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: .8rem; }}
th {{ text-align: left; color: var(--tx-muted); font-weight: 500;
      border-bottom: 1px solid var(--border); padding: .3rem .4rem; }}
td {{ padding: .25rem .4rem; border-bottom: 1px solid #1e293b; }}
tr.month-sep td {{ background: var(--border); color: var(--tx-muted);
                   font-size: .7rem; letter-spacing: .05em; padding: .2rem .4rem; }}
/* Attribution */
.attribution {{ margin-top: 2rem; font-size: .7rem; color: var(--tx-muted);
                border-top: 1px solid var(--border); padding-top: .75rem; }}
/* Snapshot image */
.snap-img {{ width: 100%; border-radius: 4px; margin-bottom: .5rem; display: block; }}
/* SE Pier note */
.se-note {{ font-size: .7rem; color: var(--tx-muted); font-style: italic; margin-top: .3rem; }}
/* Val report */
.val-pre {{ background: #111827; border: 1px solid var(--border); border-radius: 6px;
            padding: .75rem; font-size: .65rem; overflow-x: auto; white-space: pre;
            color: var(--tx-muted); max-height: 400px; overflow-y: auto; }}
details summary {{ cursor: pointer; color: var(--tx-muted); font-size: .8rem;
                   text-transform: uppercase; letter-spacing: .06em; padding: .3rem 0; }}
</style>
</head>
<body>
<h1>Kharg Island — Berth Occupancy</h1>
<div class="meta">Generated {generated} · {version}</div>

<h2>Key metrics</h2>
<div class="cards">
  <div class="card">
    <div class="card-title">{card1_title}</div>
    <div class="card-val">{card1_val}</div>
    <div class="card-sub">{card1_sub}</div>
  </div>
  <div class="card">
    <div class="card-title">{card2_title}</div>
    <div class="card-val">{card2_val}</div>
    <div class="card-sub">{card2_sub}</div>
  </div>
  <div class="card">
    <div class="card-title">{card3_title}</div>
    <div class="card-val">{card3_val}</div>
    <div class="card-sub">{card3_sub}</div>
  </div>
</div>

<h2>Pass state</h2>
<div class="pass-panels">
{prev_html}
{latest_html}
</div>

<h2>12-month heatmap</h2>
<div class="legend">
  <span><span class="legend-dot" style="background:#f87171"></span> VLCC</span>
  <span><span class="legend-dot" style="background:#fbbf24"></span> SuezAframax</span>
  <span><span class="legend-dot" style="background:#94a3b8"></span> smaller</span>
  <span><span class="legend-dot" style="background:#1e293b;border:1px solid #334155"></span> empty</span>
</div>
<div class="hm-wrap">{hm_svg}</div>

<h2>Visit history</h2>
<div class="tbl-wrap">
<table>
<thead><tr>
  <th>Arrived</th><th>Berth</th><th>Class</th><th>Length</th><th>Days at berth</th>
</tr></thead>
<tbody>
{visit_table}
</tbody>
</table>
</div>

<p class="se-note">SE Pier detection is a lower bound — vessel orientation makes hull length unreliable.</p>

<details>
<summary>Detection log</summary>
<pre class="val-pre">{val_tail}</pre>
</details>

<div class="attribution">
  SAR Sentinel-1 · Planetary Computer.
  Class by hull length (±50 m).
  Visits shorter than the gap between passes may be missed — lower bound.
</div>
</body>
</html>
"""

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html)
    print(f"Saved: {OUT_HTML} ({OUT_HTML.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
