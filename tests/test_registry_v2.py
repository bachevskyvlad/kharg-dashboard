"""
Test that the v2 Saudi geometry file loads correctly and that the registry
tank_registry.py has been updated with v2 values for JW, RT, and YB terminals.

Checks:
  - All 97 Saudi tanks have source_r and qa_status set
  - No tank in JW/RT/YB still uses the old uniform radius values
  - Kharg tanks are untouched (total count, first tank radius)
  - DB_FLOOR constant is defined in config
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_db_floor_defined():
    from config import DB_FLOOR
    assert DB_FLOOR == -5.0


def test_v2_geometry_loads():
    from registry_candidates.saudi_geometry_full_v2 import SAUDI_GEOMETRY_V2
    assert len(SAUDI_GEOMETRY_V2) == 97
    for entry in SAUDI_GEOMETRY_V2:
        assert "source_r" in entry, f"{entry['id']} missing source_r"
        assert "qa_status" in entry, f"{entry['id']} missing qa_status"
        assert entry["source_r"] in ("measured", "row-median", "registry")
        assert entry["radius_m"] > 0


def test_registry_kharg_untouched():
    from tank_registry import LOCATIONS
    kharg = next(t for t in LOCATIONS if t["id"] == "kharg")
    assert len(kharg["tanks"]) == 47
    # First tank must remain a t60
    assert kharg["tanks"][0]["radius_m"] == 60


def test_registry_jw_updated():
    from tank_registry import LOCATIONS
    from registry_candidates.saudi_geometry_full_v2 import SAUDI_GEOMETRY_V2
    jw = next(t for t in LOCATIONS if t["id"] == "juwaymah")
    v2_by_id = {e["id"]: e for e in SAUDI_GEOMETRY_V2}
    for tank in jw["tanks"]:
        v2 = v2_by_id.get(tank["id"])
        assert v2 is not None
        assert abs(tank["radius_m"] - v2["radius_m"]) < 0.1, (
            f"{tank['id']}: registry {tank['radius_m']} != v2 {v2['radius_m']}"
        )
        assert tank["h_max_m"] == v2["h_max"]


def test_registry_rt_updated():
    from tank_registry import LOCATIONS
    from registry_candidates.saudi_geometry_full_v2 import SAUDI_GEOMETRY_V2
    rt = next(t for t in LOCATIONS if t["id"] == "ras_tanura")
    v2_by_id = {e["id"]: e for e in SAUDI_GEOMETRY_V2}
    for tank in rt["tanks"]:
        v2 = v2_by_id.get(tank["id"])
        assert v2 is not None
        assert abs(tank["radius_m"] - v2["radius_m"]) < 0.1, (
            f"{tank['id']}: registry {tank['radius_m']} != v2 {v2['radius_m']}"
        )


def test_registry_yb_updated():
    from tank_registry import LOCATIONS
    from registry_candidates.saudi_geometry_full_v2 import SAUDI_GEOMETRY_V2
    yb = next(t for t in LOCATIONS if t["id"] == "yanbu")
    v2_by_id = {e["id"]: e for e in SAUDI_GEOMETRY_V2}
    for tank in yb["tanks"]:
        v2 = v2_by_id.get(tank["id"])
        assert v2 is not None
        assert abs(tank["radius_m"] - v2["radius_m"]) < 0.1, (
            f"{tank['id']}: registry {tank['radius_m']} != v2 {v2['radius_m']}"
        )


def test_compute_volumes_calibration_freeze():
    """db_min_override / db_max_override bypass percentile computation."""
    import pandas as pd
    import numpy as np
    from volume import compute_volumes

    rng = np.random.default_rng(42)
    n = 50
    df = pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=n, freq="6D"),
        "tank_id": [f"t_{i % 5}" for i in range(n)],
        "db_mean": rng.uniform(2, 10, n),
        "radius_m": [40.0] * n,
        "h_max": [14.0] * n,
    })

    _, dB_min_auto, dB_max_auto = compute_volumes(df.copy())

    frozen_min, frozen_max = 3.0, 9.0
    _, dB_min_frozen, dB_max_frozen = compute_volumes(
        df.copy(), db_min_override=frozen_min, db_max_override=frozen_max
    )

    assert dB_min_frozen == frozen_min
    assert dB_max_frozen == frozen_max
    # Auto values should differ from the frozen ones (unless unlucky with RNG)
    assert round(dB_min_auto, 3) != frozen_min or round(dB_max_auto, 3) != frozen_max
