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

export async function onRequest(context) {
  const { request } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  const auth = await verifyApiKey(request);
  if (!auth.ok) return err(auth.error);
  const url = new URL(request.url);
  const from = url.searchParams.get("from"), to = url.searchParams.get("to"), fmt = url.searchParams.get("format") || "json";
  let sbUrl = SUPABASE_URL + "/rest/v1/kharg_daily?select=date,vol_mn_bbl,vol_5d_ma,day_change,signal,pct_change_30d&order=date";
  if (from) sbUrl += "&date=gte." + from;
  if (to)   sbUrl += "&date=lte." + to;
  let rows;
  try { rows = await (await fetch(sbUrl, { headers: { apikey: SUPABASE_KEY, Authorization: "Bearer " + SUPABASE_KEY } })).json(); }
  catch { return err("Data unavailable", 503); }
  if (!Array.isArray(rows)) return err("Data unavailable", 503);
  const cols = ["date","vol_mn_bbl","vol_5d_ma","day_change","signal","pct_change_30d"];
  if (fmt === "csv") return csv(rows, cols);
  return ok({ count: rows.length, records: rows });
}
