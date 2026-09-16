"""
conftest.py — Mock heavy dependencies before any script import.

sentinelhub and supabase are not installed in the test environment.
Stub them at sys.modules so that config.py and locations.py can be
imported without network access or credentials.
"""

import sys
import types
from unittest.mock import MagicMock

# ── stub sentinelhub ──────────────────────────────────────────────────────────
_sh = types.ModuleType("sentinelhub")
_sh.SHConfig = MagicMock
_sh.DataCollection = MagicMock()
_sh.DataCollection.SENTINEL1_IW = MagicMock()
_sh.DataCollection.SENTINEL1_IW.define_from = MagicMock(return_value=MagicMock())
_sh.BBox = MagicMock
_sh.CRS = MagicMock()
_sh.CRS.WGS84 = "WGS84"
sys.modules["sentinelhub"] = _sh

# ── stub supabase ─────────────────────────────────────────────────────────────
_sb_mod = types.ModuleType("supabase")
_sb_mod.create_client = MagicMock(return_value=MagicMock())
sys.modules["supabase"] = _sb_mod

import pytest  # noqa: E402  (must come after sys.modules patching)
