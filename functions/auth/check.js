const ALLOWED_ORIGINS = [
  'https://oilradar.io',
  'https://www.oilradar.io',
  'https://app.oilradar.io'
];

export async function onRequest(context) {
  const { request } = context;
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
    return jsonResponse({ authenticated: true, email: sessionData.email, name: sessionData.name, tier: sessionData.tier || 'paid' }, origin);
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
