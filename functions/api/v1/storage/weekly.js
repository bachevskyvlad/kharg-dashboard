import { verifyApiKey, apiError, apiJson, apiCsv, parseRange, applyRange } from '../_auth.js';

const COLS = ['date','vol_mn_bbl'];

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });

  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);

  const url = new URL(request.url);
  const range = parseRange(url);
  const fmt = url.searchParams.get('format') || 'json';

  const dataUrl = `${url.protocol}//${url.host}/data/data.json`;
  let json;
  try { json = await (await fetch(dataUrl)).json(); } catch { return apiError('Data unavailable', 503); }

  let rows = (json.dates || []).map((d, i) => ({ date: d, vol_mn_bbl: +(json.values[i] || 0).toFixed(3) }));
  rows = applyRange(rows, range);

  if (fmt === 'csv') return apiCsv(rows, COLS);
  return apiJson({ count: rows.length, records: rows });
}
