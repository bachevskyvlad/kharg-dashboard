const RECIPIENTS = [
  'vladislav.bachevsky@gmail.com',
  'keepmeup2date@gmail.com',
  'vvbobylev@gmail.com',
  'fryday.kg@gmail.com',
  'cyril@bluewaterstrategy.eu'
];

export async function onRequest(context) {
  const { request, env } = context;

  if (request.method !== 'POST') {
    return new Response('Method not allowed', { status: 405 });
  }

  const authHeader = request.headers.get('Authorization') || '';
  const notifySecret = env.NOTIFY_SECRET;
  if (!notifySecret || authHeader !== `Bearer ${notifySecret}`) {
    return new Response('Unauthorized', { status: 401 });
  }

  let body;
  try { body = await request.json(); } catch {
    return new Response(JSON.stringify({ ok: false, error: 'Invalid body' }), { status: 400 });
  }

  const {
    pass_date,
    vol_current,
    vol_prev,
    prev_date,
    fill_rate_7d,
    capacity = 31.78
  } = body;

  const resendKey = env.RESEND_API_KEY;
  if (!resendKey) return new Response(JSON.stringify({ ok: false, error: 'RESEND_API_KEY not set' }), { status: 500 });

  const isBuilding = fill_rate_7d >= 0;
  const utilNow = ((vol_current / capacity) * 100).toFixed(1);
  const utilPrev = ((vol_prev / capacity) * 100).toFixed(1);
  const signalWord = isBuilding ? 'up' : 'down';
  const signalColor = isBuilding ? '#dc2626' : '#059669';

  const subject = `Kharg Island · New SAR pass · ${pass_date} · storage ${signalWord} to ${utilNow}%`;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OilRadar SAR Alert</title>
</head>
<body style="margin:0;padding:0;background:#f0f0ed;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:540px;margin:0 auto;padding:2rem 1rem 3rem;">
<div style="background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 2px 24px rgba(0,0,0,0.1);">

  <!-- Header -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0e17;">
    <tr>
      <td style="padding:1.1rem 1.75rem;">
        <div style="font-family:'Courier New',monospace;font-size:0.85rem;font-weight:700;letter-spacing:0.2em;color:#3b82f6;margin-bottom:0.3rem;">OILRADAR</div>
        <div style="font-size:0.75rem;color:#94a3b8;">New SAR pass · Kharg Island · ${pass_date}</div>
      </td>
      <td align="right" style="padding:1.1rem 1.75rem;font-family:'Courier New',monospace;font-size:0.62rem;color:#475569;vertical-align:top;">${new Date().toISOString().slice(11,16)} UTC</td>
    </tr>
  </table>

  <!-- Body -->
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:1.75rem 1.75rem 1.5rem;">

        <p style="font-size:0.9rem;color:#374151;line-height:1.6;margin:0 0 1.5rem;">
          A new satellite pass over Kharg Island has been processed.<br>
          Storage is <strong style="color:${signalColor};">${signalWord}</strong> since the previous observation.
        </p>

        <!-- Two big numbers -->
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:1.5rem;">
          <tr>
            <td width="50%" style="padding-right:1rem;vertical-align:top;">
              <div style="font-size:0.5rem;letter-spacing:0.12em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.4rem;">Now</div>
              <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:700;color:#111827;line-height:1;">${utilNow}<span style="font-size:1rem;color:#6b7280;font-weight:400;">%</span></div>
              <div style="font-size:0.72rem;color:#6b7280;margin-top:0.3rem;font-family:'Courier New',monospace;">${vol_current} Mb</div>
            </td>
            <td width="50%" style="border-left:1px solid #f3f4f6;padding-left:1rem;vertical-align:top;">
              <div style="font-size:0.5rem;letter-spacing:0.12em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.4rem;">Prev pass · ${prev_date}</div>
              <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:700;color:#9ca3af;line-height:1;">${utilPrev}<span style="font-size:1rem;font-weight:400;">%</span></div>
              <div style="font-size:0.72rem;color:#9ca3af;margin-top:0.3rem;font-family:'Courier New',monospace;">${vol_prev} Mb</div>
            </td>
          </tr>
        </table>

        <!-- Progress bar -->
        <div style="position:relative;height:6px;background:#f3f4f6;border-radius:3px;margin-bottom:0.4rem;">
          <div style="position:absolute;left:85%;top:-2px;width:1px;height:10px;background:#fca5a5;"></div>
          <div style="position:absolute;left:0;top:0;width:${utilPrev}%;height:6px;background:#dbeafe;border-radius:3px;"></div>
          <div style="position:absolute;left:0;top:0;width:${utilNow}%;height:6px;background:#3b82f6;border-radius:3px;"></div>
        </div>
        <div style="text-align:right;font-size:0.55rem;color:#fca5a5;font-family:'Courier New',monospace;margin-bottom:0;">critical threshold: 85%</div>

      </td>
    </tr>
  </table>

  <div style="height:1px;background:#f3f4f6;"></div>

  <!-- CTA -->
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:1.1rem 1.75rem;">
        <a href="https://app.oilradar.io/dashboard" style="display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;font-size:0.75rem;font-weight:600;padding:0.55rem 1.1rem;border-radius:4px;letter-spacing:0.03em;">Open dashboard →</a>
      </td>
    </tr>
  </table>

  <!-- Footer -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9f9f7;border-top:1px solid #e5e7eb;">
    <tr>
      <td style="padding:0.85rem 1.75rem;font-size:0.58rem;color:#9ca3af;line-height:1.55;">
        OilRadar · Kharg Island<br>
        <a href="#" style="color:#9ca3af;">Unsubscribe</a>
      </td>
      <td align="right" style="padding:0.85rem 1.75rem;font-family:'Courier New',monospace;font-size:0.52rem;color:#d1d5db;letter-spacing:0.1em;">oilradar.io</td>
    </tr>
  </table>

</div>
</div>
</body>
</html>`;

  const results = [];
  for (const to of RECIPIENTS) {
    const res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${resendKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: 'OilRadar <noreply@oilradar.io>', to: [to], subject, html })
    });
    results.push({ to, ok: res.ok, status: res.status });
    if (!res.ok) console.error(`Resend error for ${to}:`, await res.text());
  }

  const allOk = results.every(r => r.ok);
  return new Response(JSON.stringify({ ok: allOk, results }), {
    status: allOk ? 200 : 207,
    headers: { 'Content-Type': 'application/json' }
  });
}
