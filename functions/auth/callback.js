const GOOGLE_CLIENT_ID = "377241273746-2qhpgcaa0a9nkhjuiihfqqs35rvp5asv.apps.googleusercontent.com";
const GOOGLE_CLIENT_SECRET = "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";
const APP_URL = "https://app.oilradar.io";

const WHITELIST = [
  'vladislav.bachevsky@gmail.com',
  'keepmeup2date@gmail.com',
  'vvbobylev@gmail.com',
  'fryday.kg@gmail.com'
];

export async function onRequest(context) {
  const { request } = context;
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const error = url.searchParams.get('error');

  if (error || !code) {
    return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);
  }

  try {
    const tokenResponse = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code, client_id: GOOGLE_CLIENT_ID, client_secret: GOOGLE_CLIENT_SECRET,
        redirect_uri: `${APP_URL}/auth/callback`, grant_type: 'authorization_code'
      })
    });
    const tokens = await tokenResponse.json();
    if (!tokens.access_token) return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);

    const userResponse = await fetch('https://www.googleapis.com/oauth2/v2/userinfo', {
      headers: { Authorization: `Bearer ${tokens.access_token}` }
    });
    const user = await userResponse.json();
    const email = user.email?.toLowerCase();
    if (!email) return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);

    const isAuthorized = WHITELIST.map(e => e.toLowerCase()).includes(email);
    if (!isAuthorized) return Response.redirect(`${APP_URL}/?error=unauthorized`, 302);

    const sessionData = { email, name: user.name || email, exp: Date.now() + (7 * 24 * 60 * 60 * 1000) };
    const sessionToken = btoa(JSON.stringify(sessionData));

    return new Response(null, {
      status: 302,
      headers: {
        'Location': `${APP_URL}/dashboard.html`,
        'Set-Cookie': `session=${sessionToken}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${7 * 24 * 60 * 60}`
      }
    });
  } catch (e) {
    return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);
  }
}
