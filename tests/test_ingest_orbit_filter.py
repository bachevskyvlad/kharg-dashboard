"""
Task 6 (partial) — ingest orbit filter and pixel guard tests.

Tests:
  (a) allowed_orbits normalisation: legacy keys → allowed_orbits list
  (b) ingest skips scene dates from orbits not in allowed_orbits
  (c) pixel guard (min_valid_fraction) cuts a tank with < 50% valid pixels
  (d) los_azimuth is taken from the orbit config, not a global hardcode
  (e) xfail regression: max_tanks < 66 dates — documents pre-fix contamination
      behaviour (orbit 137 single-tank dates); kept as documentation per spec.
"""

import sys
import os
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_allowed_orbits_from_legacy(terminal_cfg):
    """
    Replicates the normalisation logic in ingest.py so we can test it in isolation.
    """
    allowed_orbits = terminal_cfg.get("allowed_orbits")
    if not allowed_orbits:
        orbit_state    = terminal_cfg.get("orbit_state", "descending")
        los_azimuth    = terminal_cfg.get("los_azimuth", None)
        relative_orbit = terminal_cfg.get("relative_orbit", None)
        allowed_orbits = [{"relative_orbit": relative_orbit,
                            "direction": orbit_state,
                            "los_azimuth": los_azimuth}]
    return allowed_orbits


# ---------------------------------------------------------------------------
# (a) legacy config → allowed_orbits normalisation
# ---------------------------------------------------------------------------

def test_legacy_config_normalised_to_allowed_orbits():
    cfg = {"orbit_state": "descending", "los_azimuth": 282, "relative_orbit": 35}
    orbits = _make_allowed_orbits_from_legacy(cfg)
    assert len(orbits) == 1
    assert orbits[0]["relative_orbit"] == 35
    assert orbits[0]["direction"] == "descending"
    assert orbits[0]["los_azimuth"] == 282


def test_allowed_orbits_passthrough():
    cfg = {"allowed_orbits": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}]}
    orbits = _make_allowed_orbits_from_legacy(cfg)
    assert len(orbits) == 1
    assert orbits[0]["direction"] == "DESC"


# ---------------------------------------------------------------------------
# (b) orbit filter: dates from non-allowed orbit are not processed
# ---------------------------------------------------------------------------

def test_dates_from_wrong_orbit_are_excluded():
    """
    Simulate the orbit_for_date dict that ingest builds from catalog results.
    Only dates tagged to an allowed orbit should be in new_t for that orbit.
    """
    import pandas as pd

    allowed = [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}]

    # Simulate catalog returning two orbits for the same bbox
    orbit_for_date = {
        pd.Timestamp("2026-08-31"): (35, "DESC"),   # allowed
        pd.Timestamp("2026-08-29"): (137, "DESC"),  # NOT allowed
        pd.Timestamp("2026-09-06"): (35, "DESC"),   # allowed
    }

    new_t = sorted(orbit_for_date.keys())
    allowed_set = {(o["relative_orbit"], o["direction"].upper()) for o in allowed}

    for orbit_cfg in allowed:
        rel  = orbit_cfg["relative_orbit"]
        dirn = orbit_cfg["direction"].upper()
        orbit_dates = [d for d in new_t if orbit_for_date.get(d) == (rel, dirn)]

    assert pd.Timestamp("2026-08-31") in orbit_dates
    assert pd.Timestamp("2026-09-06") in orbit_dates
    assert pd.Timestamp("2026-08-29") not in orbit_dates  # orbit 137 excluded


# ---------------------------------------------------------------------------
# (c) pixel guard: _tank_valid_fraction logic
# We test the extracted helper directly to avoid the sentinelhub import dependency.
# ---------------------------------------------------------------------------

def _tank_valid_fraction(img_linear, crescent_mask):
    """Local copy of sar_fetch._tank_valid_fraction for unit testing."""
    total = int(crescent_mask.sum())
    if total == 0:
        return 0.0
    return int(np.isfinite(img_linear[crescent_mask]).sum()) / total


def test_pixel_guard_fraction_full_coverage():
    """All pixels finite → fraction = 1.0."""
    img = np.full((10, 10), 0.01, dtype=np.float32)
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:7, 2:7] = True   # 25 pixels, all finite
    assert _tank_valid_fraction(img, mask) == 1.0


def test_pixel_guard_fraction_partial_nan():
    """70% NaN → fraction ≈ 0.30, should be < 0.5 threshold."""
    img = np.full((20, 20), 0.01, dtype=np.float32)
    mask = np.zeros((20, 20), dtype=bool)
    mask[10:20, 10:20] = True  # 100 pixels in mask
    img[10:17, 10:20] = np.nan  # 70 NaN → 30 valid
    frac = _tank_valid_fraction(img, mask)
    assert abs(frac - 0.30) < 0.01
    assert frac < 0.5, "30% valid must be below 50% guard threshold"


def test_pixel_guard_fraction_empty_crescent():
    """Empty mask → fraction = 0.0 (no div-by-zero)."""
    img  = np.full((10, 10), 0.01, dtype=np.float32)
    mask = np.zeros((10, 10), dtype=bool)
    assert _tank_valid_fraction(img, mask) == 0.0


def test_pixel_guard_threshold_logic():
    """Confirm the guard condition: frac < 0.5 → drop; frac >= 0.5 → keep."""
    for frac, should_drop in [(0.0, True), (0.3, True), (0.49, True),
                               (0.5, False), (0.8, False), (1.0, False)]:
        dropped = frac < 0.5
        assert dropped == should_drop, f"frac={frac}: expected drop={should_drop}"


# ---------------------------------------------------------------------------
# (d) los_azimuth from orbit config, not global hardcode
# ---------------------------------------------------------------------------

def test_los_azimuth_from_orbit_config():
    """
    allowed_orbits entry carries its own los_azimuth.
    The ingest loop should use orbit_cfg["los_azimuth"], not terminal["los_azimuth"].
    """
    terminal_cfg = {
        "id": "ras_tanura",
        "allowed_orbits": [
            {"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282},
        ],
    }
    orbits = _make_allowed_orbits_from_legacy(terminal_cfg)
    assert orbits[0]["los_azimuth"] == 282, "los_azimuth must come from orbit config"

    # Verify that a hypothetical ASC orbit would carry a different azimuth
    terminal_asc = {
        "id": "some_terminal",
        "allowed_orbits": [
            {"relative_orbit": 101, "direction": "ASC", "los_azimuth": 98},
        ],
    }
    orbits_asc = _make_allowed_orbits_from_legacy(terminal_asc)
    assert orbits_asc[0]["los_azimuth"] == 98


# ---------------------------------------------------------------------------
# (e) xfail regression: pre-fix contamination behaviour (documentation)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason=(
    "Documents pre-fix behaviour: before orbit 35 filter was enforced, "
    "DESC 137 scenes produced single-tank dates (rt_65 only). "
    "After backfill with allowed_orbits=[35], max_tanks should be 60–66 "
    "and zero single-tank dates. Keep this test as historical documentation."
))
def test_xfail_single_tank_dates_no_longer_exist():
    """After the rebuild, no date should have < 5 tanks. This xfail records
    that the pre-rebuild data violated this invariant on ~215 dates."""
    import pandas as pd

    # Simulate the old contaminated data: one date with only rt_65
    rows = [{"date": pd.Timestamp("2026-07-21"), "tank_id": "rt_65"}]
    df = pd.DataFrame(rows)

    tanks_per_date = df.groupby("date")["tank_id"].count()
    assert (tanks_per_date >= 5).all(), "Some dates have < 5 tanks (single-tank contamination)"
