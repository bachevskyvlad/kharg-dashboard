"""
checks/ — Terminal health check module for OilRadar.

Each submodule implements one check (C04, C05, …) and exports:
  run(terminal_id, df, registry_tanks) -> pd.DataFrame

  df columns required: date, tank_id, db_mean, relative_orbit, orbit_direction
  registry_tanks: list of tank dicts from tank_registry.LOCATIONS

Common CSV columns in every output:
  terminal_id, tank_id, check_id, run_date, git_sha, <metric cols>, verdict

NOT_RUN / NOT_DETECTABLE / N_A are string sentinels in the verdict column.
"""

import subprocess
from datetime import date as _date


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


RUN_DATE = str(_date.today())
SHA = git_sha()

DB_FLOOR = -5.0  # canonical clip floor from config.py
STUCK_FLOOR_THRESH = 0.50  # Al-Faw onset criterion: frac_at_floor >= 50%
FLAT_DELTA_THRESH = 0.3   # |Δdb| < 0.3 dB → "flat" pass
STEP_WL_THRESHOLD = 2.5   # frozen whitelist step threshold (dB)
