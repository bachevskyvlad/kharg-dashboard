"""
cdse_stac_diag.py — P3 of ТЗ 2026-10-26 v3.

Queries CDSE OData catalog (no auth required for public S1 products)
for RT DESC-35 dates to diagnose why post-2025-11 scenes return empty
from SentinelHub Processing API.

CDSE STAC /search is blocked by GHA proxy (400). Using OData instead:
  https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=...

Uses wide bbox (48.5–52°E, 25.5–28.5°N) to ensure DESC-35 swath is captured.
Also runs a dedicated DESC-35 orbit search to confirm presence/absence.

Outputs (to diagnostics/cdse_stac/):
  stac_before.csv  — scenes for 5 valid dates before 2025-11
  stac_after.csv   — scenes for 5 empty dates after 2025-11
  stac_diag.txt    — full diff table + conclusion

Columns per row:
  date, scene_id, platform, platformSerialIdentifier,
  relativeOrbitNumber, orbitDirection, mode, productType,
  polarisation, processingLevel, startDateTime, stopDateTime
"""

import os, sys, json, datetime, requests
import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# CDSE OData endpoint (public, no auth for S1 GRD search)
ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
# Wide bbox to catch DESC-35 swath (~250km wide, passes ~1deg west of RT)
BBOX_WKT  = "POLYGON((48.5 25.5,52.0 25.5,52.0 28.5,48.5 28.5,48.5 25.5))"
MAX_ITEMS = 50

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
EMPTY_DATES_AFTER = [
    "2025-11-24",
    "2025-12-06",
    "2025-12-18",
    "2026-01-11",
    "2026-02-03",
]


