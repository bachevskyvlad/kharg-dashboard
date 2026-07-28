import { verifyApiKey, apiError, apiJson, apiCsv, parseRange, applyRange } from '../_auth.js';

const COLS = ['date','vlcc','suezmax','aframax','mr','total'];

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });

  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);

  const url = new URL(request.url);
  const range = parseRange(url);
  const fmt = url.searchParams.get('format') || 'json';

  const base = new URL(request.url);
  const dataUrl = `${base.protocol}//${base.host}/data/anchorage.json`;
  let json;
  try {
    const res = await fetch(dataUrl);
    json = await res.json();
  } catch {
    return apiError('Data unavailable', 503);
  }

  let rows = (json.records || []).map(r => ({
    date: r.date, vlcc: r.vlcc, suezmax: r.suezmax,
    aframax: r.aframax, mr: r.mr, total: r.total
  }));
  rows = applyRange(rows, range);

  if (fmt === 'csv') return apiCsv(rows, COLS);
  return apiJson({ updated: json.updated, count: rows.length, records: rows });
}
