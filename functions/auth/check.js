import { getUserByEmail } from '../lib/sheets.js';

const ALLOWED_ORIGINS = [
  'https://oilradar.io',
  'https://www.oilradar.io',
  'https://app.oilradar.io'
];

export async function onRequest(context) {
  const { request, env } = context;
  const origin = request.headers.get('Origin');

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders(origin) });
  }

  const cookies = parseCookies(request.headers.get('Cookie') || '');
  const sessionToken = cookies.session;

  if (!sessionToken) return jsonResponse({ authenticated: false }, origin);

  try {
    const sessionData = JSON.parse(atob(sessionToken));
    if (sessionData.exp && sessionData.exp < Date.now()) return jsonResponse({ authenticated: false }, origin);

    let tier = 'paid';
    if (env.GOOGLE_SA_KEY) {
      const user = await getUserByEmail(env.GOOGLE_SA_KEY, sessionData.email);
      if (user) tier = user.data.status || 'paid';
    }

    return jsonResponse({ authenticated: true, email: sessionData.email, name: sessionData.name, tier }, origin);
  } catch (e) {
    return jsonResponse({ authenticated: false }, origin);
  }
}

function corsHeaders(origin) {
  const headers = { 'Content-Type': 'application/json' };
  if (origin && ALLOWED_ORIGINS.includes(origin)) {
    headers['Access-Control-Allow-Origin'] = origin;
    headers['Access-Control-Allow-Credentials'] = 'true';
    headers['Vary'] = 'Origin';
  }
  return headers;
}

function parseCookies(cookieHeader) {
  const cookies = {};
  cookieHeader.split(';').forEach(cookie => {
    const [name, ...rest] = cookie.trim().split('=');
    cookies[name] = rest.join('=');
  });
  return cookies;
}

function jsonResponse(data, origin) {
  return new Response(JSON.stringify(data), { headers: corsHeaders(origin) });
}