def odata_query(date_str, bbox_wkt=BBOX_WKT, max_items=MAX_ITEMS, extra_filter=""):
    """
    Query CDSE OData for Sentinel-1 scenes over bbox on given date ±1d.
    Returns list of row dicts with standardised fields.
    """
    d = datetime.date.fromisoformat(date_str)
    d0 = (d - datetime.timedelta(days=1)).isoformat()
    d1 = (d + datetime.timedelta(days=1)).isoformat()

    filt = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and ContentDate/Start gt {d0}T00:00:00.000Z "
        f"and ContentDate/Start lt {d1}T23:59:59.000Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_wkt}')"
    )
    if extra_filter:
        filt += f" and {extra_filter}"

    params = {
        "$filter":  filt,
        "$top":     max_items,
        "$expand":  "Attributes",
        "$orderby": "ContentDate/Start asc",
    }
    try:
        r = requests.get(ODATA_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items = data.get("value", [])
        rows = []
        for item in items:
            attrs = {a["Name"]: a.get("Value") for a in item.get("Attributes", [])}
            row = {
                "date":                     date_str,
                "scene_id":                 item.get("Name", ""),
                "platform":                 attrs.get("platformShortName", ""),
                "platformSerialIdentifier": attrs.get("platformSerialIdentifier", ""),
                "relativeOrbitNumber":      attrs.get("relativeOrbitNumber", ""),
                "orbitDirection":           attrs.get("orbitDirection", ""),
                "mode":                     attrs.get("operationalMode", ""),
                "productType":              attrs.get("productType", ""),
                "polarisation":             attrs.get("polarisationChannels", ""),
                "processingLevel":          attrs.get("processingLevel", ""),
                "startDateTime":            item.get("ContentDate", {}).get("Start", ""),
                "stopDateTime":             item.get("ContentDate", {}).get("End", ""),
            }
            rows.append(row)
        return rows
    except Exception as e:
        print(f"  [WARN] OData query failed for {date_str}: {e}")
        return []


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    lines = [f"Run: {run_ts}", "Script: scripts/research/cdse_stac_diag.py",
             f"API: {ODATA_URL} (OData — STAC blocked by GHA proxy)",
             f"BBOX: {BBOX_WKT}", ""]

    print("=== CDSE OData Diagnosis for RT DESC-35 ===\n")
    print(f"Wide BBOX: {BBOX_WKT}\n")

    # ---- query before dates ----
    print("--- Before 2025-11 (should have S1A DESC-35) ---")
    before_rows = []
    for d in VALID_DATES_BEFORE:
        rows = odata_query(d)
        desc35 = [r for r in rows if str(r.get("relativeOrbitNumber","")) == "35"
                  and "desc" in str(r.get("orbitDirection","")).lower()]
        all_orbs = sorted(set(f"{r['platformSerialIdentifier']}_orb{r['relativeOrbitNumber']}_{r['orbitDirection'][:3]}"
                              for r in rows))
        print(f"  {d}: {len(rows)} total, DESC-35={len(desc35)} — all_orbs: {all_orbs}")
        before_rows.extend(rows)

    before_df = pd.DataFrame(before_rows) if before_rows else pd.DataFrame()
    before_df.to_csv(OUT_DIR / "stac_before.csv", index=False)

    # ---- query after dates ----
    print("\n--- After 2025-11 (empty in SH, checking for S1C) ---")
    after_rows = []
    for d in EMPTY_DATES_AFTER:
        rows = odata_query(d)
        desc35 = [r for r in rows if str(r.get("relativeOrbitNumber","")) == "35"
                  and "desc" in str(r.get("orbitDirection","")).lower()]
        all_orbs = sorted(set(f"{r['platformSerialIdentifier']}_orb{r['relativeOrbitNumber']}_{r['orbitDirection'][:3]}"
                              for r in rows))
        print(f"  {d}: {len(rows)} total, DESC-35={len(desc35)} — all_orbs: {all_orbs}")
        after_rows.extend(rows)

    after_df = pd.DataFrame(after_rows) if after_rows else pd.DataFrame()
    after_df.to_csv(OUT_DIR / "stac_after.csv", index=False)

    # ---- compare ----
    def fmt_section(df, label):
        lines_out = [f"=== {label} ==="]
        if not df.empty:
            desc35 = df[
                (df["relativeOrbitNumber"].astype(str) == "35") &
                (df["orbitDirection"].str.lower().str.contains("desc", na=False))
            ]
            if desc35.empty:
                lines_out.append("  no DESC-35 scenes found")
                # show what orbits ARE present
                orb_summary = df.groupby(["platformSerialIdentifier","relativeOrbitNumber","orbitDirection"]).size()
                for (s, orb, d2), cnt in orb_summary.items():
                    lines_out.append(f"  found: S1{s} orb={orb} {d2} x{cnt}")
            else:
                for _, r in desc35.drop_duplicates(
                    subset=["date","platformSerialIdentifier","productType"]
                ).iterrows():
                    lines_out.append(
                        f"  {r['date']} | S1{r['platformSerialIdentifier']} | orb={r['relativeOrbitNumber']} | "
                        f"dir={r['orbitDirection']} | type={r['productType']} | pol={r['polarisation']}"
                    )
        else:
            lines_out.append("  no data returned")
        return lines_out

    lines += fmt_section(before_df, "Before 2025-11 DESC-35 scenes")
    lines.append("")
    lines += fmt_section(after_df, "After 2025-11 DESC-35 scenes")
    lines.append("")

    # ---- platform diff ----
    lines.append("=== Platform diff Before vs After (all DESC) ===")
    if not before_df.empty and not after_df.empty:
        bd = before_df[before_df["orbitDirection"].str.lower().str.contains("desc", na=False)]
        ad = after_df[after_df["orbitDirection"].str.lower().str.contains("desc", na=False)]
        serial_before = set(bd["platformSerialIdentifier"].dropna().unique())
        serial_after  = set(ad["platformSerialIdentifier"].dropna().unique())
        orb_before    = set(bd["relativeOrbitNumber"].dropna().astype(str).unique())
        orb_after     = set(ad["relativeOrbitNumber"].dropna().astype(str).unique())
        lines.append(f"  serial before: {serial_before}  after: {serial_after}")
        lines.append(f"  orbs   before: {orb_before}     after: {orb_after}")
        new_serial = serial_after - serial_before
        if new_serial:
            lines.append(f"  NEW serial in after: {new_serial}")
    else:
        lines.append("  OData also unreachable — try with CDSE auth token")
    lines.append("")

    diag_path = OUT_DIR / "stac_diag.txt"
    diag_path.write_text("\n".join(lines))
    print(f"\nDiagnosis written → {diag_path}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
