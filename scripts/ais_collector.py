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

RUN_DURATION = int(os.environ.get("AIS_RUN_DURATION", "300"))  # seconds

TERMINALS = {
    "Kharg_Island":  [29.00, 50.10, 29.50, 50.60],
    "Ras_Tanura":    [26.40, 49.90, 26.90, 50.40],
    "Fujairah":      [25.00, 56.10, 25.50, 56.70],
    "Mina_Ahmadi":   [28.80, 47.80, 29.30, 48.40],
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

    bboxes = [[min_lat, min_lon, max_lat, max_lon]
               for min_lat, min_lon, max_lat, max_lon in TERMINALS.values()]

    subscription = {
        "APIKey": AIS_API_KEY,
        "BoundingBoxes": [bboxes],
        "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"]
    }

    url = "wss://stream.aisstream.io/v0/stream"
    print(f"Connecting to {url}, will collect for {RUN_DURATION}s ...")

    try:
        async with websockets.connect(url, ping_interval=20) as ws:
            await ws.send(json.dumps(subscription))
            print("Subscribed. Listening...")

            deadline = asyncio.get_event_loop().time() + RUN_DURATION

            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue

                msg = json.loads(raw)
                msg_type = msg.get("MessageType", "")
                if msg_type not in ("PositionReport", "StandardClassBPositionReport"):
                    continue

                meta = msg.get("MetaData", {})
                pos = msg.get("Message", {}).get(msg_type, {})

                mmsi = str(meta.get("MMSI", ""))
                lat = meta.get("latitude") or pos.get("Latitude")
                lon = meta.get("longitude") or pos.get("Longitude")

                if not mmsi or lat is None or lon is None:
                    continue

                terminal = terminal_for_pos(lat, lon)
                if not terminal:
                    continue

                vessel_type = pos.get("Type", 0) or 0
                speed = pos.get("Sog", 0)
                course = pos.get("Cog", 0)
                draught = pos.get("Draught", 0)
                name = meta.get("ShipName", "").strip()
                flag = meta.get("ShipType", "")
                ts = meta.get("time_utc") or datetime.now(timezone.utc).isoformat()

                key = f"{mmsi}_{terminal}_{ts[:13]}"
                if key in seen:
                    continue
                seen.add(key)

                record = {
                    "mmsi": mmsi,
                    "vessel_name": name,
                    "vessel_type": vessel_type,
                    "is_tanker": vessel_type in TANKER_TYPES,
                    "flag": str(flag),
                    "lat": float(lat),
                    "lon": float(lon),
                    "speed": float(speed or 0),
                    "course": float(course or 0),
                    "draught": float(draught or 0),
                    "terminal": terminal,
                    "timestamp": ts if isinstance(ts, str) else ts.isoformat(),
                }
                records.append(record)
                tanker_str = " [TANKER]" if vessel_type in TANKER_TYPES else ""
                print(f"  {terminal} | {name or mmsi}{tanker_str} | spd={speed:.1f}kn draught={draught:.1f}m")

    except Exception as e:
        print(f"WebSocket error: {e}")
        if not records:
            sys.exit(1)

    print(f"\nCollected {len(records)} positions. Saving to Supabase...")
    if records:
        try:
            sb.table("ais_positions").upsert(records, on_conflict="mmsi,timestamp").execute()
            print(f"Saved {len(records)} records.")
        except Exception as e:
            print(f"Supabase write error: {e}")
            sys.exit(1)
    else:
        print("No vessels detected in target zones this run.")


if __name__ == "__main__":
    asyncio.run(collect())
