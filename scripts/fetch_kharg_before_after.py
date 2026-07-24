import os, base64, requests, sys
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

SH_CLIENT_ID     = "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
SH_CLIENT_SECRET = "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

BBOX = [50.305, 29.248, 50.358, 29.278]
WIDTH, HEIGHT = 1000, 560

EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: ["B12","B11","B04","dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [2.5*s.B12, 2.5*s.B11, 2.5*s.B04, s.dataMask];
}
"""

WINDOWS = [
    ("2026-02-20", "2026-03-12", "До ударов",    "Before · Feb 20 – Mar 12"),
    ("2026-03-14", "2026-03-31", "После ударов", "After · Mar 14 – Mar 31"),
    ("2026-04-01", "2026-04-20", "Апрель 2026",  "April 2026"),
]

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
        print(f"  HTTP {r.status_code}: {r.text[:200]}")
        return None
    img = Image.open(BytesIO(r.content)).convert("RGB")
    pixels = list(img.getdata())
    if sum(sum(p) for p in pixels[:300]) < 200:
        print("  blank/no data")
        return None
    return img

def add_label(img, label_ru, label_en):
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, HEIGHT - 44), (WIDTH, HEIGHT)], fill=(0, 0, 0))
    try:
        font  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except:
        font = small = ImageFont.load_default()
    draw.text((14, HEIGHT - 38), label_ru, fill=(255, 255, 255), font=font)
    draw.text((14, HEIGHT - 18), label_en, fill=(160, 160, 160), font=small)
    draw.text((WIDTH - 220, HEIGHT - 26), "Kharg Island · Sentinel-2", fill=(120, 120, 120), font=small)
    return img

def img_to_b64(img):
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()

print("Fetching Kharg Island before/after March 13 strikes…")
token = get_token()

frames = []
for d_from, d_to, label_ru, label_en in WINDOWS:
    print(f"  {d_from} → {d_to} ({label_ru})…", end=" ", flush=True)
    img = fetch_image(token, d_from, d_to)
    if img is None:
        print("skip")
        continue
    img = add_label(img, label_ru, label_en)
    frames.append((label_ru, label_en, img_to_b64(img)))
    print("ok")

if len(frames) < 2:
    print("Need at least 2 frames. Exiting.")
    sys.exit(1)

print(f"\n{len(frames)} frames collected. Building HTML…")

imgs_js = "[\n" + ",\n".join(
    f'  {{ ru: "{ru}", en: "{en}", src: "data:image/png;base64,{b}" }}'
    for ru, en, b in frames
) + "\n]"

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Kharg Island · Before &amp; After · March 13 Strikes</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0a0a0a; color: #eee; font-family: system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 28px 16px 64px; }}
  h1 {{ font-size: 17px; font-weight: 600; letter-spacing: .04em; color: #ccc; margin-bottom: 4px; }}
  .sub {{ font-size: 12px; color: #555; margin-bottom: 6px; }}
  .strike-note {{ font-size: 12px; color: #c87a30; background: #1a1000;
                  border: 1px solid #3a2800; border-radius: 4px;
                  padding: 6px 14px; margin-bottom: 22px; }}
  .compare-wrap {{ position: relative; width: 1000px; max-width: 100%;
                   border-radius: 8px; overflow: hidden; cursor: ew-resize;
                   user-select: none; }}
  .compare-wrap img {{ width: 100%; display: block; }}
  .img-after {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                clip-path: inset(0 50% 0 0); }}
  .divider {{ position: absolute; top: 0; left: 50%; width: 2px; height: 100%;
              background: #fff; transform: translateX(-50%); pointer-events: none; }}
  .handle {{ position: absolute; top: 50%; left: 50%;
             transform: translate(-50%, -50%);
             width: 36px; height: 36px; border-radius: 50%;
             background: #fff; border: 2px solid #ccc;
             display: flex; align-items: center; justify-content: center;
             pointer-events: none; font-size: 14px; color: #333; font-weight: 700; }}
  .label-before {{ position: absolute; top: 12px; left: 14px;
                   background: rgba(0,0,0,.65); color: #e05555;
                   font-size: 11px; font-weight: 700; letter-spacing: .08em;
                   text-transform: uppercase; padding: 3px 8px; border-radius: 3px; }}
  .label-after  {{ position: absolute; top: 12px; right: 14px;
                   background: rgba(0,0,0,.65); color: #52c07a;
                   font-size: 11px; font-weight: 700; letter-spacing: .08em;
                   text-transform: uppercase; padding: 3px 8px; border-radius: 3px; }}
  .strip {{ display: flex; gap: 10px; margin-top: 16px; width: 1000px; max-width: 100%; }}
  .thumb {{ flex: 1; cursor: pointer; border-radius: 5px; overflow: hidden;
            border: 2px solid transparent; transition: border-color .15s; }}
  .thumb.active {{ border-color: #d4a843; }}
  .thumb img {{ width: 100%; display: block; }}
  .thumb-label {{ font-size: 11px; color: #666; text-align: center; padding: 5px 0 2px; }}
  .note {{ font-size: 11px; color: #444; margin-top: 14px; text-align: center; }}
</style>
</head>
<body>
<h1>Kharg Island · Military Area — Before &amp; After</h1>
<div class="sub">Sentinel-2 L2A · True Color · Kharg Island (airfield, naval base, AA positions)</div>
<div class="strike-note">US strikes on military targets: March 13, 2026 · Second wave: April 7, 2026</div>
<div class="compare-wrap" id="cmp">
  <img id="img-before" src="" alt="Before">
  <img id="img-after"  src="" alt="After" class="img-after">
  <div class="divider" id="div-line"></div>
  <div class="handle" id="handle">⇔</div>
  <div class="label-before" id="lbl-before">Before</div>
  <div class="label-after"  id="lbl-after">After</div>
</div>
<div class="strip" id="strip"></div>
<div class="note">Drag left/right to compare · Satellite imagery: ESA Copernicus Sentinel-2</div>
<script>
const frames = {imgs_js};
const strip = document.getElementById('strip');
frames.forEach((f, i) => {{
  const div = document.createElement('div');
  div.className = 'thumb' + (i <= 1 ? ' active' : '');
  div.innerHTML = `<img src="${{f.src}}"><div class="thumb-label">${{f.ru}}</div>`;
  div.onclick = () => selectFrame(i);
  strip.appendChild(div);
}});
let beforeIdx = 0, afterIdx = Math.min(1, frames.length - 1);
function selectFrame(i) {{
  if (i === 0) {{ beforeIdx = 0; afterIdx = Math.min(1, frames.length - 1); }}
  else {{ beforeIdx = i - 1; afterIdx = i; }}
  updateImages();
  [...strip.querySelectorAll('.thumb')].forEach((t, ti) => {{
    t.classList.toggle('active', ti === beforeIdx || ti === afterIdx);
  }});
}}
function updateImages() {{
  document.getElementById('img-before').src = frames[beforeIdx].src;
  document.getElementById('img-after').src  = frames[afterIdx].src;
  document.getElementById('lbl-before').textContent = frames[beforeIdx].ru;
  document.getElementById('lbl-after').textContent  = frames[afterIdx].ru;
}}
updateImages();
const cmp = document.getElementById('cmp');
const divLine = document.getElementById('div-line');
const handle = document.getElementById('handle');
const imgAfter = document.getElementById('img-after');
let dragging = false;
function setPos(x) {{
  const rect = cmp.getBoundingClientRect();
  let pct = Math.max(2, Math.min(98, (x - rect.left) / rect.width * 100));
  divLine.style.left = pct + '%';
  handle.style.left  = pct + '%';
  imgAfter.style.clipPath = `inset(0 ${{100 - pct}}% 0 0)`;
}}
cmp.addEventListener('mousedown',  e => {{ dragging = true; setPos(e.clientX); }});
window.addEventListener('mousemove', e => {{ if (dragging) setPos(e.clientX); }});
window.addEventListener('mouseup',   () => dragging = false);
cmp.addEventListener('touchstart', e => {{ dragging = true; setPos(e.touches[0].clientX); }}, {{passive: true}});
window.addEventListener('touchmove', e => {{ if (dragging) setPos(e.touches[0].clientX); }}, {{passive: true}});
window.addEventListener('touchend',  () => dragging = false);
</script>
</body>
</html>"""

os.makedirs("public", exist_ok=True)
out_path = "public/kharg_before_after.html"
with open(out_path, "w") as f:
    f.write(html)
size_mb = os.path.getsize(out_path) / 1024 / 1024
print(f"Saved → {out_path}  ({size_mb:.1f} MB)")
