// Shared API key auth for all /api/v1/* endpoints
// Key = base64url( HMAC-SHA256( email, API_SECRET ) )
// Trial users are blocked entirely.

const TRIAL_USERS = ['cyril@bluewaterstrategy.eu'];
const API_SECRET  = 'oilradar-api-secret-2024-kharg';

export async function verifyApiKey(request, env) {
  const key = request.headers.get('X-API-Key') || new URL(request.url).searchParams.get('api_key');
  if (!key) return { ok: false, error: 'Missing X-API-Key header' };

  // key = base64url( email + ':' + hmac )
  let decoded;
  try { decoded = atob(key.replace(/-/g,'+').replace(/_/,'/').replace(/\./,'=')); } catch { return { ok: false, error: 'Invalid API key format' }; }
  const colonIdx = decoded.indexOf(':');
  if (colonIdx < 1) return { ok: false, error: 'Invalid API key format' };
  const email = decoded.slice(0, colonIdx);
  const provided = decoded.slice(colonIdx + 1);

  const expected = await hmacSha256(email, API_SECRET);
  if (provided !== expected) return { ok: false, error: 'Invalid API key' };

  if (TRIAL_USERS.map(e => e.toLowerCase()).includes(email.toLowerCase()))
    return { ok: false, error: 'API access requires a paid plan' };

  return { ok: true, email };
}

export async function generateApiKey(email) {
  const hmac = await hmacSha256(email, API_SECRET);
  const raw = email + ':' + hmac;
  return btoa(raw).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'.');
}

async function hmacSha256(message, secret) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2,'0')).join('');
}

export function apiError(msg, status = 401) {
  return new Response(JSON.stringify({ error: msg }), {
    status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}

export function apiJson(data) {
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' }
  });
}

export function apiCsv(rows, columns) {
  const lines = [columns.join(',')];
  for (const r of rows) lines.push(columns.map(c => r[c] ?? '').join(','));
  return new Response(lines.join('\n'), {
    headers: { 'Content-Type': 'text/csv', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' }
  });
}

export function parseRange(url) {
  const from = url.searchParams.get('from') || null;
  const to   = url.searchParams.get('to')   || null;
  return { from, to };
}

export function applyRange(rows, { from, to }, dateKey = 'date') {
  return rows.filter(r => {
    if (from && r[dateKey] < from) return false;
    if (to   && r[dateKey] > to)   return false;
    return true;
  });
}
