import { getUserByEmail } from '../lib/sheets.js';

const APP_URL = "https://app.oilradar.io";

async function generateOTP(secret, email) {
  const window = Math.floor(Date.now() / 600000); // 10-min window
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
  if (!email || !email.includes('@')) return jsonErr('Invalid email');

  // Check authorization against CRM (Google Sheets)
  if (env.GOOGLE_SA_KEY) {
    try {
      const existing = await getUserByEmail(env.GOOGLE_SA_KEY, email);
      if (!existing) {
        return jsonErr('This email is not on the authorized list. Contact us to request access.');
      }
    } catch (err) {
      console.error('Sheets check failed:', err?.message);
      return jsonErr('Authorization check failed. Please try again.');
    }
  }

  const secret = env.OTP_SECRET;
  const resendKey = env.RESEND_API_KEY;
  if (!secret || !resendKey) return jsonErr('Server configuration error');

  const code = await generateOTP(secret, email);

  const emailRes = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${resendKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: 'OilRadar <noreply@oilradar.io>',
      to: [email],
      subject: `Your OilRadar access code: ${code}`,
      html: `
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;background:#f9f9f7">
          <div style="font-size:22px;font-weight:700;letter-spacing:0.15em;color:#1d4ed8;margin-bottom:8px">OILRADAR</div>
          <div style="font-size:12px;color:#9ca3af;margin-bottom:32px">Kharg Island Intelligence Platform</div>
          <p style="color:#374151;font-size:15px;margin-bottom:24px">Your one-time access code:</p>
          <div style="font-size:40px;font-weight:700;letter-spacing:0.3em;color:#111827;font-family:monospace;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:20px;text-align:center;margin-bottom:24px">${code}</div>
          <p style="color:#6b7280;font-size:13px">Valid for 10 minutes. Do not share this code.</p>
        </div>`
    })
  });

  if (!emailRes.ok) {
    const err = await emailRes.text();
    console.error('Resend error:', err);
    return jsonErr('Failed to send email. Please try again.');
  }

  return new Response(JSON.stringify({ ok: true }), {
    headers: { 'Content-Type': 'application/json' }
  });
}

function jsonErr(msg) {
  return new Response(JSON.stringify({ ok: false, error: msg }), {
    status: 400, headers: { 'Content-Type': 'application/json' }
  });
}
