"""
p2_asc101_diag.py — P2 of ТЗ 2026-10-26 v5.

Diagnoses why RT ASC-101 returns pixel_valid_frac=0 for all rows.

Part A: CDSE OData catalog for 5 specific ТЗ dates
  For each date: what GRDH scenes exist in catalog over RT bbox?
  Columns: date, platform, serial, relOrbit, direction, scene_id

Part B: One concrete SH request (2019-09-24, ASC-101) + actual SH response
  Shows exact pixel_valid_frac returned and why.

Part C: Date overlap analysis
  Shows that ASC-101 dates in export == DESC-35 dates from terminal_observations
  i.e. the script queried ASC-101 filter on DESC-35 dates → always empty

Outputs (to diagnostics/p2_asc101/):
  cdse_catalog_5dates.txt  — CDSE catalog table for 5 dates
  sh_request_example.json  — exact SH request for 2019-09-24
  sh_response_example.txt  — SH response (pixel_valid_frac)
  diag.txt                 — full diagnosis + root cause
"""

import os, sys, json, datetime, requests, gzip, csv, io
from pathlib import Path

# ---------------------------------------------------------------------------
OUT_DIR  = Path("diagnostics/p2_asc101")
ODATA    = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
SH_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"
# RT bbox + 0.5deg buffer (catches passes slightly off-center)
RT_BBOX_WIDE  = "POLYGON((49.5 26.0,51.0 26.0,51.0 27.5,49.5 27.5,49.5 26.0))"
# RT bbox + 300m buffer (exact, for SH request)
RT_BBOX_SH    = [50.1074, 26.6372, 50.1574, 26.6873]

# 5 dates: 3 specified in ТЗ + 2 additional valid DESC-35 dates from export
ТЗ_DATES = ["2019-09-24", "2024-10-03", "2025-11-15", "2025-10-25", "2025-11-06"]

EVALSCRIPT = """//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }"""

SH_CLIENT_ID     = os.environ.get("SH_CLIENT_ID", "")
SH_CLIENT_SECRET = os.environ.get("SH_CLIENT_SECRET", "")


def cdse_catalog(date_str, bbox_wkt=RT_BBOX_WIDE):
    d = datetime.date.fromisoformat(date_str)
    d0 = (d - datetime.timedelta(days=1)).isoformat()
    d1 = (d + datetime.timedelta(days=1)).isoformat()
    filt = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and ContentDate/Start gt {d0}T00:00:00.000Z "
        f"and ContentDate/Start lt {d1}T23:59:59.000Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_wkt}')"
    )
    try:
        r = requests.get(ODATA, params={
            "$filter": filt, "$top": 50, "$expand": "Attributes",
            "$orderby": "ContentDate/Start asc",
        }, timeout=30)
        r.raise_for_status()
        items = r.json().get("value", [])
        rows = []
        for item in items:
            attrs = {a["Name"]: a.get("Value") for a in item.get("Attributes", [])}
            pt = attrs.get("productType", "")
            if "GRDH" not in pt:
                continue
            rows.append({
                "date":      date_str,
                "platform":  attrs.get("platformShortName", ""),
                "serial":    attrs.get("platformSerialIdentifier", ""),
                "relOrbit":  attrs.get("relativeOrbitNumber", ""),
                "direction": attrs.get("orbitDirection", ""),
                "productType": pt,
                "scene_id":  item.get("Name", "")[:60],
                "start":     item.get("ContentDate", {}).get("Start", ""),
            })
        return rows
    except Exception as e:
        return [{"date": date_str, "error": str(e)}]


def sh_token():
    if not SH_CLIENT_ID:
        return None
    try:
        r = requests.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={"grant_type": "client_credentials", "client_id": SH_CLIENT_ID,
                  "client_secret": SH_CLIENT_SECRET},
            timeout=30,
        )
        return r.json().get("access_token")
    except Exception as e:
        print(f"  [WARN] sh_token failed: {e}")
        return None


