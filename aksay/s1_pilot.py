"""
aksay/s1_pilot.py — rev4 B-pilot: ONE Sentinel-1 12-day pair (DES 107) → coherence on the S2 grid.
Runs in GitHub Actions inside a conda env with ISCE2 + GDAL.  Usage: python s1_pilot.py 2026-08-06 2026-08-18

Stages (each logged to aksay/data/s1_pilot/<tag>/log.json, so a failure is reported, not hidden):
  auth     CDSE token (Sentinel Hub OAuth client from Secrets) + test against the OData download service
  find     OData product per date (slice whose footprint best contains the tongue), file listing via Nodes
  pick     sub-swath containing the tongue from the annotation geolocation grids
  fetch    manifest + annotation (+calibration/noise) for all swaths, measurement TIFF for the chosen swath only
  orbit    precise orbits (POEORB) from the public ESA STEP mirror
  dem      Copernicus GLO-30 (AWS open data) + EGM2008 → WGS84 ellipsoid, written as an ISCE DEM
  isce     topsApp: coreg → interferogram → coherence, range×azimuth looks 5×1, no ESD, no unwrapping, geocode
  regrid   geocoded coherence → frozen S2 fetch grid (10 m) → coh_s2grid.npz
"""
import os, sys, json, glob, math, re, shutil, subprocess, time, zipfile, io, traceback
import numpy as np, requests

HERE = os.path.dirname(os.path.abspath(__file__))
D1, D2 = sys.argv[1], sys.argv[2]
TAG = f"{D1}_{D2}"
OUT = os.path.join(HERE, "data", "s1_pilot", TAG); os.makedirs(OUT, exist_ok=True)
WORK = os.path.abspath(os.environ.get("S1_WORK", "/tmp/s1work")); os.makedirs(WORK, exist_ok=True)
RELORB = 107
G = json.load(open(os.path.join(HERE, "aksay_geometry.json")))
CROP = G["crop"]                                       # lon0, lat0, lon1, lat1
FETCH_BBOX = [74.490, 42.480, 74.580, 42.560]; W10, H10 = 740, 888
LON, LAT = 74.535, 42.525
PAD = 0.03
ROI = [CROP[1] - PAD, CROP[3] + PAD, CROP[0] - PAD, CROP[2] + PAD]     # S N W E
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CAT = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DL = "https://download.dataspace.copernicus.eu/odata/v1/Products"
LOG = {"pair": [D1, D2], "relorb": RELORB, "stages": {}}
S = requests.Session()

def log(stage, **kw):
    LOG["stages"].setdefault(stage, {}).update(kw)
    json.dump(LOG, open(os.path.join(OUT, "log.json"), "w"), indent=1, default=str)
    print(f"[{stage}]", json.dumps(kw, default=str)[:600], flush=True)

def stage(name):
    def deco(fn):
        def run(*a, **k):
            t0 = time.time()
            try:
                r = fn(*a, **k); log(name, ok=True, seconds=round(time.time() - t0)); return r
            except Exception as e:
                log(name, ok=False, error=repr(e)[:2000], trace=traceback.format_exc()[-3000:]); raise SystemExit(f"stage {name} failed")
        return run
    return deco

# ── auth ────────────────────────────────────────────────────────────────────
@stage("auth")
def auth():
    r = S.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": os.environ["CLIENT_ID"],
                                "client_secret": os.environ["CLIENT_SECRET"]}, timeout=60)
    r.raise_for_status(); S.headers["Authorization"] = "Bearer " + r.json()["access_token"]

# ── find products ───────────────────────────────────────────────────────────
def footprint_score(fp):
    from shapely.geometry import shape, Point, box
    g = shape(fp); roi = box(ROI[2], ROI[0], ROI[3], ROI[1])
    return g.intersection(roi).area / roi.area, g.exterior.distance(Point(LON, LAT)) if hasattr(g, "exterior") else 0

@stage("find")
def find(date):
    flt = ("Collection/Name eq 'SENTINEL-1' and "
           "Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/OData.CSC.StringAttribute/Value eq 'IW_SLC__1S') and "
           f"Attributes/OData.CSC.IntegerAttribute/any(a:a/Name eq 'relativeOrbitNumber' and a/OData.CSC.IntegerAttribute/Value eq {RELORB}) and "
           f"OData.CSC.Intersects(area=geography'SRID=4326;POINT({LON} {LAT})') and "
           f"ContentDate/Start gt {date}T00:00:00.000Z and ContentDate/Start lt {date}T23:59:59.000Z")
    r = requests.get(CAT, params={"$filter": flt, "$top": 20}, timeout=120); r.raise_for_status()
    prods = r.json()["value"]
    best = max(prods, key=lambda p: footprint_score(p["GeoFootprint"]))
    info = {"id": best["Id"], "name": best["Name"], "candidates": [p["Name"] for p in prods],
            "roi_cover": round(footprint_score(best["GeoFootprint"])[0], 3)}
    log("find", **{date: info}); return info

