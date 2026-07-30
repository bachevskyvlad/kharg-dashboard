import { upsertUser } from '../lib/sheets.js';

const APP_URL = "https://app.oilradar.io";
const TG_BOT = "7745904293:AAHV2qN_5_XNxpg6Ir3iV3XmEKpokthi308";
const TG_CHAT = "204715787";

async function tgLogin(email) {
  const text = `🔐 Login\n${email}\n${new Date().toISOString().slice(0,16).replace('T',' ')} UTC`;
  await fetch(`https://api.telegram.org/bot${TG_BOT}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: TG_CHAT, text })
  }).catch(() => {});
}

const TRIAL_USERS = [
  'cyril@bluewaterstrategy.eu'
];

async function computeOTP(secret, email, windowOffset = 0) {
  const window = Math.floor(Date.now() / 600000) + windowOffset;
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const data = new TextEncoder().encode(`${email}:${window}`);
  const sig = await crypto.subtle.sign('HMAC', key, data);
  const bytes = new Uint8Array(sig);
  const num = ((bytes[0] << 16) | (bytes[1] << 8) | bytes[2]) % 1000000;
  return String(num).padStart(6, '0');
}

export async function onRequest(context) {
  const { request, env } = context;

  if (request.method !== 'POST') {
    return new Response('Method not allowed', { status: 405 });
  }

  let body;
  try { body = await request.json(); } catch {
    return jsonErr('Invalid request body');
  }

  const email = (body.email || '').toLowerCase().trim();
  const code = (body.code || '').trim();
  if (!email || !code) return jsonErr('Missing email or code');

  const secret = env.OTP_SECRET;
  if (!secret) return jsonErr('Server configuration error');

  // Check current and previous 10-min windows (allows up to ~20 min)
  const validCodes = await Promise.all([
    computeOTP(secret, email, 0),
    computeOTP(secret, email, -1)
  ]);

  if (!validCodes.includes(code)) {
    return jsonErr('Invalid or expired code. Please request a new one.');
  }

  let tier = 'paid';
  if (env.GOOGLE_SA_KEY) {
    tier = await upsertUser(env.GOOGLE_SA_KEY, { email });
  }

  const sessionData = { email, name: email, tier, exp: Date.now() + (7 * 24 * 60 * 60 * 1000) };
  const sessionToken = btoa(JSON.stringify(sessionData));

  context.ctx.waitUntil(tgLogin(email));

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Set-Cookie': `session=${sessionToken}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${7 * 24 * 60 * 60}`
    }
  });
}

function jsonErr(msg) {
  return new Response(JSON.stringify({ ok: false, error: msg }), {
    status: 400, headers: { 'Content-Type': 'application/json' }
  });
}
