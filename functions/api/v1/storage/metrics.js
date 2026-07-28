import { verifyApiKey, apiError, apiJson, apiCsv, parseRange, applyRange } from '../_auth.js';

const SUPABASE_URL = 'https://yoekhkczwdeyauwarsyy.supabase.co';
const SUPABASE_KEY = 'sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM';
const COLS = ['date','vol_mn_bbl','vol_5d_ma','day_change','signal','pct_change_30d'];

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });

  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);

  const url = new URL(request.url);
  const { from, to } = parseRange(url);
  const fmt = url.searchParams.get('format') || 'json';

  let sbUrl = `${SUPABASE_URL}/rest/v1/kharg_daily?select=date,vol_mn_bbl,vol_5d_ma,day_change,signal,pct_change_30d&order=date`;
  if (from) sbUrl += `&date=gte.${from}`;
  if (to)   sbUrl += `&date=lte.${to}`;

  let rows;
  try {
    const res = await fetch(sbUrl, { headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` } });
    rows = await res.json();
  } catch { return apiError('Data unavailable', 503); }

  if (!Array.isArray(rows)) return apiError('Data unavailable', 503);

  if (fmt === 'csv') return apiCsv(rows, COLS);
  return apiJson({ count: rows.length, records: rows });
}
