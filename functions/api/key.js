// GET /api/key — returns API key for the authenticated session user (paid only)
import { generateApiKey } from './_auth.js';

const TRIAL_USERS = ['cyril@bluewaterstrategy.eu'];

function parseCookies(header) {
  const c = {};
  (header || '').split(';').forEach(p => { const [k,...v] = p.trim().split('='); c[k] = v.join('='); });
  return c;
}

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS')
    return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type' } });

  const session = parseCookies(request.headers.get('Cookie') || '').session;
  if (!session) return json({ error: 'Not authenticated' }, 401);

  let email;
  try { email = JSON.parse(atob(session)).email; } catch { return json({ error: 'Invalid session' }, 401); }

  if (TRIAL_USERS.map(e => e.toLowerCase()).includes(email.toLowerCase()))
    return json({ error: 'API access requires a paid plan' }, 403);

  const key = await generateApiKey(email);
  return json({ api_key: key, email });
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}
