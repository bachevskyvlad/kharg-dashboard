"""ТЗ v2.1 step 6: check the live app.oilradar.io/anchorage (headless Chromium).
Facts printed as text + screenshots saved; compares totals with daily_cal.csv."""
import csv, json, os, sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
URLS = ['https://app.oilradar.io/anchorage', 'https://app.oilradar.io/dashboard']
cal = {r['date']: r for r in csv.DictReader(open(os.path.join(HERE, 'daily_cal.csv')))}
lines = []


def P(s):
    print(s, flush=True); lines.append(s)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1400, 'height': 1000})
    pg.goto(URLS[0], wait_until='networkidle'); pg.wait_for_timeout(4000)
    js = pg.evaluate("""async () => {
      const s = await loadAnchorageSeries();
      const snaps = document.getElementById('aq-snaps');
      const cap = [...document.querySelectorAll('#aq-snaps [data-snap]')].map(e => ({
        k: e.dataset.snap, date: e.querySelector('.aq-snap-date')?.textContent,
        img: e.querySelector('img')?.currentSrc, w: e.querySelector('img')?.naturalWidth, h: e.querySelector('img')?.naturalHeight}));
      return {source: s.source, n: s.records.length, first: s.records[0], last: s.records[s.records.length-1],
              recs: s.records, kpiTotal: document.getElementById('kpi-total')?.textContent,
              kpiDate: document.getElementById('kpi-date')?.textContent,
              snapsVisible: snaps ? getComputedStyle(snaps).display !== 'none' : null, cap};
    }""")
    P(f'STEP6 {URLS[0]}: series source={js["source"]} records={js["n"]} first={js["first"]["date"]} last={js["last"]["date"]}')
    P(f'STEP6 KPI on page: total={js["kpiTotal"]} date={js["kpiDate"]}')
    recs = {r['date']: r for r in js['recs']}
    for d in sorted(cal)[-3:] + [sorted(cal)[0]]:
        site = recs.get(d, {}).get('total'); c = cal[d]['total_no_small']
        P(f'STEP6 compare {d}: site total={site} | daily_cal.csv total_no_small={c} -> {"OK" if str(site) == str(c) else "MISMATCH"}')
    P(f'STEP6 snapshots block visible={js["snapsVisible"]}')
    for c in js['cap']:
        P(f'STEP6 snapshot {c["k"]}: caption date={c["date"]} img={c["img"]} natural={c["w"]}x{c["h"]}')
    pg.locator('#aq-snaps').scroll_into_view_if_needed()
    pg.screenshot(path=os.path.join(HERE, 'logs', 'live_anchorage.png'), full_page=True)
    b.close()

with open(os.path.join(HERE, 'logs', 'finalize_v21.txt'), 'a') as f:
    f.write('\n'.join(lines) + '\n')
