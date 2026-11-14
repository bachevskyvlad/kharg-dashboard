import { verifyApiKey, apiError, apiJson, apiCsv } from '../../_auth.js';

const COLS = ['date','vlcc','suezmax','aframax','mr','total'];

export async function onRequest(context) {
  const { request } = context;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });

  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);

  const url = new URL(request.url);
  const from = url.searchParams.get('from');
  const to   = url.searchParams.get('to');
  const fmt  = url.searchParams.get('format') || 'json';

  let json;
  try {
    const assetUrl = `${url.protocol}//${url.host}/data/anchorage.json`;
    json = await (await context.env.ASSETS.fetch(new Request(assetUrl))).json();
  } catch { return apiError('Data unavailable', 503); }

  let rows = (json.records || []).map(r => ({ date:r.date, vlcc:r.vlcc, suezmax:r.suezmax, aframax:r.aframax, mr:r.mr, total:r.total }));
  if (from) rows = rows.filter(r => r.date >= from);
  if (to)   rows = rows.filter(r => r.date <= to);

  if (fmt === 'csv') return apiCsv(rows, COLS);
  return apiJson({ updated: json.updated, count: rows.length, records: rows });
}
