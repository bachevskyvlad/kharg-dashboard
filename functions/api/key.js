const TRIAL_USERS = ['cyril@bluewaterstrategy.eu'];
const API_SECRET  = 'oilradar-api-secret-2024-kharg';

async function generateApiKey(email) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(API_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(email));
  const hex = Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2,'0')).join('');
  return btoa(email + ':' + hex).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'.');
}

function parseCookies(header) {
  const c = {};
  (header || '').split(';').forEach(p => { const [k,...v] = p.trim().split('='); c[k] = v.join('='); });
  return c;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    }
  });
}

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS')
    return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });

  const session = parseCookies(request.headers.get('Cookie') || '').session;
  if (!session) return json({ error: 'Not authenticated' }, 401);

  let email;
  try { email = JSON.parse(atob(session)).email; } catch { return json({ error: 'Invalid session' }, 401); }
  if (!email) return json({ error: 'Invalid session' }, 401);

  if (TRIAL_USERS.map(e => e.toLowerCase()).includes(email.toLowerCase()))
    return json({ error: 'API access requires a paid plan' }, 403);

  const apiKey = await generateApiKey(email);
  return json({ api_key: apiKey, email });
}
