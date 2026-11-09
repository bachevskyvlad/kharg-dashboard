"""Which Supabase key secrets exist and what role they have. Never prints values.
Role check without writes: GET /auth/v1/admin/users (200 only for service_role / sb_secret)."""
import os, json, base64, requests
U = 'https://yoekhkczwdeyauwarsyy.supabase.co'
NAMES = ['SUPABASE_KEY', 'SUPABASE_SERVICE_KEY', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_SECRET_KEY',
         'SB_SERVICE_KEY', 'SB_SECRET_KEY', 'SERVICE_ROLE_KEY', 'SUPABASE_ADMIN_KEY', 'SUPABASE_SERVICE_ROLE',
         'SUPABASE_SR_KEY', 'SUPABASE_WRITE_KEY', 'SUPABASE_ANON_KEY']
out = []
for n in NAMES:
    v = os.environ.get('K_' + n, '')
    if not v:
        out.append(f'{n}: not set'); continue
    kind = 'sb_secret' if v.startswith('sb_secret_') else 'sb_publishable' if v.startswith('sb_publishable_') else 'jwt' if v.count('.') == 2 else 'other'
    role = None
    if kind == 'jwt':
        try:
            p = v.split('.')[1]; p += '=' * (-len(p) % 4)
            role = json.loads(base64.urlsafe_b64decode(p)).get('role')
        except Exception:
            role = '?'
    h = {'apikey': v, 'Authorization': f'Bearer {v}'}
    adm = requests.get(f'{U}/auth/v1/admin/users?per_page=1', headers=h, timeout=30).status_code
    calib = requests.get(f'{U}/rest/v1/anchorage_calib?select=id', headers=h, timeout=30)
    out.append(f'{n}: set, len={len(v)}, kind={kind}, jwt_role={role}, admin_api_http={adm} '
               f'(200 => service_role), anchorage_calib rows={len(calib.json()) if calib.ok else calib.status_code}')
print('\n'.join(out))
open('diagnostics/anchorage_v22/keyprobe.txt', 'w').write('\n'.join(out) + '\n')
