"""
Fetch a single Sentinel-2 image for a specific date and append to existing viewer.
Usage: python scripts/fetch_single_s2.py 2026-07-20
"""
import os, sys, base64, requests, re
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

SH_CLIENT_ID     = "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

BBOX   = [50.290, 29.195, 50.370, 29.270]
WIDTH, HEIGHT = 700, 500
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-20"

EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: ["B04","B03","B02","dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [3.5*s.B04, 3.5*s.B03, 3.5*s.B02, s.dataMask];
}
"""

def get_token():
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID,
        "client_secret": SH_CLIENT_SECRET,
    })
    r.raise_for_status()
    return r.json()["access_token"]

def fetch_image(token, date_from, date_to):
    payload = {
        "input": {
            "bounds": {"bbox": BBOX, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": date_from + "T00:00:00Z", "to": date_to + "T23:59:59Z"},
                    "mosaickingOrder": "leastCC",
                    "maxCloudCoverage": 50,
                },
            }],
        },
        "output": {"width": WIDTH, "height": HEIGHT,
                   "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": EVALSCRIPT,
    }
    r = requests.post(SH_PROCESS_URL, json=payload,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return None
    return Image.open(BytesIO(r.content)).convert("RGB")

def add_label(img, label):
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, HEIGHT - 40), (WIDTH, HEIGHT)], fill=(0, 0, 0))
    try:
        font  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        font = small = ImageFont.load_default()
    draw.text((14, HEIGHT - 34), label, fill=(255, 255, 255), font=font)
    draw.text((WIDTH - 220, HEIGHT - 30), "Kharg Island · Sentinel-2", fill=(160, 160, 160), font=small)
    return img

def img_to_b64(img):
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()

# ── Fetch
print(f"Fetching S2 for {TARGET_DATE}…")
token = get_token()
d = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
# ±2 day window to catch the nearest pass
img = fetch_image(token, (d - timedelta(days=1)).strftime("%Y-%m-%d"),
                         (d + timedelta(days=1)).strftime("%Y-%m-%d"))

if img is None:
    # widen window to ±5 days
    print("No image in ±1d, trying ±5d…")
    img = fetch_image(token, (d - timedelta(days=5)).strftime("%Y-%m-%d"),
                             (d + timedelta(days=5)).strftime("%Y-%m-%d"))

if img is None:
    print("No image found. Exiting.")
    sys.exit(1)

label = d.strftime("%d %b %Y")
img = add_label(img, label)
b64 = img_to_b64(img)
new_entry = f'  {{ date: "{label}", src: "data:image/png;base64,{b64}" }}'

print(f"Got image for {label}. Injecting into viewer…")

viewer_path = "public/kharg_viewer.html"
with open(viewer_path, "r") as f:
    html = f.read()

# Find the frames array and append the new entry (before closing bracket)
# Pattern: last entry before the closing ]
pattern = r'(\[\n)([\s\S]*?)(\n\])'
match = re.search(pattern, html)
if not match:
    print("Could not find frames array in HTML")
    sys.exit(1)

existing = match.group(2).rstrip()
# Check if already present
if label in existing:
    print(f"{label} already in viewer, skipping.")
    sys.exit(0)

updated = match.group(1) + existing + ",\n" + new_entry + match.group(3)
html = html[:match.start()] + updated + html[match.end():]

with open(viewer_path, "w") as f:
    f.write(html)

print(f"Done — {label} added to {viewer_path}")
