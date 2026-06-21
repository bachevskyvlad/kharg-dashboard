"""
AIS Vessel Collector — Gulf Oil Terminals
Connects to aisstream.io WebSocket, collects vessel positions
near Kharg, Ras Tanura, Fujairah, Mina Ahmadi, saves to Supabase.
Runs for RUN_DURATION seconds then exits (cron-friendly).
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

RUN_DURATION = int(os.environ.get("AIS_RUN_DURATION", "60"))  # seconds

# TEST MODE: English Channel — very busy, 24/7 traffic
# TODO: switch back to Gulf terminals after key verification
TERMINALS = {
    "English_Channel": [50.0, 0.0, 52.0, 3.0],
}

TANKER_TYPES = set(range(80, 90))


def terminal_for_pos(lat, lon):
    for name, (min_lat, min_lon, max_lat, max_lon) in TERMINALS.items():
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return name
    return None


async def collect():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    records = []
    seen = set()

    # Each bbox in its own group so they are ORed (not ANDed)
    bboxes = [[[min_lat, min_lon, max_lat, max_lon]]
               for min_lat, min_lon, max_lat, max_lon in TERMINALS.values()]

    subscription = {
        "APIKey": AIS_API_KEY,
        "BoundingBoxes": bboxes,
        "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"]
    }

    url = "wss://stream.aisstream.io/v0/stream"
    print(f"[TEST MODE] English Channel bbox to verify API key")
    print(f"Connecting to {url}, will collect for {RUN_DURATION}s ...")
    print(f"Subscription: {json.dumps(subscription, indent=2)}")
    sys.stdout.flush()

    total_msgs = 0

    try:
        async with websockets.connect(url, ping_interval=20) as ws:
            await ws.send(json.dumps(subscription))
            print("Subscribed. Listening...")
            sys.stdout.flush()

            deadline = asyncio.get_event_loop().time() + RUN_DURATION

            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    elapsed = RUN_DURATION - (deadline - asyncio.get_event_loop().time())
                    print(f"  [timeout] {elapsed:.0f}s elapsed, {total_msgs} msgs so far")
                    sys.stdout.flush()
                    continue

                total_msgs += 1
                msg = json.loads(raw)

                if total_msgs <= 5:
                    print(f"  [RAW MSG #{total_msgs}]: {json.dumps(msg)[:300]}")
                    sys.stdout.flush()

                if total_msgs >= 5:
                    print(f"Got {total_msgs} messages — API key is WORKING!")
                    sys.stdout.flush()
                    break

    except Exception as e:
        print(f"WebSocket error: {e}")
        sys.exit(1)

    print(f"\n=== RESULT ===")
    print(f"Total messages received: {total_msgs}")
    if total_msgs == 0:
        print("API KEY PROBLEM — no data received even from English Channel")
        sys.exit(1)
    else:
        print("API key OK — the Gulf zones just had no traffic at collection time")


if __name__ == "__main__":
    asyncio.run(collect())
