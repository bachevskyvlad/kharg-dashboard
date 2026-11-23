"""
oil_digest_job.py — run oil_digest and send new messages to Telegram.

Env vars (required):
  TG_BOT   — Telegram bot token (same as pipeline uses)
  TG_CHAT  — Telegram chat id (same as pipeline uses)

Optional:
  OD_STATE_DIR — directory for oil_digest_state.sqlite + inbox/
                 defaults to $HOME/.oil_digest (persistent across runs)
  OD_USE_SCHEDULE — 1 (default) or 0 to check all sources regardless of schedule

Usage:
  python scripts/oil_digest_job.py                   # normal daily run
  python scripts/oil_digest_job.py --no-schedule     # check all sources now
"""
import argparse
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oil_digest_job")

# ── locate state dir ──────────────────────────────────────────────────────────
_state_dir_default = Path(os.environ.get("HOME", "/tmp")) / ".oil_digest"
STATE_DIR = Path(os.environ.get("OD_STATE_DIR", _state_dir_default))
STATE_DIR.mkdir(parents=True, exist_ok=True)
INBOX_DIR = STATE_DIR / "oil_digest_inbox"


def _send_tg(token: str, chat_id: str, text: str) -> None:
    """Send one Telegram message, retry on 429."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(4):
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "text": text,
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 5))
                log.warning("429 Too Many Requests — retrying in %ds", retry_after)
                time.sleep(retry_after)
                continue
            raise RuntimeError(f"Telegram API error {e.code}: {body}") from e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-schedule", action="store_true", help="ignore schedule, check all sources")
    args = parser.parse_args()

    token = os.environ.get("TG_BOT", "")
    chat_id = os.environ.get("TG_CHAT", "")
    if not token:
        sys.exit("FATAL: TG_BOT is empty")
    if not chat_id:
        sys.exit("FATAL: TG_CHAT is empty")

    use_schedule = not args.no_schedule
    if os.environ.get("OD_USE_SCHEDULE") == "0":
        use_schedule = False

    # import from repo root (oil_digest/ package is at repo root)
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from oil_digest import Digest

    log.info("state dir: %s", STATE_DIR)
    log.info("use_schedule: %s", use_schedule)

    digest = Digest(
        state_path=STATE_DIR / "oil_digest_state.sqlite",
        inbox_dir=INBOX_DIR,
        use_schedule=use_schedule,
    )
    messages = digest.collect()
    log.info("messages to send: %d", len(messages))

    sent = 0
    for msg in messages:
        try:
            _send_tg(token, chat_id, msg.text)
        except Exception:
            log.exception("failed to send %s/%s — will retry next run", msg.source, msg.key)
            continue
        digest.commit(msg)
        sent += 1
        time.sleep(1)

    log.info("sent: %d", sent)


if __name__ == "__main__":
    main()
