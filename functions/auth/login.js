export async function onRequest(context) {
  const clientId = "377241273746-2qhpgcaa0a9nkhjuiihfqqs35rvp5asv.apps.googleusercontent.com";
  const appUrl = "https://app.oilradar.io";
  const redirectUri = `${appUrl}/auth/callback`;
  const scope = encodeURIComponent('email profile');
  const state = crypto.randomUUID();
  const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?` +
    `client_id=${clientId}&redirect_uri=${encodeURIComponent(redirectUri)}` +
    `&response_type=code&scope=${scope}&state=${state}&access_type=offline&prompt=consent`;
  return new Response(null, {
    status: 302,
    headers: {
      'Location': authUrl,
      'Set-Cookie': `oauth_state=${state}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=600`
    }
  });
}
