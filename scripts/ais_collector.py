"""
AIS Vessel Collector — Gulf Oil Terminals
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import websockets
from supabase import create_client

AIS_API_KEY = "bced5e2b20ddeeb7107957a2bdc90ca3866ed6a0"

SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

RUN_DURATION = int(os.environ.get("AIS_RUN_DURATION", "60"))

# TEST: English Channel, no FilterMessageTypes
subscription = {
    "APIKey": AIS_API_KEY,
    "BoundingBoxes": [[[50.0, 0.0, 52.0, 3.0]]],
}


async def collect():
    url = "wss://stream.aisstream.io/v0/stream"
    print(f"Connecting to {url} for {RUN_DURATION}s")
    print(f"Subscription: {json.dumps(subscription)}")
    sys.stdout.flush()

    total_msgs = 0

    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps(subscription))
            print("Subscribed. Listening...")
            sys.stdout.flush()

            deadline = asyncio.get_event_loop().time() + RUN_DURATION

            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    elapsed = RUN_DURATION - (deadline - asyncio.get_event_loop().time())
                    print(f"  [timeout] {elapsed:.0f}s elapsed, {total_msgs} msgs")
                    sys.stdout.flush()
                    continue

                total_msgs += 1
                msg = json.loads(raw)
                print(f"  [MSG #{total_msgs}] type={msg.get('MessageType','?')} keys={list(msg.keys())}")
                sys.stdout.flush()

                if total_msgs >= 3:
                    print("Got messages — API key WORKS!")
                    sys.stdout.flush()
                    break

    except Exception as e:
        print(f"WebSocket error: {type(e).__name__}: {e}")
        sys.stdout.flush()
        sys.exit(1)

    print(f"\nTotal messages: {total_msgs}")
    if total_msgs == 0:
        print("STILL 0 — key issue or service down")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(collect())
