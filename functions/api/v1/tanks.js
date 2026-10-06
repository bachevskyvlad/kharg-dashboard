const TRIAL_USERS = [];
const API_SECRET  = "oilradar-api-secret-2024-kharg";

async function verifyApiKey(request) {
  const key = request.headers.get("X-API-Key") || new URL(request.url).searchParams.get("api_key");
  if (!key) return { ok: false, error: "Missing X-API-Key header" };
  let decoded;
  try { decoded = atob(key.replace(/-/g,"+").replace(/_/g,"/").replace(/\./g,"=")); } catch { return { ok: false, error: "Invalid API key" }; }
  const ci = decoded.indexOf(":");
  if (ci < 1) return { ok: false, error: "Invalid API key" };
  const email = decoded.slice(0, ci), provided = decoded.slice(ci + 1);
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey("raw", enc.encode(API_SECRET), { name:"HMAC", hash:"SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", k, enc.encode(email));
  const expected = Array.from(new Uint8Array(sig)).map(b=>b.toString(16).padStart(2,"0")).join("");
  if (provided !== expected) return { ok: false, error: "Invalid API key" };
  if (TRIAL_USERS.map(e=>e.toLowerCase()).includes(email.toLowerCase())) return { ok: false, error: "API access requires a paid plan" };
  return { ok: true, email };
}
function ok(d) { return new Response(JSON.stringify(d), { headers: { "Content-Type":"application/json","Access-Control-Allow-Origin":"*","Cache-Control":"no-store" } }); }
function err(msg, s=401) { return new Response(JSON.stringify({ error:msg }), { status:s, headers: { "Content-Type":"application/json","Access-Control-Allow-Origin":"*" } }); }
function csv(rows, cols) { return new Response([cols.join(","), ...rows.map(r=>cols.map(c=>r[c]??"").join(","))].join("\n"), { headers: { "Content-Type":"text/csv","Access-Control-Allow-Origin":"*" } }); }

const SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co";
const SUPABASE_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM";
const BARREL_PER_M3 = 6.2898;
const TANK_HEIGHT_MIN = 2;

export async function onRequest(context) {
  const { request } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  const auth = await verifyApiKey(request);
  if (!auth.ok) return err(auth.error);
  const url = new URL(request.url);
  const from = url.searchParams.get("from"), to = url.searchParams.get("to");
  const fmt = url.searchParams.get("format") || "json";
  const tankId = url.searchParams.get("tank_id");
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
    } catch { return err("Data unavailable", 503); }
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
  if (fmt === "csv") return csv(records, cols);
  return ok({ count: records.length, records });
}
