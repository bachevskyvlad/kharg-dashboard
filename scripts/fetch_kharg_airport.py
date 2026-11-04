"""
Fetch weekly Sentinel-2 images of Kharg Island airport area (northern part)
from Jan 2026 to today and build an interactive HTML viewer.
Usage: python scripts/fetch_kharg_airport.py
"""
import os, base64, requests, sys
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

SH_CLIENT_ID     = os.environ.get("CLIENT_ID", "")
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Northern Kharg — airport area
BBOX = [50.270, 29.240, 50.390, 29.315]  # [W, S, E, N]
WIDTH, HEIGHT = 800, 500

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
                    "maxCloudCoverage": 30,
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
        print(f"  HTTP {r.status_code}: {r.text[:200]}")
        return None
    img = Image.open(BytesIO(r.content)).convert("RGB")
    pixels = list(img.getdata())
    if sum(sum(p) for p in pixels[:300]) < 200:
        print("  blank/no data")
        return None
    return img

def add_label(img, label):
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, HEIGHT - 40), (WIDTH, HEIGHT)], fill=(0, 0, 0))
    try:
        font  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        font = small = ImageFont.load_default()
    draw.text((14, HEIGHT - 34), label, fill=(255, 255, 255), font=font)
    draw.text((WIDTH - 280, HEIGHT - 30), "Kharg Airport · Sentinel-2", fill=(160, 160, 160), font=small)
    return img

def img_to_b64(img):
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()

# Weekly intervals Jan 1 2026 → today
start = datetime(2026, 1, 1)
end   = datetime.utcnow()

intervals = []
cur = start
while cur < end:
    nxt = cur + timedelta(days=7)
    intervals.append((cur.strftime("%Y-%m-%d"), min(nxt, end).strftime("%Y-%m-%d")))
    cur = nxt

print(f"Fetching {len(intervals)} weekly intervals (Jan 2026 → {end.strftime('%Y-%m-%d')})…")
token = get_token()

frames = []
for d_from, d_to in intervals:
    print(f"  {d_from} → {d_to}", end=" … ", flush=True)
    img = fetch_image(token, d_from, d_to)
    if img is None:
        print("skip")
        continue
    label = datetime.strptime(d_from, "%Y-%m-%d").strftime("%d %b %Y")
    img = add_label(img, label)
    frames.append((label, img_to_b64(img)))
    print(f"ok")

print(f"\n{len(frames)} frames collected")
if len(frames) < 1:
    print("No frames. Exiting.")
    sys.exit(1)

imgs_js = "[\n" + ",\n".join(
    f'  {{ date: "{d}", src: "data:image/png;base64,{b}" }}'
    for d, b in frames
) + "\n]"

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Kharg Airport · Sentinel-2 Weekly 2026</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0a0a0a; color: #eee; font-family: system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 24px 16px; }}
  h1 {{ font-size: 18px; font-weight: 600; letter-spacing: .04em;
        color: #ccc; margin-bottom: 6px; }}
  .sub {{ font-size: 13px; color: #666; margin-bottom: 20px; }}
  #viewer {{ position: relative; width: 800px; max-width: 100%; }}
  #viewer img {{ width: 100%; border-radius: 6px; display: block; }}
  .controls {{ display: flex; align-items: center; gap: 12px;
               margin-top: 14px; justify-content: center; }}
  button {{ background: #1e1e1e; border: 1px solid #333; color: #eee;
            padding: 8px 22px; border-radius: 5px; font-size: 15px;
            cursor: pointer; transition: background .15s; }}
  button:hover {{ background: #2a2a2a; }}
  button:disabled {{ opacity: .3; cursor: default; }}
  #counter {{ font-size: 13px; color: #666; min-width: 80px; text-align: center; }}
  #date-label {{ font-size: 16px; font-weight: 600; color: #fff;
                 min-width: 130px; text-align: center; }}
  input[type=range] {{ width: 600px; max-width: 90vw; accent-color: #4a9eff; }}
</style>
</head>
<body>
<h1>Kharg Island · Airport Area</h1>
<div class="sub">Sentinel-2 L2A · True Color · Weekly · Jan 2026 – Jul 2026</div>
<div id="viewer"><img id="frame" src="" alt=""></div>
<div class="controls">
  <button id="prev" onclick="step(-1)">&#8592;</button>
  <div id="date-label"></div>
  <button id="next" onclick="step(1)">&#8594;</button>
</div>
<div class="controls" style="margin-top:10px">
  <input type="range" id="slider" min="0" value="0" oninput="show(+this.value)">
  <div id="counter"></div>
</div>
<script>
const frames = {imgs_js};
let cur = 0;
const slider = document.getElementById('slider');
slider.max = frames.length - 1;
function show(i) {{
  cur = Math.max(0, Math.min(frames.length - 1, i));
  document.getElementById('frame').src = frames[cur].src;
  document.getElementById('date-label').textContent = frames[cur].date;
  document.getElementById('counter').textContent = (cur+1) + ' / ' + frames.length;
  slider.value = cur;
  document.getElementById('prev').disabled = cur === 0;
  document.getElementById('next').disabled = cur === frames.length - 1;
}}
function step(d) {{ show(cur + d); }}
document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowLeft') step(-1);
  if (e.key === 'ArrowRight') step(1);
}});
show(frames.length - 1);
</script>
</body>
</html>"""

os.makedirs("public", exist_ok=True)
out_path = "public/kharg_airport_viewer.html"
with open(out_path, "w") as f:
    f.write(html)
size_mb = os.path.getsize(out_path) / 1024 / 1024
print(f"Saved → {out_path}  ({size_mb:.1f} MB)")
