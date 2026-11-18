import { verifyApiKey, apiError, apiJson, apiCsv } from '../_auth.js';

const BARREL_PER_M3 = 6.2898;
const TANK_HEIGHT_MIN = 2;

export async function onRequest(context) {
  const { request } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  const auth = await verifyApiKey(request, context.env);
  if (!auth.ok) return apiError(auth.error);
  const url = new URL(request.url);
  const from = url.searchParams.get("from"), to = url.searchParams.get("to");
  const fmt = url.searchParams.get("format") || "json";
  const tankId = url.searchParams.get("tank_id");
  const SUPABASE_URL = context.env.SUPABASE_URL;
  const SUPABASE_KEY = context.env.SUPABASE_KEY;
  const PAGE = 5000;
  let allRows = [], offset = 0;
  const headers = { apikey: SUPABASE_KEY, Authorization: "Bearer " + SUPABASE_KEY };
  while (true) {
    let sbUrl = SUPABASE_URL + "/rest/v1/raw_observations?select=date,tank_id,db_mean,radius_m,h_max&order=date&limit=" + PAGE + "&offset=" + offset;
    if (from) sbUrl += "&date=gte." + from;
    if (to)   sbUrl += "&date=lte." + to;
    if (tankId) sbUrl += "&tank_id=eq." + tankId;
    try {
      const batch = await (await fetch(sbUrl, { headers })).json();
      if (!Array.isArray(batch) || !batch.length) break;
      allRows.push(...batch);
      if (batch.length < PAGE) break;
      offset += PAGE;
    } catch { return apiError("Data unavailable", 503); }
  }
  const dbs = allRows.map(r=>r.db_mean).filter(v=>v!=null).sort((a,b)=>a-b);
  const dBmin = dbs[Math.floor(dbs.length*0.05)]??0;
  const dBmax = dbs[Math.floor(dbs.length*0.95)]??20;
  const range = dBmax - dBmin || 1;
  const records = allRows.map(r => {
    const clipped = Math.min(Math.max(r.db_mean, dBmin), dBmax);
    const fill = (dBmax - clipped) / range;
    const h = TANK_HEIGHT_MIN + fill * ((r.h_max||14) - TANK_HEIGHT_MIN);
    const vol = (Math.PI * Math.pow(r.radius_m||40, 2) * h * BARREL_PER_M3) / 1e6;
    return { date: r.date, tank_id: r.tank_id, vol_mn_bbl: +vol.toFixed(4) };
  });
  const cols = ["date","tank_id","vol_mn_bbl"];
  if (fmt === "csv") return apiCsv(records, cols);
  return apiJson({ count: records.length, records: records });
}
