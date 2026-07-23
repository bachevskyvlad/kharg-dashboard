"""
Fetch Sentinel-2 true-color images of Kharg Island for the past 12 months
and assemble into an animated GIF.
"""
import os, json, requests
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

SH_CLIENT_ID     = "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
SH_CLIENT_SECRET = "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Kharg Island bounding box
BBOX = [50.255, 29.215, 50.395, 29.315]  # [W, S, E, N]
WIDTH, HEIGHT = 600, 400

# ── 1. Auth
def get_token():
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID,
        "client_secret": SH_CLIENT_SECRET,
    })
    r.raise_for_status()
    return r.json()["access_token"]

# ── 2. Fetch one true-color image for a date range
EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: ["B04","B03","B02","dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [3.5*s.B04, 3.5*s.B03, 3.5*s.B02, s.dataMask];
}
"""

def fetch_image(token, date_from, date_to):
    payload = {
        "input": {
            "bounds": {"bbox": BBOX, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": date_from + "T00:00:00Z", "to": date_to + "T23:59:59Z"},
                    "mosaickingOrder": "leastCC",
                    "maxCloudCoverage": 30,
                },
            }],
        },
        "output": {"width": WIDTH, "height": HEIGHT, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": EVALSCRIPT,
    }
    r = requests.post(SH_PROCESS_URL, json=payload,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if r.status_code != 200:
        print(f"  [!] {date_from}: HTTP {r.status_code} — {r.text[:200]}")
        return None
    return Image.open(BytesIO(r.content)).convert("RGBA")

def add_label(img, label):
    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)
    # semi-transparent black bar at bottom
    draw.rectangle([(0, HEIGHT - 36), (WIDTH, HEIGHT)], fill=(0, 0, 0, 180))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except:
        font = ImageFont.load_default()
    draw.text((12, HEIGHT - 30), label, fill=(255, 255, 255), font=font)
    draw.text((WIDTH - 180, HEIGHT - 30), "Kharg Island · S2", fill=(180, 180, 180), font=font)
    return out

# ── 3. Build monthly intervals for past 12 months
end   = datetime.utcnow()
start = end - timedelta(days=365)

intervals = []
cur = start.replace(day=1)
while cur < end:
    nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    intervals.append((cur.strftime("%Y-%m-%d"), min(nxt - timedelta(days=1), end).strftime("%Y-%m-%d")))
    cur = nxt

print(f"Fetching {len(intervals)} monthly intervals…")
token = get_token()

frames = []
for d_from, d_to in intervals:
    print(f"  {d_from} → {d_to}", end=" ", flush=True)
    img = fetch_image(token, d_from, d_to)
    if img is None:
        print("skip")
        continue
    label = datetime.strptime(d_from, "%Y-%m-%d").strftime("%b %Y")
    frame = add_label(img, label)
    frames.append(frame)
    print("ok")

if len(frames) < 2:
    print("Not enough frames — check cloud cover or date range")
    exit(1)

# ── 4. Save GIF
os.makedirs("public", exist_ok=True)
out_path = "public/kharg_animation.gif"
frames[0].save(
    out_path,
    save_all=True,
    append_images=frames[1:],
    duration=800,       # ms per frame
    loop=0,
    optimize=False,
)
print(f"\nSaved {len(frames)}-frame GIF → {out_path}")
print(f"File size: {os.path.getsize(out_path) / 1024:.0f} KB")
