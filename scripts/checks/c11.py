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


def run(terminal_id: str, df: pd.DataFrame, _registry_tanks: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (detail_df, summary_df)."""
    detail_rows = []
    df = df.copy()
    df["year"] = pd.to_datetime(df["date"]).dt.year

    for (yr, orb, dirn), grp in df.groupby(["year", "relative_orbit", "orbit_direction"]):
        detail_rows.append({
            "terminal_id": terminal_id,
            "year": yr,
            "relative_orbit": orb,
            "orbit_direction": dirn,
            "n_passes": int(grp["date"].nunique()),
            "check_id": "C11",
            "run_date": RUN_DATE,
            "git_sha": SHA,
        })

    detail_df = pd.DataFrame(detail_rows)

    # Summary
    all_years = set(range(df["year"].min(), df["year"].max() + 1)) if len(df) else set()
    covered_years = set(df["year"].unique())
    gap_years = sorted(all_years - covered_years)

    orbit_combo = (
        df.drop_duplicates(subset=["relative_orbit", "orbit_direction"])[
            ["relative_orbit", "orbit_direction"]
        ]
        .apply(lambda r: f"{r['relative_orbit']}/{r['orbit_direction']}", axis=1)
        .tolist()
    )
    orbit_summary = "; ".join(sorted(set(orbit_combo)))
    is_mono = len(set(orbit_combo)) == 1

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
        "check_id": "C11",
        "run_date": RUN_DATE,
        "git_sha": SHA,
        "verdict": "",
    }])

    return detail_df, summary_df
