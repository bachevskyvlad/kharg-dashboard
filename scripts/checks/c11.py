"""
C11 — Orbits / cadence.

Per terminal: pass counts by year, gaps (calendar years with 0 passes),
working orbits, mono vs mix.

Output CSV columns:
  terminal_id, year, relative_orbit, orbit_direction, n_passes,
  check_id, run_date, git_sha

Summary CSV (one row per terminal):
  terminal_id, n_total, first_date, last_date, n_years_coverage,
  n_gap_years, orbit_summary, is_mono, check_id, run_date, git_sha, verdict
"""

import pandas as pd
from . import RUN_DATE, SHA

# Normalize raw orbit_direction labels to canonical ASC/DESC/UNKNOWN
_DIR_NORM = {
    "ASC": "ASC", "ASCENDING": "ASC",
    "DESC": "DESC", "DESCENDING": "DESC",
}


def _norm_dir(d) -> str:
    if pd.isna(d):
        return "UNKNOWN"
    return _DIR_NORM.get(str(d).strip().upper(), str(d).strip())


def run(terminal_id: str, df: pd.DataFrame, _registry_tanks: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (detail_df, summary_df)."""
    df = df.copy()
    df["year"] = pd.to_datetime(df["date"]).dt.year
    df["orbit_dir_norm"] = df["orbit_direction"].apply(_norm_dir)

    detail_rows = []
    for (yr, orb, dirn_raw, dirn_norm), grp in df.groupby(
        ["year", "relative_orbit", "orbit_direction", "orbit_dir_norm"], dropna=False
    ):
        detail_rows.append({
            "terminal_id": terminal_id,
            "year": yr,
            "relative_orbit": orb,
            "orbit_direction_raw": dirn_raw,
            "orbit_direction": dirn_norm,
            "n_passes": int(grp["date"].nunique()),
            "check_id": "C11",
            "run_date": RUN_DATE,
            "git_sha": SHA,
        })

    detail_df = pd.DataFrame(detail_rows)

    # Summary
    all_years = set(range(int(df["year"].min()), int(df["year"].max()) + 1)) if len(df) else set()
    covered_years = set(df["year"].unique())
    gap_years = sorted(all_years - covered_years)

    # Orbit combos using normalized direction
    orbit_combos = (
        df.drop_duplicates(subset=["relative_orbit", "orbit_dir_norm"])[
            ["relative_orbit", "orbit_dir_norm"]
        ]
        .apply(lambda r: f"{r['relative_orbit']}/{r['orbit_dir_norm']}", axis=1)
        .tolist()
    )
    unique_combos = sorted(set(orbit_combos))
    # Include per-combo pass counts
    combo_counts = (
        df.groupby(["relative_orbit", "orbit_dir_norm"], dropna=False)["date"]
        .nunique()
        .reset_index()
    )
    combo_strs = [
        f"{row['relative_orbit']}/{row['orbit_dir_norm']}(n={row['date']})"
        for _, row in combo_counts.iterrows()
    ]
    orbit_summary = "; ".join(sorted(combo_strs))
    is_mono = len(unique_combos) == 1

    # Flag raw labels that differ from normalized
    raw_labels = sorted(set(str(d) for d in df["orbit_direction"].dropna().unique()))
    norm_mismatch = [lb for lb in raw_labels if lb not in _DIR_NORM]

    summary_df = pd.DataFrame([{
        "terminal_id": terminal_id,
        "n_total": int(df["date"].nunique()),
        "first_date": str(df["date"].min().date()) if len(df) else "",
        "last_date": str(df["date"].max().date()) if len(df) else "",
        "n_years_coverage": len(covered_years),
        "n_gap_years": len(gap_years),
        "gap_years": str(gap_years) if gap_years else "none",
        "orbit_summary": orbit_summary,
        "is_mono": is_mono,
        "raw_dir_labels": str(raw_labels),
        "norm_mismatch_labels": str(norm_mismatch) if norm_mismatch else "none",
        "check_id": "C11",
        "run_date": RUN_DATE,
        "git_sha": SHA,
        "verdict": "",
    }])

    return detail_df, summary_df
