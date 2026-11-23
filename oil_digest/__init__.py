"""Oil digest: collects short Telegram-ready summaries from UANI, China Customs, OPEC MOMR, JODI and the IEA OMR.

    from oil_digest import Digest
    d = Digest(state_path="oil_digest_state.sqlite", inbox_dir="inbox")
    for msg in d.collect():
        send_to_telegram(msg.text)      # HTML, <= 4096 chars
        d.commit(msg)                   # mark as sent only after a successful send
"""
from .core import Digest, Message

__all__ = ["Digest", "Message"]
__version__ = "0.1.0"
