"""
Test that fetch_new_records passes dataFilter + mosaicking when orbit_cfg is set,
and that the Kharg call path (no orbit_cfg) leaves other_args unchanged.

These tests mock SentinelHubRequest so no network calls are made.
sentinelhub is not installed in the test environment — the entire module is stubbed.
"""
import sys
import os
import types
import unittest.mock as mock

import pytest

# ── Stub sentinelhub before importing sar_fetch ───────────────────────────────
def _stub_sentinelhub():
    sh = types.ModuleType("sentinelhub")

    class _BBox:
        def __init__(self, bbox, crs=None):
            self.min_x, self.min_y, self.max_x, self.max_y = bbox

    class _CRS:
        WGS84 = "WGS84"

    class _MimeType:
        TIFF = "TIFF"

    class _DataCollection:
        SENTINEL1_IW = mock.MagicMock()
        SENTINEL1_IW.define_from = mock.MagicMock(return_value=mock.MagicMock())

    class _SHConfig:
        sh_client_id = ""
        sh_client_secret = ""
        sh_token_url = ""
        sh_base_url = ""

    class _SentinelHubCatalog:
        def __init__(self, config=None): pass
        def search(self, **kwargs): return iter([])
        def get_info(self, **kwargs): return {}

    sh.BBox = _BBox
    sh.CRS = _CRS
    sh.MimeType = _MimeType
    sh.DataCollection = _DataCollection
    sh.SHConfig = _SHConfig
    sh.SentinelHubRequest = mock.MagicMock()
    sh.SentinelHubCatalog = _SentinelHubCatalog
    sys.modules["sentinelhub"] = sh

_stub_sentinelhub()

# Also stub supabase (imported by config)
_sb = types.ModuleType("supabase")
_sb.create_client = mock.MagicMock(return_value=mock.MagicMock())
sys.modules.setdefault("supabase", _sb)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _captured_other_args(monkeypatch, orbit_cfg):
    """
    Call fetch_new_records with a single dummy date and capture the other_args
    dict that was passed to SentinelHubRequest.input_data().
    """
    import datetime
    from sentinelhub import BBox, CRS

    captured = {}

    class FakeRequest:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def input_data(data_collection, time_interval, other_args=None):
            captured["other_args"] = other_args
            return object()

        @staticmethod
        def output_response(*a):
            return object()

        def get_data(self):
            return []  # empty → no records emitted

    import sar_fetch
    monkeypatch.setattr(sar_fetch, "SentinelHubRequest", FakeRequest)

    bbox = BBox(bbox=(49.98, 26.81, 49.99, 26.83), crs=CRS.WGS84)
    dates = [datetime.date(2024, 1, 10)]
    sar_fetch.fetch_new_records(dates, [], bbox, 10, 10, orbit_cfg=orbit_cfg)
    return captured.get("other_args", {})


def test_no_orbit_cfg_no_datafilter(monkeypatch):
    args = _captured_other_args(monkeypatch, orbit_cfg=None)
    assert "dataFilter" not in args
    assert "mosaicking" not in args
    assert "processing" in args


def test_orbit_cfg_adds_datafilter(monkeypatch):
    cfg = [{"direction": "DESC", "relative_orbit": 35, "los_azimuth": 282}]
    args = _captured_other_args(monkeypatch, orbit_cfg=cfg)
    assert "dataFilter" in args
    assert args["dataFilter"]["orbitDirection"] == "DESCENDING"
    assert args["dataFilter"]["relativeOrbitNumber"] == 35
    assert args["mosaicking"] == "ORBIT"


def test_orbit_cfg_asc_direction(monkeypatch):
    cfg = [{"direction": "ASC", "relative_orbit": 14, "los_azimuth": 98}]
    args = _captured_other_args(monkeypatch, orbit_cfg=cfg)
    assert args["dataFilter"]["orbitDirection"] == "ASCENDING"
    assert args["dataFilter"]["relativeOrbitNumber"] == 14


def test_processing_block_always_present(monkeypatch):
    cfg = [{"direction": "DESC", "relative_orbit": 35, "los_azimuth": 282}]
    args = _captured_other_args(monkeypatch, orbit_cfg=cfg)
    assert "processing" in args
    assert args["processing"]["orthorectify"] is True
