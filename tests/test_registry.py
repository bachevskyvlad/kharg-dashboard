"""
Group 4 — tank_registry: cluster intersection, geometry completeness.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from tank_registry import LOCATIONS


def _get_loc(loc_id):
    for loc in LOCATIONS:
        if loc["id"] == loc_id:
            return loc
    return None


# ── Test 1: every tank has radius_m and h_max_m ──────────────────────────────
def test_all_tanks_have_geometry():
    for loc in LOCATIONS:
        for tank in loc["tanks"]:
            assert "radius_m" in tank, f"{loc['id']}/{tank['id']} missing radius_m"
            assert "h_max_m" in tank, f"{loc['id']}/{tank['id']} missing h_max_m"
            assert tank["radius_m"] > 0
            assert tank["h_max_m"] > 0


# ── Test 2: every tank has lat/lon ───────────────────────────────────────────
def test_all_tanks_have_coordinates():
    for loc in LOCATIONS:
        for tank in loc["tanks"]:
            assert "lat" in tank, f"{loc['id']}/{tank['id']} missing lat"
            assert "lon" in tank, f"{loc['id']}/{tank['id']} missing lon"
            assert -90 <= tank["lat"] <= 90
            assert -180 <= tank["lon"] <= 180


# ── Test 3: cluster tank_ids are subsets of the parent's tank list ────────────
def test_cluster_tank_ids_are_subsets():
    for loc in LOCATIONS:
        if "clusters" not in loc:
            continue
        all_ids = {t["id"] for t in loc["tanks"]}
        for cluster in loc["clusters"]:
            for tid in cluster["tank_ids"]:
                assert tid in all_ids, (
                    f"{loc['id']}: cluster '{cluster['name']}' references "
                    f"tank '{tid}' not in tanks list"
                )


# ── Test 4: cluster tank_ids have no intersection (no tank in two clusters) ───
def test_cluster_tank_ids_no_overlap():
    for loc in LOCATIONS:
        if "clusters" not in loc:
            continue
        seen = set()
        for cluster in loc["clusters"]:
            for tid in cluster["tank_ids"]:
                assert tid not in seen, (
                    f"{loc['id']}: tank '{tid}' appears in multiple clusters"
                )
                seen.add(tid)


# ── Test 5: cluster union covers all tanks in ras_tanura ──────────────────────
def test_ras_tanura_clusters_cover_all_tanks():
    loc = _get_loc("ras_tanura")
    if loc is None or "clusters" not in loc:
        pytest.skip("ras_tanura not found or has no clusters")
    all_tank_ids = {t["id"] for t in loc["tanks"]}
    cluster_ids = set()
    for cluster in loc["clusters"]:
        cluster_ids.update(cluster["tank_ids"])
    assert cluster_ids == all_tank_ids, (
        f"Clusters cover {len(cluster_ids)} tanks but registry has {len(all_tank_ids)}"
    )


# ── Test 6: kharg has expected tank count (47) ────────────────────────────────
def test_kharg_tank_count():
    loc = _get_loc("kharg")
    assert loc is not None
    assert len(loc["tanks"]) == 47, f"Expected 47 tanks, got {len(loc['tanks'])}"


# ── Test 7: all location ids are unique ──────────────────────────────────────
def test_location_ids_unique():
    ids = [loc["id"] for loc in LOCATIONS]
    assert len(ids) == len(set(ids)), "Duplicate location IDs in registry"


# ── Test 8: all tank ids within a location are unique ────────────────────────
def test_tank_ids_unique_within_location():
    for loc in LOCATIONS:
        ids = [t["id"] for t in loc["tanks"]]
        assert len(ids) == len(set(ids)), f"{loc['id']} has duplicate tank IDs"
