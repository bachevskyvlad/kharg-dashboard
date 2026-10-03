import { getUserByEmail, upsertUser } from '../lib/sheets.js';
import { tgNotify, tgTime } from '../lib/telegram.js';

const APP_URL = "https://app.oilradar.io";

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const error = url.searchParams.get('error');

  if (error || !code) {
    context.ctx.waitUntil(tgNotify(`❌ Google auth cancelled\n${error || 'no code'}\n${tgTime()}`));
    return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);
  }

  try {
    const tokenResponse = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code, client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET,
        redirect_uri: `${APP_URL}/auth/callback`, grant_type: 'authorization_code'
      })
    });
    const tokens = await tokenResponse.json();
    if (!tokens.access_token) {
      context.ctx.waitUntil(tgNotify(`❌ Google token failed\nNo access_token\n${tgTime()}`));
      return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);
    }

    const userResponse = await fetch('https://www.googleapis.com/oauth2/v2/userinfo', {
      headers: { Authorization: `Bearer ${tokens.access_token}` }
    });
    const user = await userResponse.json();
    const email = user.email?.toLowerCase();
    if (!email) {
      context.ctx.waitUntil(tgNotify(`❌ Google login failed\nNo email returned\n${tgTime()}`));
      return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);
    }

    // Authorization: must exist in CRM sheet
    let tier = 'paid';
    if (env.GOOGLE_SA_KEY) {
      try {
        const existing = await getUserByEmail(env.GOOGLE_SA_KEY, email);
        if (!existing) {
          context.ctx.waitUntil(tgNotify(`⛔ Google blocked\n${user.name || email} (${email})\nNot in CRM\n${tgTime()}`));
          return Response.redirect(`${APP_URL}/?error=unauthorized`, 302);
        }
        const nameParts = (user.name || '').split(' ');
        tier = await upsertUser(env.GOOGLE_SA_KEY, {
          email,
          first_name: nameParts[0] || '',
          last_name: nameParts.slice(1).join(' ') || '',
        });
      } catch (sheetErr) {
        console.error('Sheets error:', sheetErr?.message);
      }
    }

    const sessionData = { email, name: user.name || email, tier, exp: Date.now() + (7 * 24 * 60 * 60 * 1000) };
    const sessionToken = btoa(JSON.stringify(sessionData));

    context.ctx.waitUntil(tgNotify(`✅ Google login\n${user.name || email} (${email})\n${tgTime()}`));

    return new Response(null, {
      status: 302,
      headers: {
        'Location': `${APP_URL}/dashboard`,
        'Set-Cookie': `session=${sessionToken}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${7 * 24 * 60 * 60}`
      }
    });
  } catch (e) {
    context.ctx.waitUntil(tgNotify(`❌ Google auth error\n${e?.message || 'unknown'}\n${tgTime()}`));
    return Response.redirect(`${APP_URL}/?error=auth_failed`, 302);
  }
}
