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

  // Simple shared-secret auth
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
    pass_date,       // "2026-07-26"
    pass_number,     // 309
    vol_current,     // 21.33  (Mb)
    vol_prev,        // 20.54  (Mb)
    prev_date,       // "2026-07-20"
    fill_rate_7d,    // 0.129  (Mb/d, positive = build)
    capacity = 33.2
  } = body;

  const resendKey = env.RESEND_API_KEY;
  if (!resendKey) return new Response(JSON.stringify({ ok: false, error: 'RESEND_API_KEY not set' }), { status: 500 });

  const delta = (vol_current - vol_prev).toFixed(3);
  const deltaSign = delta >= 0 ? '+' : '';
  const isBuilding = fill_rate_7d >= 0;
  const signal = isBuilding ? 'Building' : 'Drawing';
  const signalColor = isBuilding ? '#dc2626' : '#059669';
  const barBg = isBuilding ? '#fff5f5' : '#f0fdf4';
  const barBorder = isBuilding ? '#dc2626' : '#059669';
  const utilization = ((vol_current / capacity) * 100).toFixed(1);
  const daysToCap = isBuilding && fill_rate_7d > 0
    ? Math.round((capacity - vol_current) / fill_rate_7d)
    : null;
  const rateAbs = Math.abs(fill_rate_7d).toFixed(3);
  const rateSign = isBuilding ? '+' : '−';
  const passNum = pass_number ? `SAR PASS #${pass_number} · ` : '';

  const subject = `⚡ Kharg Island · New SAR pass · ${vol_current} Mb · ${signal} ${rateSign}${rateAbs} Mb/d`;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OilRadar SAR Alert</title>
