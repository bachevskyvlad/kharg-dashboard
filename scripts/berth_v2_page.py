"""
berth_v2_page.py — Generate berth occupancy product page.

Reads data/berth_occupancy.json (v2) and data/berth_snapshots.json,
generates a full authenticated page at product/berths.html with:
  - 3 metric cards (computed from visits list)
  - Previous/Latest pass state (class-coloured boxes + SAR snapshots)
  - 12-month heatmap (rows=berth/side, cols=passes, color=class)
  - Visit list table
  - Attribution footer
"""

import json, sys
from pathlib import Path
from datetime import datetime, date

ROOT       = Path(__file__).parent.parent
JSON_IN    = ROOT / "data" / "berth_occupancy.json"
SNAPS_JSON = ROOT / "data" / "berth_snapshots.json"
OUT_HTML   = ROOT / "product" / "berths.html"


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

    snaps_data = {}
    if SNAPS_JSON.exists():
        try:
            snaps_data = json.loads(SNAPS_JSON.read_text())
        except Exception:
            pass

    berths   = data.get("berths", ["sea_island", "east_t_head", "southeast_pier"])
    passes   = data.get("passes", [])
    visits   = data.get("visits", [])
    metrics  = data.get("metrics", {})
    generated = data.get("generated", "")
    version   = data.get("version", "")

    passes = sorted(passes, key=lambda p: p.get("date", ""))
    visits = sorted(visits, key=lambda v: v.get("start_date", ""))

    # ── Metrics: compute from visits list (bug 1) ──────────────────────────────
    today_dt = date.today()

    # Last 4 weeks from visits
    cutoff_4w    = (today_dt.replace(day=today_dt.day) - __import__('datetime').timedelta(weeks=4)).isoformat()
    cutoff_4w_py = (today_dt - __import__('datetime').timedelta(weeks=4+52)).isoformat()
    end_4w_py    = (today_dt - __import__('datetime').timedelta(weeks=52)).isoformat()

    l4w_visits   = [v for v in visits if v.get("start_date", "") >= cutoff_4w]
    l4w_py_visits= [v for v in visits if cutoff_4w_py <= v.get("start_date", "") < end_4w_py]

    def _count_by_class(vlist):
        total = len(vlist)
        vlcc  = sum(1 for v in vlist if v.get("class_max", v.get("class")) == "VLCC")
        suez  = sum(1 for v in vlist if v.get("class_max", v.get("class")) == "SuezAframax")
        return total, vlcc, suez

    l4w_total, l4w_vlcc, l4w_suez = _count_by_class(l4w_visits)
    l4w_py_total, _, _ = _count_by_class(l4w_py_visits)

    # Year-ago delta (bug 2)
    if l4w_total == 0 and l4w_py_total == 0:
        delta_pct = "—"
    elif l4w_py_total == 0:
        delta_pct = ""
    elif l4w_total == 0:
        delta_pct = "▼ 100% vs year ago"
    else:
        d = (l4w_total - l4w_py_total) / l4w_py_total * 100
        delta_pct = f"{'▲' if d >= 0 else '▼'} {abs(d):.0f}% vs year ago"

    card1_title = "Loadings last 4 weeks"
    card1_val   = str(l4w_total)
    card1_sub   = f"{l4w_vlcc} VLCC  {l4w_suez} SuezAframax  {delta_pct}"

    # Last ship from visits (bug 1 & 3: format = class · len m · berth · date)
    last_visit = visits[-1] if visits else None
    if last_visit:
        ls_date  = last_visit.get("start_date", "")
        ls_cls   = last_visit.get("class_max", last_visit.get("class", "—"))
        ls_len   = last_visit.get("len_max",   last_visit.get("len_m"))
        ls_berth = last_visit.get("berth", "").replace("_", " ")
        try:
            days_since = (today_dt - datetime.strptime(ls_date, "%Y-%m-%d").date()).days
        except Exception:
            days_since = None
        len_str = f"{ls_len:.0f}m" if isinstance(ls_len, float) else (f"{ls_len}m" if ls_len else "—")
        card2_val = ls_cls
        card2_sub = f"{len_str} · {ls_berth} · {_fmt_date(ls_date)}"
    else:
        card2_val, card2_sub, days_since = "—", "no data", None

    card3_title = "Days since last loading"
    card3_val   = str(days_since) if days_since is not None else "—"
    avg_days    = metrics.get("avg_visit_days_2025", {})
    avg_str     = "  ".join(f"{b.replace('_',' ')[:8]} {v}d" for b, v in avg_days.items() if v)
    card3_sub   = f"Avg visit 2025: {avg_str}" if avg_str else ""

    # ── Previous / Latest pass ─────────────────────────────────────────────────
    def _pass_summary(p):
        rows = []
        for b in berths:
            bd    = p.get(b, {})
            det   = bd.get("det", "—")
            hulls = bd.get("hulls", [])
            if hulls:
                parts = []
                for h in hulls:
                    cls = h.get("class", "smaller")
                    lm  = h.get("len_m", 0)
                    arr = "arrived" if h.get("arrived") else "same"
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
        if berth_key == "southeast_pier":
            return f'{h["cls"]} · size n/a · {h["arrived"]}'
        return f'{h["cls"]} · {h["len_m"]:.0f}m · {h["arrived"]}'

    def _render_pass_panel(pass_obj, rows, tot, vlcc, sm, label, snap_key):
        d   = pass_obj.get("date", "?")
        orb = pass_obj.get("orbit", "?")
        snap    = snaps_data.get(snap_key, {})
        img_url = snap.get("url") if snap else None
        lines = [f'<div class="pass-panel"><div class="pass-header">'
                 f'<span class="pass-label">{label}</span>'
                 f'<span class="pass-date">{_fmt_date(d)} orbit {orb}</span>'
                 f'</div>']
        if img_url:
            lines.append(f'<img src="{img_url}" alt="{label} SAR snapshot" class="snap-img">')
        for r in rows:
            b  = r["berth"].replace("_", " ")
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
    hm_start  = date(today_dt.year - 1, today_dt.month, 1)
    hm_passes = [p for p in passes if p.get("date", "") >= hm_start.isoformat()]

    berth_sides = [(b, s) for b in berths for s in [1, -1]]
    pass_dates  = [p["date"] for p in hm_passes]

    hm_data: dict = {}
    for p in hm_passes:
        for b in berths:
            bd = p.get(b, {})
            for h in bd.get("hulls", []):
                hm_data[(b, h.get("side", 1), p["date"])] = {
                    "cls": h.get("class", "smaller"),
                    "len_m": h.get("len_m", 0),
                }

    cell_w, cell_h = 14, 20
    n_cols = max(len(pass_dates), 1)
    n_rows = len(berth_sides)
    svg_w  = n_cols * cell_w + 120
    svg_h  = n_rows * cell_h + 40

    hm_cells = []
    for ri, (b, s) in enumerate(berth_sides):
        for ci, pd in enumerate(pass_dates):
            x    = 120 + ci * cell_w
            y    = ri * cell_h
            cell = hm_data.get((b, s, pd))
            if cell:
                fill = _cls_color(cell["cls"])
                tip  = f"{pd} {b}: {cell['cls']} {cell['len_m']:.0f}m"
            else:
                fill = "#1e293b"
                tip  = f"{pd} {b}: empty"
            hm_cells.append(f'<rect x="{x}" y="{y}" width="{cell_w-1}" height="{cell_h-1}" '
                            f'fill="{fill}" rx="1"><title>{tip}</title></rect>')

    for ri, (b, s) in enumerate(berth_sides):
        y     = ri * cell_h + cell_h * 0.7
        label = b.replace("_", " ") + (" +" if s > 0 else " −")
        hm_cells.insert(0, f'<text x="118" y="{y}" text-anchor="end" '
                         f'font-size="9" fill="var(--tx-muted)">{label}</text>')

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
        prev_month = None
        for v in reversed(visits):
            d  = v.get("start_date", "")
            mo = d[:7] if d else ""
            if mo != prev_month:
                prev_month = mo
                visit_rows_html.append(
                    f'<tr class="month-sep"><td colspan="5">{mo}</td></tr>'
                )
            b   = v.get("berth", "").replace("_", " ")
            cls = v.get("class_max", v.get("class", "—"))
            lm  = v.get("len_max",  v.get("len_m", "?"))
            days = v.get("visit_days", "?")
            clr  = _cls_color(cls)
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

    # ── HTML ───────────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Berth Occupancy — OilRadar</title>
