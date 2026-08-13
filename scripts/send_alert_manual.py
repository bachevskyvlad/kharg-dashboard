"""Manual SAR alert sender — called by send_sar_manual.yml workflow."""
import os, json
from urllib import request as ur, error as ue

pass_date    = os.environ["PASS_DATE"]
vol_current  = float(os.environ["VOL_CURRENT"])
vol_prev     = float(os.environ["VOL_PREV"])
prev_date    = os.environ["PREV_DATE"]
fill_rate_7d = float(os.environ["FILL_RATE_7D"])
resend_key   = os.environ["RESEND_API_KEY"]
capacity     = 31.78

util_now     = round(vol_current / capacity * 100, 1)
util_prev    = round(vol_prev    / capacity * 100, 1)
is_building  = fill_rate_7d >= 0
signal_word  = "up" if is_building else "down"
signal_color = "#dc2626" if is_building else "#059669"
subject = f"Kharg Island · New SAR pass · {pass_date} · storage {signal_word} to {util_now}%"

html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>OilRadar SAR Alert</title></head>
<body style="margin:0;padding:0;background:#f0f0ed;font-family:-apple-system,sans-serif;">
<div style="max-width:540px;margin:0 auto;padding:2rem 1rem 3rem;">
<div style="background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 2px 24px rgba(0,0,0,0.1);">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0e17;"><tr>
<td style="padding:1.1rem 1.75rem;">
  <div style="font-family:'Courier New',monospace;font-size:0.85rem;font-weight:700;letter-spacing:0.2em;color:#3b82f6;">OILRADAR</div>
  <div style="font-size:0.75rem;color:#94a3b8;">New SAR pass &middot; Kharg Island &middot; {pass_date}</div>
</td></tr></table>

<table width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:1.75rem;">
<p style="font-size:0.9rem;color:#374151;line-height:1.6;margin:0 0 1.5rem;">
  A new satellite pass over Kharg Island has been processed.<br>
  Storage is <strong style="color:{signal_color};">{signal_word}</strong> since the previous observation.
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:1.5rem;"><tr>
  <td width="50%" style="padding-right:1rem;vertical-align:top;">
    <div style="font-size:0.5rem;letter-spacing:0.12em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.4rem;">Now</div>
    <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:700;color:#111827;line-height:1;">{util_now}<span style="font-size:1rem;color:#6b7280;font-weight:400;">%</span></div>
    <div style="font-size:0.72rem;color:#6b7280;margin-top:0.3rem;font-family:'Courier New',monospace;">{vol_current} Mb</div>
  </td>
  <td width="50%" style="border-left:1px solid #f3f4f6;padding-left:1rem;vertical-align:top;">
    <div style="font-size:0.5rem;letter-spacing:0.12em;text-transform:uppercase;color:#9ca3af;font-weight:600;margin-bottom:0.4rem;">Prev pass &middot; {prev_date}</div>
    <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:700;color:#9ca3af;line-height:1;">{util_prev}<span style="font-size:1rem;font-weight:400;">%</span></div>
    <div style="font-size:0.72rem;color:#9ca3af;margin-top:0.3rem;font-family:'Courier New',monospace;">{vol_prev} Mb</div>
  </td>
</tr></table>

<div style="position:relative;height:6px;background:#f3f4f6;border-radius:3px;margin-bottom:0.4rem;">
  <div style="position:absolute;left:85%;top:-2px;width:1px;height:10px;background:#fca5a5;"></div>
  <div style="position:absolute;left:0;top:0;width:{util_prev}%;height:6px;background:#dbeafe;border-radius:3px;"></div>
  <div style="position:absolute;left:0;top:0;width:{util_now}%;height:6px;background:#3b82f6;border-radius:3px;"></div>
</div>
<div style="text-align:right;font-size:0.55rem;color:#fca5a5;font-family:'Courier New',monospace;">critical threshold: 85%</div>
</td></tr></table>

<div style="height:1px;background:#f3f4f6;"></div>
<table width="100%" cellpadding="0" cellspacing="0"><tr>
  <td style="padding:1.1rem 1.75rem;">
    <a href="https://app.oilradar.io/dashboard" style="display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;font-size:0.75rem;font-weight:600;padding:0.55rem 1.1rem;border-radius:4px;letter-spacing:0.03em;">Open dashboard &rarr;</a>
  </td>
</tr></table>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f9f9f7;border-top:1px solid #e5e7eb;"><tr>
  <td style="padding:0.85rem 1.75rem;font-size:0.58rem;color:#9ca3af;">OilRadar &middot; Kharg Island</td>
  <td align="right" style="padding:0.85rem 1.75rem;font-family:'Courier New',monospace;font-size:0.52rem;color:#d1d5db;">oilradar.io</td>
</tr></table>

</div></div></body></html>"""

recipients = [
    "vladislav.bachevsky@gmail.com",
    "cyril@bluewaterstrategy.eu",
    "fryday.kg@gmail.com",
]

for to in recipients:
    payload = json.dumps({
        "from": "OilRadar <noreply@oilradar.io>",
        "to": [to],
        "subject": subject,
        "html": html,
    }).encode()
    req = ur.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with ur.urlopen(req, timeout=15) as resp:
            print(f"OK {to}:", resp.read().decode())
    except ue.HTTPError as e:
        print(f"FAIL {to}: HTTP {e.code}", e.read().decode())
