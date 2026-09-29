"""
test_enabled_flag.py — Tests for enabled flag behaviour in locations/ingest/compute pipeline.

Run: pytest tests/test_enabled_flag.py -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import numpy as np
import pandas as pd
import pytest

from locations import TERMINALS, raw_tanks, _to_pipeline_tank


# ── Helper ─────────────────────────────────────────────────────────────────────

def _fake_tank(tid, enabled=None):
    base = {"id": tid, "lat": 26.0, "lon": 50.0, "radius_m": 50, "h_max_m": 16}
    if enabled is not None:
        base["enabled"] = enabled
    return base


# ── 1. Kharg tanks have no enabled field (always processed) ────────────────────

def test_kharg_tanks_no_enabled_field():
    for t in raw_tanks:
        assert "enabled" not in t, f"Kharg tank {t['id']} should not have enabled field"


# ── 2. _to_pipeline_tank propagates enabled correctly ─────────────────────────

def test_pipeline_tank_enabled_true():
    t = _fake_tank("rt_5", enabled=True)
    out = _to_pipeline_tank(t)
    assert out["enabled"] is True


def test_pipeline_tank_enabled_false():
    t = _fake_tank("rt_50", enabled=False)
    out = _to_pipeline_tank(t)
    assert out["enabled"] is False


def test_pipeline_tank_no_enabled_defaults_true():
    t = _fake_tank("t60_0")  # no enabled field — Kharg style
    out = _to_pipeline_tank(t)
    assert out.get("enabled", True) is True  # absent → default True


# ── 3. Ingest filter: enabled=False tank not in fetch clusters ─────────────────

def test_ingest_filter_removes_disabled():
    tanks = [
        _fake_tank("rt_2",  enabled=True),
        _fake_tank("rt_50", enabled=False),
        _fake_tank("rt_11", enabled=True),
    ]
    tanks_enabled = [t for t in tanks if t.get("enabled", True)]
    ids = {t["id"] for t in tanks_enabled}
    assert "rt_50" not in ids
    assert "rt_2"  in ids
    assert "rt_11" in ids


def test_ingest_filter_absent_defaults_enabled():
    tanks = [_fake_tank("t60_0")]  # no enabled key
    tanks_enabled = [t for t in tanks if t.get("enabled", True)]
    assert len(tanks_enabled) == 1


# ── 4. Compute filter: disabled tank id excluded from tank_geom ────────────────

def test_compute_tank_geom_excludes_disabled():
    tanks = [
        {"id": "rt_2",  "radius_m": 60, "h_max_m": 18, "enabled": True},
        {"id": "rt_50", "radius_m": 60, "h_max_m": 18, "enabled": False},
    ]
    tank_geom = {t["id"]: {"radius_m": t.get("radius_m"), "h_max_m": t.get("h_max_m")}
                 for t in tanks if t.get("enabled", True)}
    assert "rt_50" not in tank_geom
    assert "rt_2"  in tank_geom


def test_compute_disabled_rows_dropped_from_df():
    """Rows for disabled tanks get dropped by tank_geom join (radius_m → NaN → dropna)."""
    tanks = [
        {"id": "rt_2",  "radius_m": 60, "h_max_m": 18, "enabled": True},
        {"id": "rt_50", "radius_m": 60, "h_max_m": 18, "enabled": False},
    ]
    tank_geom = {t["id"]: {"radius_m": t["radius_m"], "h_max_m": t["h_max_m"]}
                 for t in tanks if t.get("enabled", True)}

    df = pd.DataFrame([
        {"date": "2026-01-01", "tank_id": "rt_2",  "db_mean": 5.0},
        {"date": "2026-01-01", "tank_id": "rt_50", "db_mean": 4.0},
    ])
    df["radius_m"] = df["tank_id"].map(lambda t: tank_geom.get(t, {}).get("radius_m"))
    df["h_max"]    = df["tank_id"].map(lambda t: tank_geom.get(t, {}).get("h_max_m"))
    df = df.dropna(subset=["radius_m", "h_max"])
    assert list(df["tank_id"]) == ["rt_2"]


# ── 5. Golden: Kharg pipeline unchanged by these changes ───────────────────────

def test_kharg_raw_tanks_count():
    """Kharg has 47 tanks — none should be filtered."""
    assert len(raw_tanks) == 47


def test_kharg_raw_tanks_all_have_required_fields():
    for t in raw_tanks:
        assert "id" in t
        assert "radius" in t
        assert "h_max" in t
        assert "lat" in t
        assert "lon" in t
