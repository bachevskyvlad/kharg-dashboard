"""ТЗ v2.2 probe 2: what the view returns for the 5 reference dates; calib/optical_ref visibility."""
import os, json, requests
U = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_WRITE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
out = []
def P(s): print(s, flush=True); out.append(s)
for tb in ('anchorage_calib', 'anchorage_optical_ref'):
    r = requests.get(f'{U}/rest/v1/{tb}?select=*', headers={**H, 'Prefer': 'count=exact'}, timeout=60)
    P(f'== {tb}: http={r.status_code} content-range={r.headers.get("content-range")} body={r.text[:300]}')
    for c in ('date', 'k_m_per_sea', 'sea_ref', 'merge_m', 'vlcc', 'aframax', 'mr', 'suezmax', 'total', 'id', 'param', 'value', 'name', 'lat', 'lon', 'length_m', 'class'):
        rr = requests.get(f'{U}/rest/v1/{tb}?select={c}&limit=0', headers=H, timeout=30)
        if rr.status_code == 200:
            P(f'   column exists: {c}')
cols = 'date,lat,lon,sea_p70,peak_norm,area_px,length_bbox_m,length_corr_m,class_corr'
for d in ('2026-10-18', '2026-10-24', '2026-10-30', '2026-11-05'):
    r = requests.get(f'{U}/rest/v1/anchorage_blobs_corrected?select={cols}&date=eq.{d}&order=length_corr_m.desc', headers=H, timeout=60)
    rows = r.json()
    P(f'== view {d}: n={len(rows)}')
    for x in rows:
        P('   ' + json.dumps(x))
r = requests.get(f'{U}/rest/v1/anchorage_daily?select=*&date=in.(2026-10-18,2026-10-24,2026-10-30,2026-11-05)&order=date', headers=H, timeout=60)
P('== daily 4 dates: ' + json.dumps(r.json()))
open('diagnostics/anchorage_v22/probe.txt', 'w').write('\n'.join(out) + '\n')
