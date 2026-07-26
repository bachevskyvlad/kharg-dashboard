"""
notify.py — SAR pass email alert via the /auth/notify-sar Cloudflare Worker.

Sends a POST to app.oilradar.io/auth/notify-sar with the latest pass metrics.
The Worker (functions/auth/notify-sar.js) forwards to Resend API → 5 recipients.

This module is called only when new_dates is non-empty — i.e. a genuinely new
SAR pass was processed. One email per pipeline run that yields new data.

fill_rate_7d calculation:
  Look back through the daily series to find the observation closest to 7 days
  before the current pass, then compute (vol_now - vol_7d_ago) / 7.
  Falls back to single-pass delta if history is too short.
  Positive = storage building. Negative = drawing.

NOTIFY_SECRET must be set as a GitHub Actions secret AND as a Cloudflare
Pages environment variable (same value). If the Worker returns 401, the
secret is missing or mismatched on the CF side.
"""

import json
import os
import urllib.request
import pandas as pd


def send_sar_alert(daily_total, new_dates):
    """
    Posts SAR pass metrics to the notify-sar endpoint.

    daily_total: DataFrame with columns 'day' (datetime) and 'vol_mn_bbl'.
    new_dates:   list of new pass dates (used as a truthiness gate only here).

    Failures are caught and printed as non-critical — the pipeline should
    never abort because an email failed to send.
    """
    if not new_dates:
        return

    notify_url    = os.environ.get("NOTIFY_URL", "https://app.oilradar.io/auth/notify-sar")
    notify_secret = os.environ.get("NOTIFY_SECRET")

    if not notify_secret:
        print("  SAR alert emails: NOTIFY_SECRET not set, skipped")
        return

    try:
        dates_sorted  = daily_total["day"].dt.strftime("%Y-%m-%d").tolist()
        values_sorted = daily_total["vol_mn_bbl"].round(3).tolist()

        vol_current = round(values_sorted[-1], 3)
        pass_date   = dates_sorted[-1]
        vol_prev    = round(values_sorted[-2], 3) if len(values_sorted) >= 2 else vol_current
        prev_date   = dates_sorted[-2]             if len(dates_sorted)  >= 2 else pass_date

        # Find the closest observation >= 7 days back for the 7d fill rate.
        # reversed() scan is O(n) but the series is short (~300 dates max).
        idx_7d = next(
            (i for i, d in enumerate(reversed(dates_sorted))
             if (pd.to_datetime(pass_date) - pd.to_datetime(d)).days >= 7),
            None
        )
        if idx_7d is not None:
            vol_7d_ago   = values_sorted[-(idx_7d + 1)]
            fill_rate_7d = round((vol_current - vol_7d_ago) / 7, 4)
        else:
            fill_rate_7d = round(vol_current - vol_prev, 4)

        payload = json.dumps({
            "pass_date":    pass_date,
            "vol_current":  vol_current,
            "vol_prev":     vol_prev,
            "prev_date":    prev_date,
            "fill_rate_7d": fill_rate_7d,
        }).encode()

        req = urllib.request.Request(
            notify_url,
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {notify_secret}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"  SAR alert emails: {resp.read().decode()}")

    except Exception as e:
        print(f"  [!] SAR alert email failed (non-critical): {e}")
