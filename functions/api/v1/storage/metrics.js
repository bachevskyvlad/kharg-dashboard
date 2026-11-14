import { verifyApiKey, apiError, apiJson, apiCsv } from '../../../_auth.js';

export async function onRequest(context) {
  const { request } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);
  const url = new URL(request.url);
  const from = url.searchParams.get("from"), to = url.searchParams.get("to"), fmt = url.searchParams.get("format") || "json";
  const SUPABASE_URL = context.env.SUPABASE_URL;
  const SUPABASE_KEY = context.env.SUPABASE_KEY;
  let sbUrl = SUPABASE_URL + "/rest/v1/kharg_daily?select=date,vol_mn_bbl,vol_5d_ma,day_change,signal,pct_change_30d&order=date";
  if (from) sbUrl += "&date=gte." + from;
  if (to)   sbUrl += "&date=lte." + to;
  let rows;
  try { rows = await (await fetch(sbUrl, { headers: { apikey: SUPABASE_KEY, Authorization: "Bearer " + SUPABASE_KEY } })).json(); }
  catch { return apiError("Data unavailable", 503); }
  if (!Array.isArray(rows)) return apiError("Data unavailable", 503);
  const cols = ["date","vol_mn_bbl","vol_5d_ma","day_change","signal","pct_change_30d"];
  if (fmt === "csv") return apiCsv(rows, cols);
  return apiJson({ count: rows.length, records: rows });
}
