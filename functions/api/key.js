import { generateApiKey } from './_auth.js';

const TRIAL_USERS = [];

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

  const apiKey = await generateApiKey(email, context.env);
  return json({ api_key: apiKey, email });
}
