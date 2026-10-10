"""
h1_db_norm_refix.py — Recompute scene_bias excluding floor tanks (db_mean > -4.9).
Fixes contaminated median: if >=8 clean tanks per scene → scene_bias = median(clean);
else → scene_bias = NaN, db_norm = NULL for that scene.

Writes updated db_norm back to Supabase (idempotent UPDATE).

Usage:
  python h1_db_norm_refix.py          # dry-run
  python h1_db_norm_refix.py --force  # live upsert
"""

import os, sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DRY_RUN = "--force" not in sys.argv

SB_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or ""

TERMINAL_ID  = "al_flaw"
ORBIT_DIR    = "DESCENDING"
PAGE_SIZE    = 1000
FLOOR_DB     = -4.9   # db_mean threshold — tanks at or below this are excluded from scene_bias
MIN_CLEAN    = 8      # minimum clean tanks per scene for valid scene_bias

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def sb_upsert(table, rows):
    r = requests.post(
        f"{SB_URL}/rest/v1/{table}?on_conflict=terminal_id,date,tank_id",
        json=rows,
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=60)
    if not r.ok:
        print(f"  [upsert error] {r.status_code}: {r.text[:500]}")
    r.raise_for_status()


def fetch_all():
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations",
                       {"terminal_id": f"eq.{TERMINAL_ID}",
                        "orbit_direction": f"eq.{ORBIT_DIR}",
                        "select": "date,tank_id,db_mean",
                        "order": "date.asc",
                        "offset": str(offset), "limit": str(PAGE_SIZE)})
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.DataFrame(rows)


def main():
    print("=== Al-Faw H1 db_norm REFIX (floor-excluded scene_bias) ===")
    print(f"  mode: {'DRY-RUN' if DRY_RUN else 'LIVE --force'}")
    print(f"  floor_thresh: {FLOOR_DB} dB  |  min_clean_tanks: {MIN_CLEAN}")
    print()

    print("[1] Fetching observations …")
    df = fetch_all()
    df = df.drop_duplicates(subset=["date", "tank_id"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df = df[df["db_mean"].notna()].copy()
    n_total = len(df)
    n_dates = df["date"].nunique()
    print(f"  {n_total} rows | {n_dates} dates | {df['tank_id'].nunique()} tanks")
    print(f"  db_mean <= {FLOOR_DB}: {(df.db_mean <= FLOOR_DB).sum()} rows ({(df.db_mean <= FLOOR_DB).mean()*100:.1f}%)")
    print()

    print("[2] Computing scene_bias (clean tanks only) …")
    clean = df[df["db_mean"] > FLOOR_DB]
    per_scene = clean.groupby("date").agg(
        n_clean=("tank_id", "count"),
        scene_bias=("db_mean", "median"),
    ).reset_index()

    # Invalidate scenes with < MIN_CLEAN tanks
    nan_scenes = per_scene[per_scene.n_clean < MIN_CLEAN]
    per_scene.loc[per_scene.n_clean < MIN_CLEAN, "scene_bias"] = np.nan

    valid_scenes = per_scene[per_scene.scene_bias.notna()]
    print(f"  Valid scenes (>= {MIN_CLEAN} clean tanks): {len(valid_scenes)} / {n_dates}")
    print(f"  NaN scenes: {len(nan_scenes)}")
    if len(nan_scenes):
        print(f"  NaN scene dates: {list(nan_scenes.date)}")
    print(f"  scene_bias range (valid): [{valid_scenes.scene_bias.min():.4f}, {valid_scenes.scene_bias.max():.4f}] dB")
    print()

    # Save updated scene_bias
    bias_path = OUT_DIR / "h1_scene_bias_fixed.csv"
    per_scene[["date","n_clean","scene_bias"]].to_csv(bias_path, index=False)
    print(f"  scene_bias_fixed → {bias_path}")

    print("[3] Computing db_norm_fixed …")
    df = df.merge(per_scene[["date", "scene_bias"]], on="date", how="left")
    df["db_norm"] = df["db_mean"] - df["scene_bias"]  # NaN where scene_bias is NaN

    normed = df[df.db_norm.notna()]
    null_norm = df[df.db_norm.isna()]
    print(f"  db_norm computed: {len(normed)} rows")
    print(f"  db_norm NULL (NaN scene): {len(null_norm)} rows")
    print(f"  db_norm range: [{normed.db_norm.min():.4f}, {normed.db_norm.max():.4f}] dB")
    print()

    if DRY_RUN:
        print("DRY-RUN complete. Pass --force to write to Supabase.")
        return

    print("[4] Upserting db_norm to Supabase …")
    # Write all rows: NaN → null (JSON null), numeric → value
    batch = []
    written = 0
    null_written = 0

    for _, row in df.iterrows():
        db_norm_val = None if pd.isna(row["db_norm"]) else round(float(row["db_norm"]), 4)
        batch.append({
            "terminal_id": TERMINAL_ID,
            "orbit_direction": ORBIT_DIR,
            "date": row["date"],
            "tank_id": row["tank_id"],
            "db_norm": db_norm_val,
        })
        if db_norm_val is None:
            null_written += 1
        else:
            written += 1

        if len(batch) >= 200:
            sb_upsert("terminal_observations", batch)
            batch = []

    if batch:
        sb_upsert("terminal_observations", batch)

    total_written = written + null_written
    print(f"\nDone.")
    print(f"  db_norm (numeric): {written}")
    print(f"  db_norm (NULL):    {null_written}")
    print(f"  total upserted:    {total_written}")
    print(f"\nInvariant: {written} + {null_written} = {total_written}  (expected {n_total})")
    assert total_written == n_total, f"INVARIANT FAIL: {total_written} != {n_total}"
    print("  [INVARIANT OK]")
    print("\nNext: re-run series_v2.py to regenerate CSVs.")


if __name__ == "__main__":
    main()