def nodes(pid, *path):
    url = f"{DL}({pid})" + "".join(f"/Nodes({p})" for p in path) + "/Nodes"
    r = S.get(url, timeout=120); r.raise_for_status()
    j = r.json(); return [n["Name"] for n in (j["result"] if "result" in j else j.get("value", []))]

def get_file(pid, path, dest):
    url = f"{DL}({pid})" + "".join(f"/Nodes({p})" for p in path) + "/$value"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(4):
        with S.get(url, stream=True, timeout=600, allow_redirects=True) as r:
            if r.status_code == 401: auth(); continue
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(8 << 20): fh.write(chunk)
        return os.path.getsize(dest)
    raise RuntimeError(f"download failed {url}")

@stage("download_test")
def download_test(p):
    top = nodes(p["id"], p["name"])
    log("download_test", safe_root=top)
    return top

def pick_swath(ann_files):
    """Swath whose geolocation grid contains the tongue point."""
    from lxml import etree
    best = None
    for f in ann_files:
        t = etree.parse(f); pts = t.findall(".//geolocationGridPoint")
        lat = np.array([float(p.find("latitude").text) for p in pts]); lon = np.array([float(p.find("longitude").text) for p in pts])
        sw = int(re.search(r"-iw(\d)-", os.path.basename(f)).group(1))
        inside = lat.min() <= LAT <= lat.max() and lon.min() <= LON <= lon.max()
        margin = min(LON - lon.min(), lon.max() - LON) if inside else -1
        if inside and (best is None or margin > best[1]): best = (sw, margin)
    return best[0] if best else None

@stage("fetch")
def fetch(p, swath=None):
    safe = os.path.join(WORK, p["name"]); os.makedirs(safe, exist_ok=True)
    get_file(p["id"], [p["name"], "manifest.safe"], os.path.join(safe, "manifest.safe"))
    ann = [a for a in nodes(p["id"], p["name"], "annotation") if a.endswith(".xml") and "-vv-" in a]
    for a in ann: get_file(p["id"], [p["name"], "annotation", a], os.path.join(safe, "annotation", a))
    for sub in ("calibration",):
        try:
            for a in nodes(p["id"], p["name"], "annotation", sub):
                if "-vv-" in a: get_file(p["id"], [p["name"], "annotation", sub, a], os.path.join(safe, "annotation", sub, a))
        except Exception as e: log("fetch", **{f"{sub}_warning": repr(e)})
    if swath is None:
        swath = pick_swath(sorted(glob.glob(os.path.join(safe, "annotation", "*.xml"))))
    meas = [m for m in nodes(p["id"], p["name"], "measurement") if f"-iw{swath}-slc-vv-" in m]
    size = 0
    for m in meas: size += get_file(p["id"], [p["name"], "measurement", m], os.path.join(safe, "measurement", m))
    # keep only the chosen swath's annotation so ISCE sees a consistent single-swath SAFE
    log("fetch", **{p["name"]: {"swath": swath, "measurement": meas, "mb": round(size / 1e6)}})
    return safe, swath

# ── orbits ──────────────────────────────────────────────────────────────────
@stage("orbit")
def orbits(names):
    od = os.path.join(WORK, "orbits"); os.makedirs(od, exist_ok=True); got = []
    for n in names:
        mission = n[:3]; t = n.split("_")[5]                      # sensing start yyyymmddThhmmss
        when = time.strptime(t, "%Y%m%dT%H%M%S")
        for kind in ("POEORB", "RESORB"):
            found = None
            for dm in (0, -1):                                        # validity starts the day before
                tt = time.localtime(time.mktime(when) + dm * 86400 * 15)
                url = f"https://step.esa.int/auxdata/orbits/Sentinel-1/{kind}/{mission}/{tt.tm_year}/{tt.tm_mon:02d}/"
                r = requests.get(url, timeout=60)
                if r.status_code != 200: continue
                for f in sorted(set(re.findall(r'href="([^"]+\.EOF\.zip)"', r.text))):
                    m = re.search(r"_V(\d{8}T\d{6})_(\d{8}T\d{6})", f)
                    if m and m.group(1) <= t <= m.group(2): found = url + f; break
                if found: break
            if found:
                z = zipfile.ZipFile(io.BytesIO(requests.get(found, timeout=300).content)); z.extractall(od)
                got.append({"scene": n, "orbit": os.path.basename(found), "kind": kind}); break
        else:
            got.append({"scene": n, "orbit": None})
    log("orbit", files=got)
    if any(g["orbit"] is None for g in got): raise RuntimeError("missing orbit file")
    return od

