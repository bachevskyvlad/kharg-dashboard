"""
Fetch Sentinel-2 images of southern Kharg Island (bi-weekly, past 12 months)
and build an interactive HTML viewer with prev/next controls.
"""
import os, base64, requests
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

SH_CLIENT_ID     = "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
SH_CLIENT_SECRET = "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Southern Kharg — tighter, more zoomed in
BBOX = [50.290, 29.195, 29.370, 29.270]  # placeholder, fix below
BBOX = [50.290, 29.195, 50.370, 29.270]  # [W, S, E, N]
WIDTH, HEIGHT = 700, 500

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
                    "maxCloudCoverage": 40,
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
        print(f"  HTTP {r.status_code}")
        return None, None
    img = Image.open(BytesIO(r.content)).convert("RGB")
    # detect blank (all-black) response
    pixels = list(img.getdata())
    if sum(sum(p) for p in pixels[:200]) < 100:
        print("  blank/no data")
        return None, None
    # get actual date from response header if available
    actual_date = r.headers.get("x-imagedate", date_from)
    return img, actual_date

def add_label(img, label):
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, HEIGHT - 40), (WIDTH, HEIGHT)], fill=(0, 0, 0))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        font = ImageFont.load_default()
        small = font
    draw.text((14, HEIGHT - 34), label, fill=(255, 255, 255), font=font)
    draw.text((WIDTH - 220, HEIGHT - 30), "Kharg Island · Sentinel-2", fill=(160, 160, 160), font=small)
    return img

def img_to_b64(img):
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()

# ── Build bi-weekly intervals for past 12 months
end   = datetime.utcnow()
start = end - timedelta(days=365)

intervals = []
cur = start
while cur < end:
    nxt = cur + timedelta(days=14)
    intervals.append((cur.strftime("%Y-%m-%d"), min(nxt, end).strftime("%Y-%m-%d")))
    cur = nxt

print(f"Fetching up to {len(intervals)} bi-weekly intervals…")
token = get_token()

frames = []   # list of (date_label, b64_png)
for d_from, d_to in intervals:
    print(f"  {d_from} → {d_to}", end=" … ", flush=True)
    img, actual = fetch_image(token, d_from, d_to)
    if img is None:
        continue
    label = datetime.strptime(d_from, "%Y-%m-%d").strftime("%d %b %Y")
    img = add_label(img, label)
    frames.append((label, img_to_b64(img)))
    print(f"ok ({label})")

print(f"\n{len(frames)} frames collected")
if len(frames) < 2:
    print("Not enough frames")
    exit(1)

# ── Build HTML viewer
imgs_js = "[\n" + ",\n".join(
    f'  {{ date: "{d}", src: "data:image/png;base64,{b}" }}'
    for d, b in frames
) + "\n]"

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Kharg Island · Sentinel-2 Viewer</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0a0a0a; color: #eee; font-family: system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 24px 16px; }}
  h1 {{ font-size: 18px; font-weight: 600; letter-spacing: .04em;
        color: #ccc; margin-bottom: 6px; }}
  .sub {{ font-size: 13px; color: #666; margin-bottom: 20px; }}
  #viewer {{ position: relative; width: 700px; max-width: 100%; }}
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
  input[type=range] {{ width: 480px; max-width: 90vw; accent-color: #4a9eff; }}
</style>
</head>
<body>
<h1>Kharg Island · Southern Tank Farm</h1>
<div class="sub">Sentinel-2 L2A · True Color · Jul 2025 – Jul 2026</div>
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
show(0);
</script>
</body>
</html>"""

os.makedirs("public", exist_ok=True)
out_path = "public/kharg_viewer.html"
with open(out_path, "w") as f:
    f.write(html)
size_mb = os.path.getsize(out_path) / 1024 / 1024
print(f"Saved → {out_path}  ({size_mb:.1f} MB)")