<meta name="robots" content="noindex, nofollow">
<link rel="icon" type="image/x-icon" href="/assets/images/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TZQ5VH3GPY"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-TZQ5VH3GPY');</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0a0e17;--sidebar:#0d1320;--surface:#111827;
  --border:rgba(255,255,255,0.07);--muted:#94a3b8;--dim:#64748b;
  --text:#e2e8f0;--accent:#3b82f6;--green:#34d399;--red:#f87171;--yellow:#fbbf24;
  --tx:#e2e8f0;--tx-muted:#94a3b8;--radius:8px;--gap:1rem;
}}
html,body{{height:100%;overflow:hidden}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);display:flex;font-size:14px}}
/* Sidebar */
.sidebar{{width:200px;min-width:200px;background:var(--sidebar);border-right:1px solid var(--border);
  display:flex;flex-direction:column;height:100vh;position:fixed;top:0;left:0;z-index:200}}
.sidebar-logo{{padding:1.5rem 1.25rem 1rem;border-bottom:1px solid var(--border)}}
.logo-mark{{font-family:'Space Grotesk',sans-serif;font-size:.9rem;font-weight:700;letter-spacing:.2em;color:var(--accent)}}
.logo-sub{{font-size:.58rem;color:var(--muted);margin-top:.3rem;letter-spacing:.04em;line-height:1.5}}
.sidebar-nav{{flex:1;padding:.75rem 0;overflow-y:auto}}
.nav-group-label{{padding:.55rem 1.25rem .2rem;font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);font-weight:600}}
.nav-item{{display:flex;align-items:center;gap:.7rem;padding:.55rem 1.25rem;font-size:.78rem;color:var(--muted);
  cursor:pointer;transition:all .15s;border-left:2px solid transparent;letter-spacing:.02em;text-decoration:none}}
