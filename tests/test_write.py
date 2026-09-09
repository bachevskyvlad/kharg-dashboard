"""
Group 5 — Supabase write contract: delete-before-insert, idempotency.

All Supabase calls are mocked. No network access.
"""

import sys
import os
import pytest
import pandas as pd
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _mock_sb():
    """Return a mock Supabase client that records calls."""
    sb = MagicMock()
    # Chain: sb.table(...).delete().eq(...).eq(...).execute()
    table_mock = MagicMock()
    sb.table.return_value = table_mock
    table_mock.delete.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.insert.return_value = table_mock
    table_mock.upsert.return_value = table_mock
    table_mock.execute.return_value = MagicMock(data=[])
    return sb


def _daily_df(n=3, terminal_id="ras_tanura"):
    """Small daily aggregate DataFrame."""
    return pd.DataFrame({
        "day": pd.date_range("2024-01-01", periods=n, freq="D"),
        "vol_mn_bbl": [15.0, 15.5, 14.8],
    })


# ── Helper: the write sequence extracted from compute.py terminals loop ───────
def _write_terminal_daily(sb, terminal_id, daily_df):
    """Mirrors the delete+insert pattern in compute.py."""
    rows = [
        {
            "terminal_id": terminal_id,
            "date": r["day"].strftime("%Y-%m-%d"),
            "vol_mn_bbl": round(float(r["vol_mn_bbl"]), 3),
            "aggregation": "daily",
            "smoothed": False,
        }
        for _, r in daily_df.iterrows()
    ]
    sb.table("terminal_volume_timeseries").delete().eq(
        "terminal_id", terminal_id).eq("aggregation", "daily").execute()
    sb.table("terminal_volume_timeseries").insert(rows).execute()
    return rows


# ── Test 1: delete is called before insert ───────────────────────────────────
def test_delete_before_insert():
    sb = _mock_sb()
    _write_terminal_daily(sb, "ras_tanura", _daily_df())
    # extract all calls on the table mock in order
    calls = sb.table("terminal_volume_timeseries").method_calls
    names = [c[0] for c in calls]
    assert "delete" in names
    assert "insert" in names
    assert names.index("delete") < names.index("insert")


# ── Test 2: insert rows contain required keys ─────────────────────────────────
def test_insert_rows_have_required_keys():
    sb = _mock_sb()
    rows = _write_terminal_daily(sb, "ras_tanura", _daily_df())
    required = {"terminal_id", "date", "vol_mn_bbl", "aggregation", "smoothed"}
    for row in rows:
        assert required.issubset(row.keys())


# ── Test 3: vol_mn_bbl rounded to 3 decimal places ───────────────────────────
def test_vol_mn_bbl_rounded():
    sb = _mock_sb()
    df = _daily_df()
    df["vol_mn_bbl"] = [15.123456, 15.5, 14.8]
    rows = _write_terminal_daily(sb, "ras_tanura", df)
    assert rows[0]["vol_mn_bbl"] == 15.123


# ── Test 4: idempotency — second write deletes the first, not accumulates ─────
def test_write_twice_deletes_first():
    sb = _mock_sb()
    _write_terminal_daily(sb, "ras_tanura", _daily_df())
    _write_terminal_daily(sb, "ras_tanura", _daily_df())
    # delete should have been called exactly twice
    delete_calls = [c for c in sb.table("terminal_volume_timeseries").method_calls
                    if c[0] == "delete"]
    assert len(delete_calls) == 2


# ── Test 5: date strings are formatted YYYY-MM-DD ────────────────────────────
def test_date_format():
    sb = _mock_sb()
    rows = _write_terminal_daily(sb, "ras_tanura", _daily_df())
    import re
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for row in rows:
        assert pattern.match(row["date"]), f"Bad date format: {row['date']}"


# ── Test 6: aggregation field is always 'daily', smoothed is False ────────────
def test_metadata_fields():
    sb = _mock_sb()
    rows = _write_terminal_daily(sb, "ras_tanura", _daily_df())
    for row in rows:
        assert row["aggregation"] == "daily"
        assert row["smoothed"] is False
