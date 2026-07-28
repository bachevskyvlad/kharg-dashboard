const TRIAL_USERS = ['cyril@bluewaterstrategy.eu'];
const API_SECRET  = 'oilradar-api-secret-2024-kharg';

async function verifyApiKey(request) {
  const key = request.headers.get('X-API-Key') || new URL(request.url).searchParams.get('api_key');
  if (!key) return { ok: false, error: 'Missing X-API-Key header' };
  let decoded;
  try { decoded = atob(key.replace(/-/g,'+').replace(/_/g,'/').replace(/\./g,'=')); } catch { return { ok: false, error: 'Invalid API key' }; }
  const ci = decoded.indexOf(':');
  if (ci < 1) return { ok: false, error: 'Invalid API key' };
  const email = decoded.slice(0, ci);
  const provided = decoded.slice(ci + 1);
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey('raw', enc.encode(API_SECRET), { name:'HMAC', hash:'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', k, enc.encode(email));
  const expected = Array.from(new Uint8Array(sig)).map(b=>b.toString(16).padStart(2,'0')).join('');
  if (provided !== expected) return { ok: false, error: 'Invalid API key' };
  if (TRIAL_USERS.map(e=>e.toLowerCase()).includes(email.toLowerCase())) return { ok: false, error: 'API access requires a paid plan' };
  return { ok: true, email };
}

const COLS = ['date','vlcc','suezmax','aframax','mr','total'];

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });

  const auth = await verifyApiKey(request);
  if (!auth.ok) return err(auth.error);

  const url = new URL(request.url);
  const from = url.searchParams.get('from');
  const to   = url.searchParams.get('to');
  const fmt  = url.searchParams.get('format') || 'json';

  let json;
  try { json = await (await fetch(`${url.protocol}//${url.host}/data/anchorage.json`)).json(); }
  catch { return err('Data unavailable', 503); }

  let rows = (json.records || []).map(r => ({ date:r.date, vlcc:r.vlcc, suezmax:r.suezmax, aframax:r.aframax, mr:r.mr, total:r.total }));
  if (from) rows = rows.filter(r => r.date >= from);
  if (to)   rows = rows.filter(r => r.date <= to);

  if (fmt === 'csv') return csv(rows, COLS);
  return ok({ updated: json.updated, count: rows.length, records: rows });
}

function ok(data) { return new Response(JSON.stringify(data), { headers: { 'Content-Type':'application/json','Access-Control-Allow-Origin':'*','Cache-Control':'no-store' } }); }
function err(msg, s=401) { return new Response(JSON.stringify({ error:msg }), { status:s, headers: { 'Content-Type':'application/json','Access-Control-Allow-Origin':'*' } }); }
function csv(rows, cols) {
  const lines = [cols.join(','), ...rows.map(r => cols.map(c => r[c]??'').join(','))];
  return new Response(lines.join('\n'), { headers: { 'Content-Type':'text/csv','Access-Control-Allow-Origin':'*','Cache-Control':'no-store' } });
}
