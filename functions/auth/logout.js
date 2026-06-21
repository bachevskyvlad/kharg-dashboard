export async function onRequest(context) {
  return new Response(null, {
    status: 302,
    headers: {
      'Location': 'https://app.oilradar.io',
      'Set-Cookie': 'session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0'
    }
  });
}