.nav-item:hover{{color:var(--text);background:rgba(255,255,255,.04)}}
.nav-item.active{{color:var(--text);border-left-color:var(--accent);background:rgba(59,130,246,.1)}}
.nav-icon{{font-size:.85rem;opacity:.7}}
.nav-divider{{margin:.5rem 1.25rem;border:none;border-top:1px solid var(--border)}}
.sidebar-footer{{padding:1rem 1.25rem;border-top:1px solid var(--border)}}
.user-email{{font-size:.65rem;color:var(--muted);display:block;margin-bottom:.6rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.logout-btn{{background:none;border:1px solid rgba(220,38,38,.2);color:var(--red);padding:.3rem .75rem;border-radius:4px;font-size:.68rem;cursor:pointer;width:100%;font-family:inherit;transition:border-color .2s}}
.logout-btn:hover{{border-color:rgba(220,38,38,.5)}}
/* Main */
.main{{margin-left:200px;flex:1;height:100vh;overflow-y:auto;padding:2rem}}
h1{{font-size:1.05rem;font-family:'Space Grotesk',sans-serif;font-weight:600;margin-bottom:.3rem}}
.meta{{color:var(--muted);font-size:.7rem;margin-bottom:1.5rem}}
h2{{font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:600;margin:1.5rem 0 .6rem}}
/* Cards */
.cards{{display:flex;gap:var(--gap);flex-wrap:wrap;margin-bottom:1rem}}
.card{{flex:1 1 220px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1rem}}
.card-title{{font-size:.6rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:600;margin-bottom:.3rem}}
.card-val{{font-size:2rem;font-weight:700;font-family:'Space Grotesk',sans-serif;font-variant-numeric:tabular-nums;line-height:1}}
.card-sub{{font-size:.72rem;color:var(--muted);margin-top:.3rem}}
/* Pass panels */
.pass-panels{{display:flex;gap:var(--gap);flex-wrap:wrap}}
.pass-panel{{flex:1 1 320px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1rem}}
.pass-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.6rem}}
.pass-label{{font-weight:600;font-size:.85rem}}
.pass-date{{font-size:.72rem;color:var(--muted)}}
.berth-row{{display:flex;align-items:center;gap:.4rem;padding:.2rem 0;flex-wrap:wrap;font-size:.8rem}}
.berth-row b{{min-width:110px;color:var(--muted)}}
.hull-box{{border:1.5px solid;border-radius:4px;padding:1px 6px;font-size:.7rem;font-weight:600;white-space:nowrap}}
.empty-label{{color:#475569;font-style:italic}}
.pass-footer{{margin-top:.6rem;font-size:.72rem;color:var(--muted)}}
.snap-img{{width:100%;border-radius:4px;margin-bottom:.5rem;display:block}}
/* Heatmap */
.hm-wrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:.75rem}}
.legend{{display:flex;gap:1rem;align-items:center;font-size:.72rem;margin-bottom:.4rem;flex-wrap:wrap}}
.legend-dot{{width:10px;height:10px;border-radius:2px;display:inline-block}}
/* Visit table */
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:.8rem}}
th{{text-align:left;color:var(--muted);font-weight:500;border-bottom:1px solid var(--border);padding:.3rem .4rem;font-size:.62rem;text-transform:uppercase;letter-spacing:.06em}}
td{{padding:.25rem .4rem;border-bottom:1px solid rgba(255,255,255,.04)}}
tr.month-sep td{{background:var(--border);color:var(--muted);font-size:.7rem;letter-spacing:.05em;padding:.2rem .4rem}}
.attribution{{margin-top:2rem;font-size:.7rem;color:var(--muted);border-top:1px solid var(--border);padding-top:.75rem}}
.se-note{{font-size:.7rem;color:var(--muted);font-style:italic;margin-top:.3rem}}
@media(max-width:640px){{
  .sidebar{{display:none}}
  .main{{margin-left:0}}
}}
</style>
</head>
<body>

