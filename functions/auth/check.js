export async function onRequest(context) {
  const { request } = context;
  const cookies = parseCookies(request.headers.get('Cookie') || '');
  const sessionToken = cookies.session;

  if (!sessionToken) return jsonResponse({ authenticated: false });

  try {
    const sessionData = JSON.parse(atob(sessionToken));
    if (sessionData.exp && sessionData.exp < Date.now()) return jsonResponse({ authenticated: false });
    return jsonResponse({ authenticated: true, email: sessionData.email, name: sessionData.name });
  } catch (e) {
    return jsonResponse({ authenticated: false });
  }
}

function parseCookies(cookieHeader) {
  const cookies = {};
  cookieHeader.split(';').forEach(cookie => {
    const [name, ...rest] = cookie.trim().split('=');
    cookies[name] = rest.join('=');
  });
  return cookies;
}

function jsonResponse(data) {
  return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
}
