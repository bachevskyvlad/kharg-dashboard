"""
hormuz_weekly_update.py — Weekly Hormuz queue update (cron entry point).

Runs every Tuesday. Fetches new SAR passes since last DB entry,
runs v1.1 tracking, writes to hormuz_passes / hormuz_ships / hormuz_weekly.
Sends Telegram alert on completion or error.

Usage (cron):
  0 3 * * 2  cd /workspace && python scripts/hormuz_weekly_update.py

Required env:
  SUPABASE_URL          (optional, default hardcoded)
  SUPABASE_SERVICE_KEY  (required; exits with Telegram alert if missing)
  TG_BOT                (optional; Telegram alerts)
  TG_CHAT               (optional)
"""

import os
import sys
import subprocess
import datetime

TG_BOT  = os.environ.get("TG_BOT", "")
TG_CHAT = os.environ.get("TG_CHAT", "")


def tg(text):
    if not TG_BOT or not TG_CHAT:
        print("[TG-SKIP]", text)
        return
    import urllib.request, json
    payload = json.dumps({"chat_id": TG_CHAT, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_BOT}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("[TG-ERR]", e)


def main():
    if not os.environ.get("SUPABASE_SERVICE_KEY"):
        tg("⚠️ Hormuz weekly: SUPABASE_SERVICE_KEY not set — skipping write")
        sys.exit(0)  # not fatal for cron

    script = os.path.join(os.path.dirname(__file__), "hormuz_queue_backfill.py")
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    tg(f"🛰 Hormuz queue update started {ts}")

    try:
        result = subprocess.run(
            [sys.executable, script, "--weekly", "--commit"],
            capture_output=True, text=True, timeout=3600,
        )
        if result.returncode == 0:
            tg(f"✅ Hormuz queue updated {ts}\n{result.stdout[-300:]}")
        else:
            tg(f"❌ Hormuz queue FAILED {ts}\n{result.stderr[-400:]}")
            sys.exit(1)
    except subprocess.TimeoutExpired:
        tg(f"⏱ Hormuz queue timeout after 1h {ts}")
        sys.exit(1)
    except Exception as e:
        tg(f"❌ Hormuz queue error {ts}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