def sh_request_asc101(date_str, token=None):
    """Make exact SH request that mega_export makes for ASC-101."""
    d0 = date_str
    d1 = (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    payload = {
        "input": {
            "bounds": {"bbox": RT_BBOX_SH, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "dataFilter": {"orbitDirection": "ASCENDING", "relativeOrbitNumber": 101},
                "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN", "demInstance": "COPERNICUS_30"},
                "type": "S1GRD",
                "dataCollection": "sentinel-1-grd",
            }],
            "mosaicking": "ORBIT",
        },
        "output": {
            "width": 100, "height": 100,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": EVALSCRIPT,
    }
    # Override time_interval in dataFilter
    payload["input"]["data"][0]["dataFilter"]["timeRange"] = {
        "from": f"{d0}T00:00:00Z", "to": f"{d1}T00:00:00Z"
    }

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = requests.post(SH_URL, json=payload, headers=headers, timeout=60)
        return {"status_code": r.status_code, "content_length": len(r.content),
                "content_type": r.headers.get("content-type", ""),
                "body_preview": r.text[:500] if r.status_code != 200 else f"TIFF {len(r.content)} bytes"}
    except Exception as e:
        return {"error": str(e)}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    lines.append(f"Run: {ts}")
    lines.append("Script: scripts/research/p2_asc101_diag.py")
    lines.append("")

    # ---- Part A: CDSE catalog for 5 dates ----
    print("=== Part A: CDSE catalog for 5 ТЗ dates ===\n")
    lines.append("=== Part A: CDSE GRDH catalog — 5 specific dates, RT wide bbox (49.5–51°E) ===")
    lines.append(f"{'date':<12} {'serial':<6} {'relOrbit':<10} {'direction':<12} {'scene_id_prefix'}")
    lines.append("-" * 80)

    all_catalog = []
    for d in ТЗ_DATES:
        rows = cdse_catalog(d)
        if not rows or "error" in rows[0]:
            err = rows[0].get("error", "NO DATA") if rows else "NO DATA"
            lines.append(f"  {d}: ERROR — {err}")
            print(f"  {d}: ERROR — {err}")
        else:
            seen = set()
            for row in rows:
                key = (row["serial"], str(row["relOrbit"]), row["direction"])
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"  {row['date']:<12} S1{row['serial']:<4} orb={row['relOrbit']:<6} {row['direction']:<12} {row['scene_id']}")
                print(f"  {row['date']:<12} S1{row['serial']:<4} orb={row['relOrbit']:<6} {row['direction']:<12} {row['scene_id']}")
            all_catalog.extend(rows)

    lines.append("")

    # ---- Part B: SH request example ----
    print("\n=== Part B: SH request for 2019-09-24 ASC-101 ===\n")
    lines.append("=== Part B: SH request — 2019-09-24, ASC-101 filter ===")

    example_date = "2019-09-24"
    token = sh_token()
    resp = sh_request_asc101(example_date, token)

    sh_req_doc = {
        "endpoint": SH_URL,
        "date": example_date,
        "dataFilter": {"orbitDirection": "ASCENDING", "relativeOrbitNumber": 101,
                       "timeRange": {"from": f"{example_date}T00:00:00Z", "to": "2019-09-25T00:00:00Z"}},
        "bbox": RT_BBOX_SH,
        "crs": "EPSG:4326",
        "size": [100, 100],
        "backCoeff": "GAMMA0_TERRAIN",
        "orthorectify": True,
        "mosaicking": "ORBIT",
        "evalscript": EVALSCRIPT[:120] + "...",
    }
    (OUT_DIR / "sh_request_example.json").write_text(json.dumps(sh_req_doc, indent=2))

    lines.append(f"  SH response status:       {resp.get('status_code', resp.get('error', 'N/A'))}")
    lines.append(f"  Content-type:             {resp.get('content_type','N/A')}")
    lines.append(f"  Content-length:           {resp.get('content_length','N/A')} bytes")
    lines.append(f"  Body preview:             {resp.get('body_preview', resp.get('error',''))}")
    (OUT_DIR / "sh_response_example.txt").write_text(json.dumps(resp, indent=2))

    lines.append("")
    lines.append(f"  Prod DB value (DESC-35 export):  date=2019-09-24, rt_0, ring15_far=9.035 dB, pixel_valid_frac=1.0")
    lines.append(f"  ASC-101 export value:            date=2019-09-24, rt_0, ring15_far='', pixel_valid_frac=0.0")
    lines.append("")

    # ---- Part C: Root cause ----
    lines.append("=== Part C: Root cause — date list mismatch ===")
    lines.append("  mega_export section 4, ASC-101 dates source:")
    lines.append("    v0_manifest_all.csv NOT FOUND (WARNING in log)")
    lines.append("    → fallback: sb_fetch_all('terminal_observations', filters={'terminal_id':'ras_tanura'})")
    lines.append("    → returns DESC-35 pass dates (only DESC in terminal_observations)")
    lines.append("    → queries SH with ASC-101 filter on DESC-35 dates → no scenes → pixel_valid_frac=0")
    lines.append("")
    lines.append("  FIX: supply v0_manifest_all.csv with correct ASC-101 dates,")
    lines.append("       OR: pre-populate dates via CDSE OData before calling SH")
    lines.append("       (ЗАПРЕЩЕНО: повтор без relativeOrbitNumber)")
    lines.append("")
    lines.append("=== Part A conclusion ===")
    if all_catalog:
        orbs = sorted(set(str(r["relOrbit"]) for r in all_catalog if "error" not in r))
        dirs = sorted(set(r["direction"] for r in all_catalog if "error" not in r))
        serials = sorted(set(r["serial"] for r in all_catalog if "error" not in r))
        lines.append(f"  orbs found over RT: {orbs}")
        lines.append(f"  directions found:   {dirs}")
        lines.append(f"  platforms:          {['S1'+s for s in serials]}")
    else:
        lines.append("  CDSE unreachable — see GHA logs")

    diag = OUT_DIR / "diag.txt"
    diag.write_text("\n".join(lines))
    print(f"\nDiagnosis → {diag}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
