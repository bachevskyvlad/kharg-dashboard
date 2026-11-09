"""ТЗ v2.2 probe: dump schema/samples of anchorage_calib, anchorage_blobs_corrected,
anchorage_optical_ref (+ counts) so the calibration code matches the real columns."""
import os, json, requests
U = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_WRITE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
out = []
def P(s): print(s, flush=True); out.append(s)
for tb, lim in (('anchorage_calib', 50), ('anchorage_optical_ref', 400), ('anchorage_blobs_corrected', 3),
                ('anchorage_blobs', 1), ('anchorage_daily', 2)):
    r = requests.get(f'{U}/rest/v1/{tb}?select=*&limit={lim}', headers={**H, 'Prefer': 'count=exact'}, timeout=60)
    P(f'== {tb}: http={r.status_code} count={r.headers.get("content-range")}')
    try:
        rows = r.json()
    except Exception:
        P(r.text[:300]); continue
    if isinstance(rows, list) and rows:
        P(f'   columns: {list(rows[0].keys())}')
        for x in rows[:lim]:
            x = {k: (v[:40] + '…' if isinstance(v, str) and len(v) > 60 else v) for k, v in x.items()}
            P('   ' + json.dumps(x, ensure_ascii=False, default=str))
    else:
        P(f'   {str(rows)[:300]}')
os.makedirs('diagnostics/anchorage_v22', exist_ok=True)
open('diagnostics/anchorage_v22/probe.txt', 'w').write('\n'.join(out) + '\n')
