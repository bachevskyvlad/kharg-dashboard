"""
cdse_stac_diag.py — P3 of ТЗ 2026-10-26 v3.

Queries CDSE STAC catalog for RT DESC-35 dates to diagnose why
post-2025-11 scenes return empty from SentinelHub Processing API.

Outputs (to diagnostics/cdse_stac/):
  stac_before.csv  — 5 valid dates before 2025-11 (known good)
  stac_after.csv   — 5 empty dates after 2025-11 (known missing)
  stac_diag.txt    — full diff table + conclusion

Columns per row:
  date, scene_id, platform, platformSerialIdentifier,
  relativeOrbitNumber, orbitDirection, mode, productType,
  polarisation, processingLevel, constellation, swath,
  startDateTime, stopDateTime
"""

import os, sys, json, datetime, requests
import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STAC_URL    = "https://catalogue.dataspace.copernicus.eu/stac/search"
BBOX        = "50.0,26.5,50.8,27.0"   # RT bounding box
MAX_ITEMS   = 20                        # per query

OUT_DIR = Path("diagnostics/cdse_stac")

# 5 dates where SH returned data (DESC-35, before S1C era)
VALID_DATES_BEFORE = [
    "2025-10-01",
    "2025-10-13",
    "2025-10-25",
    "2025-11-06",
    "2025-11-12",
]

# 5 dates where SH returned [] (DESC-35, after S1C era)
# From tvt_all.csv / mega_export logs where pixel_valid_frac=0
EMPTY_DATES_AFTER = [
    "2025-11-24",
    "2025-12-06",
    "2025-12-18",
    "2026-01-11",
    "2026-02-03",
]


def stac_query(date_str, bbox=BBOX, max_items=MAX_ITEMS):
    """
    Query CDSE STAC for Sentinel-1 scenes over bbox on given date.
    Returns list of item property dicts.
    """
    # date window: same day + 1 day
    d = datetime.date.fromisoformat(date_str)
    d1 = (d + datetime.timedelta(days=1)).isoformat()
    params = {
        "collections": "SENTINEL-1",
        "bbox":        bbox,
        "datetime":    f"{date_str}T00:00:00Z/{d1}T00:00:00Z",
        "limit":       max_items,
    }
    try:
        r = requests.get(STAC_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items = data.get("features", [])
        rows = []
        for item in items:
            props = item.get("properties", {})
            row = {
                "date":                     date_str,
                "scene_id":                 item.get("id", ""),
                "platform":                 props.get("platform", ""),
                "platformSerialIdentifier": props.get("platformSerialIdentifier", ""),
                "relativeOrbitNumber":      props.get("relativeOrbitNumber", ""),
                "orbitDirection":           props.get("orbitDirection", props.get("sat:orbit_state", "")),
                "mode":                     props.get("operationalMode", props.get("sar:instrument_mode", "")),
                "productType":              props.get("productType", ""),
                "polarisation":             props.get("polarisation", props.get("sar:polarizations", "")),
                "processingLevel":          props.get("processingLevel", ""),
                "constellation":            props.get("constellation", ""),
                "swath":                    props.get("swath", ""),
                "startDateTime":            props.get("startDatetime", props.get("datetime", "")),
                "stopDateTime":             props.get("stopDatetime", ""),
                "s3Path":                   props.get("s3Path", ""),
            }
            rows.append(row)
        return rows
    except Exception as e:
        print(f"  [WARN] STAC query failed for {date_str}: {e}")
        return []


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    lines = [f"Run: {run_ts}", "Script: scripts/research/cdse_stac_diag.py",
             f"STAC: {STAC_URL}", f"BBOX: {BBOX}", ""]

    print("=== CDSE STAC Diagnosis for RT DESC-35 ===\n")

    # ---- query before dates ----
    print("--- Before 2025-11 (should be S1A DESC-35) ---")
    before_rows = []
    for d in VALID_DATES_BEFORE:
        rows = stac_query(d)
        # filter to DESC direction
        desc_rows = [r for r in rows if "desc" in str(r.get("orbitDirection","")).lower()]
        print(f"  {d}: {len(rows)} total, {len(desc_rows)} DESC — {[r['platform']+'_orb'+str(r['relativeOrbitNumber']) for r in desc_rows]}")
        before_rows.extend(rows)

    before_df = pd.DataFrame(before_rows) if before_rows else pd.DataFrame()
    before_df.to_csv(OUT_DIR / "stac_before.csv", index=False)

    # ---- query after dates ----
    print("\n--- After 2025-11 (empty in SH, may be S1C) ---")
    after_rows = []
    for d in EMPTY_DATES_AFTER:
        rows = stac_query(d)
        desc_rows = [r for r in rows if "desc" in str(r.get("orbitDirection","")).lower()]
        print(f"  {d}: {len(rows)} total, {len(desc_rows)} DESC — {[r['platform']+'_orb'+str(r['relativeOrbitNumber']) for r in desc_rows]}")
        after_rows.extend(rows)

    after_df = pd.DataFrame(after_rows) if after_rows else pd.DataFrame()
    after_df.to_csv(OUT_DIR / "stac_after.csv", index=False)

    # ---- compare ----
    lines.append("=== Before 2025-11 (DESC only) ===")
    if not before_df.empty:
        bd = before_df[before_df["orbitDirection"].str.lower().str.contains("desc", na=False)]
        for _, r in bd.iterrows():
            lines.append(
                f"  {r['date']} | {r['platform']} | {r.get('platformSerialIdentifier','')} | "
                f"orb={r['relativeOrbitNumber']} | dir={r['orbitDirection']} | mode={r['mode']} | "
                f"type={r['productType']} | pol={r['polarisation']}"
            )
    else:
        lines.append("  no data returned")
    lines.append("")

    lines.append("=== After 2025-11 (DESC only) ===")
    if not after_df.empty:
        ad = after_df[after_df["orbitDirection"].str.lower().str.contains("desc", na=False)]
        for _, r in ad.iterrows():
            lines.append(
                f"  {r['date']} | {r['platform']} | {r.get('platformSerialIdentifier','')} | "
                f"orb={r['relativeOrbitNumber']} | dir={r['orbitDirection']} | mode={r['mode']} | "
                f"type={r['productType']} | pol={r['polarisation']}"
            )
    else:
        lines.append("  no data returned")
    lines.append("")

    # ---- platform diff ----
    lines.append("=== Platform diff: Before vs After (DESC) ===")
    if not before_df.empty and not after_df.empty:
        bd_desc = before_df[before_df["orbitDirection"].str.lower().str.contains("desc", na=False)]
        ad_desc = after_df[after_df["orbitDirection"].str.lower().str.contains("desc", na=False)]
        platforms_before = set(bd_desc["platform"].dropna().unique())
        platforms_after  = set(ad_desc["platform"].dropna().unique())
        serial_before = set(bd_desc["platformSerialIdentifier"].dropna().unique())
        serial_after  = set(ad_desc["platformSerialIdentifier"].dropna().unique())
        lines.append(f"  platform before: {platforms_before}  after: {platforms_after}")
        lines.append(f"  serial before:   {serial_before}  after: {serial_after}")
        new_platforms = platforms_after - platforms_before
        if new_platforms:
            lines.append(f"  NEW in after: {new_platforms} → use platformSerialIdentifier filter")
    lines.append("")

    diag_path = OUT_DIR / "stac_diag.txt"
    diag_path.write_text("\n".join(lines))
    print(f"\nDiagnosis written → {diag_path}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
