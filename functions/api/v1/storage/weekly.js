import { verifyApiKey, apiError, apiJson, apiCsv } from '../../../_auth.js';

export async function onRequest(context) {
  const { request } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);
  const url = new URL(request.url);
  const from = url.searchParams.get("from"), to = url.searchParams.get("to"), fmt = url.searchParams.get("format") || "json";
  let data;
  try {
    const assetUrl = url.protocol + "//" + url.host + "/data/data.json";
    data = await (await context.env.ASSETS.fetch(new Request(assetUrl))).json();
  } catch { return apiError("Data unavailable", 503); }
  let rows = (data.dates || []).map((d,i) => ({ date:d, vol_mn_bbl:+((data.values[i]||0).toFixed(3)) }));
  if (from) rows = rows.filter(r => r.date >= from);
  if (to)   rows = rows.filter(r => r.date <= to);
  const cols = ["date","vol_mn_bbl"];
  if (fmt === "csv") return apiCsv(rows, cols);
  return apiJson({ count: rows.length, records: rows });
}