</head>
<body style="margin:0;padding:0;background:#f0f0ed;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:2rem 1rem 3rem;">

  <!-- Email card -->
  <div style="background:#ffffff;border-radius:6px;overflow:hidden;box-shadow:0 2px 24px rgba(0,0,0,0.1);">

    <!-- Header -->
    <div style="background:#0a0e17;padding:1.4rem 2rem;display:flex;justify-content:space-between;align-items:center;">
      <div style="font-family:'Courier New',monospace;font-size:0.9rem;font-weight:700;letter-spacing:0.2em;color:#3b82f6;">OILRADAR</div>
      <div style="text-align:right;">
        <div style="font-family:'Courier New',monospace;font-size:0.65rem;color:#475569;letter-spacing:0.05em;">${pass_date} · ${new Date().toISOString().slice(11,16)} UTC</div>
        <div style="font-size:0.6rem;color:#334155;letter-spacing:0.08em;text-transform:uppercase;margin-top:0.2rem;">${passNum}SENTINEL-1</div>
      </div>
    </div>

    <!-- Signal bar -->
    <div style="background:${barBg};border-left:3px solid ${barBorder};padding:1.1rem 2rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;">
      <div>
        <div style="font-size:0.58rem;letter-spacing:0.12em;text-transform:uppercase;color:#6b7280;font-weight:600;margin-bottom:0.3rem;">Terminal signal</div>
        <div style="font-size:1rem;font-weight:600;color:${signalColor};">${signal}</div>
        <div style="font-family:'Courier New',monospace;font-size:0.75rem;color:#6b7280;margin-top:0.2rem;">${rateSign}${rateAbs} Mb/d · 7-day fill rate</div>
      </div>
      <div style="text-align:right;">
        <div style="font-family:'Courier New',monospace;font-size:0.7rem;color:#374151;font-weight:600;">${deltaSign}${delta} Mb</div>
        <div style="font-size:0.6rem;color:#9ca3af;margin-top:0.15rem;">vs prev pass · ${prev_date}</div>
      </div>
    </div>

    <!-- Metrics row -->
    <table width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid #f3f4f6;">
      <tr>
        <td width="25%" style="padding:1.1rem 1rem 1rem;border-right:1px solid #f3f4f6;vertical-align:top;">
          <div style="font-size:0.52rem;letter-spacing:0.1em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.45rem;">Storage</div>
          <div style="font-family:'Courier New',monospace;font-size:1.1rem;font-weight:700;color:#111827;line-height:1;">${vol_current}<span style="font-size:0.62rem;color:#9ca3af;font-weight:400;"> Mb</span></div>
          <div style="font-size:0.65rem;margin-top:0.35rem;font-family:'Courier New',monospace;color:${signalColor};">${deltaSign}${delta} vs prev</div>
        </td>
        <td width="25%" style="padding:1.1rem 1rem 1rem;border-right:1px solid #f3f4f6;vertical-align:top;">
          <div style="font-size:0.52rem;letter-spacing:0.1em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.45rem;">Utilization</div>
          <div style="font-family:'Courier New',monospace;font-size:1.1rem;font-weight:700;color:#111827;line-height:1;">${utilization}<span style="font-size:0.62rem;color:#9ca3af;font-weight:400;">%</span></div>
          <div style="font-size:0.65rem;margin-top:0.35rem;font-family:'Courier New',monospace;color:#9ca3af;">of ${capacity} Mb cap</div>
        </td>
        <td width="25%" style="padding:1.1rem 1rem 1rem;border-right:1px solid #f3f4f6;vertical-align:top;">
          <div style="font-size:0.52rem;letter-spacing:0.1em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.45rem;">Fill rate · 7d</div>
          <div style="font-family:'Courier New',monospace;font-size:1.1rem;font-weight:700;color:#111827;line-height:1;">${rateSign}${rateAbs}<span style="font-size:0.62rem;color:#9ca3af;font-weight:400;"> Mb/d</span></div>
          <div style="font-size:0.65rem;margin-top:0.35rem;font-family:'Courier New',monospace;color:${signalColor};">${isBuilding ? '▲ Build streak' : '▼ Draw streak'}</div>
        </td>
        <td width="25%" style="padding:1.1rem 1rem 1rem;vertical-align:top;">
          <div style="font-size:0.52rem;letter-spacing:0.1em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.45rem;">${isBuilding ? 'Days to cap' : 'Days to floor'}</div>
          <div style="font-family:'Courier New',monospace;font-size:1.1rem;font-weight:700;color:#111827;line-height:1;">${daysToCap !== null ? daysToCap : '—'}<span style="font-size:0.62rem;color:#9ca3af;font-weight:400;"> d</span></div>
          <div style="font-size:0.65rem;margin-top:0.35rem;font-family:'Courier New',monospace;color:#059669;">at current rate</div>
        </td>
      </tr>
    </table>

    <!-- Body text -->
    <div style="padding:1.25rem 2rem 1.5rem;border-bottom:1px solid #f3f4f6;">
      <p style="font-size:0.82rem;color:#374151;line-height:1.65;margin:0;">
        Kharg Island storage reached <strong style="color:#111827;">${vol_current} Mb</strong> on the ${pass_date} pass —
        a <strong style="color:${signalColor};">${deltaSign}${delta} Mb ${isBuilding ? 'gain' : 'drop'}</strong> over the prior observation.
        7-day fill rate is <strong style="color:${signalColor};">${rateSign}${rateAbs} Mb/d</strong>.
        Utilization is <strong style="color:#111827;">${utilization}%</strong> of the 33.2 Mb terminal capacity.
        ${daysToCap !== null && isBuilding ? `At current pace, the terminal reaches 85% critical threshold in approximately <strong style="color:#111827;">${Math.round((0.85 * capacity - vol_current) / fill_rate_7d)} days</strong>.` : ''}
      </p>
    </div>

    <!-- CTA -->
    <div style="padding:1.25rem 2rem;display:flex;align-items:center;gap:1rem;">
      <a href="https://app.oilradar.io/dashboard" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;font-size:0.75rem;font-weight:600;padding:0.6rem 1.25rem;border-radius:4px;letter-spacing:0.03em;">Open dashboard →</a>
      <span style="font-size:0.68rem;color:#9ca3af;">Per-tank breakdown · Full history · Gross flows</span>
    </div>

    <!-- Footer -->
    <div style="background:#f9f9f7;border-top:1px solid #e5e7eb;padding:1rem 2rem;display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:0.6rem;color:#9ca3af;line-height:1.6;">
        OilRadar · Kharg Island Intelligence<br>
        You receive this because your account is on the authorized list.
      </div>
      <div style="font-family:'Courier New',monospace;font-size:0.55rem;color:#d1d5db;letter-spacing:0.1em;">oilradar.io</div>
    </div>

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
