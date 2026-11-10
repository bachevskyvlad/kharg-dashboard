const SB_URL = "https://yoekhkczwdeyauwarsyy.supabase.co";
const SB_KEY  = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM";
const H       = { apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}` };

function ok(d)   { return new Response(JSON.stringify(d), { headers: { "Content-Type":"application/json","Access-Control-Allow-Origin":"*","Cache-Control":"s-maxage=3600,public" } }); }
function err(msg, s=500) { return new Response(JSON.stringify({ error:msg }), { status:s, headers: { "Content-Type":"application/json","Access-Control-Allow-Origin":"*" } }); }

export async function onRequest(context) {
  const { request } = context;
  if (request.method === "OPTIONS") return new Response(null, { status:204, headers:{"Access-Control-Allow-Origin":"*"} });

  const url = new URL(request.url);
  const from = url.searchParams.get("from") || "2024-01-01";
  const to   = url.searchParams.get("to")   || new Date().toISOString().slice(0,10);

  try {
    const r = await fetch(
      `${SB_URL}/rest/v1/hormuz_weekly?select=week,passes,pairs,large_avg,all_avg,stay_share,gone_per_day,dwell_days`
      + `&week=gte.${from}&week=lte.${to}&order=week`,
      { headers: H }
    );
    if (!r.ok) return err("Supabase error", 502);
    const rows = await r.json();
    if (!Array.isArray(rows)) return err("Bad data", 502);
    return ok({ count: rows.length, records: rows });
  } catch (e) {
    return err(String(e));
  }
}