<aside class="sidebar">
  <div class="sidebar-logo">
    <div class="logo-mark">OILRADAR</div>
    <div class="logo-sub">Kharg Island<br>Iran Crude Terminal</div>
  </div>
  <nav class="sidebar-nav">
    <div class="nav-group-label">Oil</div>
    <a class="nav-item" href="/dashboard"><span class="nav-icon">◎</span> Overview</a>
    <a class="nav-item" href="/dashboard"><span class="nav-icon">⊞</span> Per-Tank Map</a>
    <a class="nav-item" href="/dashboard"><span class="nav-icon">⬆</span> Upstream</a>
    <hr class="nav-divider">
    <div class="nav-group-label">Ships</div>
    <a class="nav-item" href="/dashboard"><span class="nav-icon">⚓</span> Tanker Queue</a>
    <a class="nav-item active" href="/berths"><span class="nav-icon">🛳</span> Berth Occupancy</a>
    <a class="nav-item" href="/dashboard"><span class="nav-icon">🛰</span> Hormuz Strait</a>
    <hr class="nav-divider">
    <div class="nav-group-label">Docs</div>
    <a class="nav-item" href="/dashboard"><span class="nav-icon">⌥</span> API</a>
  </nav>
  <div class="sidebar-footer">
    <span class="user-email" id="userEmail">Loading…</span>
    <button class="logout-btn" onclick="logout()">Logout</button>
  </div>
</aside>

<div class="main">
<h1>Berth Occupancy — Kharg Island Terminal</h1>
<div class="meta">Generated {generated} · {version}</div>

<h2>Key metrics</h2>
<div class="cards">
  <div class="card">
    <div class="card-title">{card1_title}</div>
    <div class="card-val">{card1_val}</div>
    <div class="card-sub">{card1_sub}</div>
  </div>
  <div class="card">
    <div class="card-title">Last ship</div>
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

<div class="attribution">
  SAR Sentinel-1 · Planetary Computer.
  Class by hull length (±50 m).
  Visits shorter than the gap between passes may be missed — lower bound.
</div>
</div><!-- .main -->

<script>
async function checkAuth() {{
    try {{
        const r = await fetch('/auth/check');
        const d = await r.json();
        if (!d.authenticated) {{ window.location.href = '/app'; return; }}
        document.getElementById('userEmail').textContent = d.email || '';
    }} catch {{ window.location.href = '/app'; }}
}}
function logout() {{ window.location.href = '/auth/logout'; }}
checkAuth();
</script>
</body>
</html>
"""

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html)
    print(f"Saved: {OUT_HTML} ({OUT_HTML.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