# ── DEM ─────────────────────────────────────────────────────────────────────
@stage("dem")
def dem():
    """Copernicus GLO-30 (EGM2008 heights) → + geoid undulation N (constant over this 10 km area, |ΔN| < 0.5 m)
    → WGS84 ellipsoid heights, written as an ISCE DEM."""
    from osgeo import gdal
    from pyproj import Transformer
    import isceobj
    tile = ("/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N42_00_E074_00_DEM/"
            "Copernicus_DSM_COG_10_N42_00_E074_00_DEM.tif")
    s, n, w, e = ROI[0] - 0.05, ROI[1] + 0.05, ROI[2] - 0.05, ROI[3] + 0.05
    out = os.path.join(WORK, "dem_crop.tif")
    gdal.Warp(out, tile, outputBounds=(w, s, e, n), xRes=1 / 3600, yRes=1 / 3600, resampleAlg="bilinear",
              outputType=gdal.GDT_Float32)
    ds = gdal.Open(out); a = ds.GetRasterBand(1).ReadAsArray().astype(np.float32); gt = ds.GetGeoTransform()
    os.environ["PROJ_NETWORK"] = "ON"
    tr = Transformer.from_crs("EPSG:4326+3855", "EPSG:4979", always_xy=True)
    N = float(tr.transform(LON, LAT, 0.0)[2])
    if not (-80 < N < 0) or abs(N) < 1: raise RuntimeError(f"implausible geoid undulation {N}")
    a += N
    fn = os.path.join(WORK, "dem.wgs84"); a.tofile(fn)
    img = isceobj.createDemImage(); img.setFilename(fn); img.setWidth(a.shape[1]); img.setLength(a.shape[0])
    img.setAccessMode("read"); img.dataType = "FLOAT"; img.reference = "WGS84"
    img.coord1.coordStart = gt[0]; img.coord1.coordDelta = gt[1]; img.coord2.coordStart = gt[3]; img.coord2.coordDelta = gt[5]
    img.renderHdr()
    log("dem", shape=a.shape, z_min=float(a.min()), z_max=float(a.max()), geoid_undulation_m=round(N, 2))
    return fn

# ── ISCE topsApp ────────────────────────────────────────────────────────────
@stage("isce")
def isce(ref_safe, sec_safe, swath, orbit_dir, demfn):
    wd = os.path.join(WORK, "isce"); os.makedirs(wd, exist_ok=True)
    s, n, w, e = ROI
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<topsApp>
  <component name="topsinsar">
    <property name="Sensor name">SENTINEL1</property>
    <component name="reference">
      <property name="orbit directory">{orbit_dir}</property>
      <property name="output directory">reference</property>
      <property name="safe">['{ref_safe}']</property>
      <property name="polarization">vv</property>
    </component>
    <component name="secondary">
      <property name="orbit directory">{orbit_dir}</property>
      <property name="output directory">secondary</property>
      <property name="safe">['{sec_safe}']</property>
      <property name="polarization">vv</property>
    </component>
    <property name="swaths">[{swath}]</property>
    <property name="region of interest">[{s}, {n}, {w}, {e}]</property>
    <property name="demFilename">{demfn}</property>
    <property name="range looks">5</property>
    <property name="azimuth looks">1</property>
    <property name="do ESD">False</property>
    <property name="do unwrap">False</property>
    <property name="filter strength">0.0</property>
    <property name="geocode bounding box">[{s}, {n}, {w}, {e}]</property>
    <property name="geocode list">['merged/topophase.cor', 'merged/phsig.cor', 'merged/los.rdr']</property>
  </component>
</topsApp>"""
    open(os.path.join(wd, "topsApp.xml"), "w").write(xml)
    p = subprocess.run(["topsApp.py", "topsApp.xml", "--end=geocode"], cwd=wd, capture_output=True, text=True)
    open(os.path.join(OUT, "topsApp_stdout.txt"), "w").write(p.stdout[-200000:])
    open(os.path.join(OUT, "topsApp_stderr.txt"), "w").write(p.stderr[-200000:])
    if p.returncode != 0: raise RuntimeError(f"topsApp rc={p.returncode}: {p.stderr[-1500:]}")
    return wd

# ── regrid to the S2 fetch grid ─────────────────────────────────────────────
@stage("regrid")
def regrid(wd):
    from osgeo import gdal
    out = {}
    for name, band in (("topophase.cor", 2), ("phsig.cor", 1)):
        f = os.path.join(wd, "merged", name + ".geo")
        if not os.path.exists(f): continue
        vrt = f + ".vrt" if os.path.exists(f + ".vrt") else f
        dst = os.path.join(WORK, name + ".s2.tif")
        gdal.Warp(dst, vrt, outputBounds=(FETCH_BBOX[0], FETCH_BBOX[1], FETCH_BBOX[2], FETCH_BBOX[3]), width=W10, height=H10,
                  dstSRS="EPSG:4326", resampleAlg="bilinear", srcNodata=0, dstNodata=np.nan, outputType=gdal.GDT_Float32)
        ds = gdal.Open(dst); b = min(band, ds.RasterCount)
        out[name.replace(".", "_")] = ds.GetRasterBand(b).ReadAsArray().astype(np.float16)
        out[name.replace(".", "_") + "_band"] = b
    np.savez_compressed(os.path.join(OUT, "coh_s2grid.npz"), **out)
    log("regrid", arrays=list(out))

def main():
    auth()
    p1, p2 = find(D1), find(D2)
    download_test(p1)
    s1, sw = fetch(p1); s2, _ = fetch(p2, sw)
    od = orbits([p1["name"], p2["name"]])
    demfn = dem()
    wd = isce(s1, s2, sw, od, demfn)
    regrid(wd)

if __name__ == "__main__":
    main()
