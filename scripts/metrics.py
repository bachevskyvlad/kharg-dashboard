"""
metrics.py — Canonical SAR metrics shared across all pipeline stages.

All WL screens and re-audits must call tank_step_db() from here.
Inline step formulas in pipeline scripts are forbidden.
"""

import numpy as np


def tank_step_db(series) -> float:
    """
    Canonical pass-to-pass step metric: median(|db[t] - db[t-1]|).

    Parameters
    ----------
    series : sequence of float
        db_mean values sorted chronologically (ascending date order).

    Returns
    -------
    float
        Median of absolute consecutive differences. Returns NaN if fewer
        than 2 values are provided.

    Notes
    -----
    This measures pass-to-pass volatility, NOT total dynamic range.
    p95-p5 (range over years) is explicitly the wrong metric for the
    WL screen — it systematically rejects slowly-trending tanks.
    """
    vals = [v for v in series if v is not None and np.isfinite(v)]
    if len(vals) < 2:
        return float("nan")
    diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
    return float(np.median(diffs))


# ── Unit tests (run with: python metrics.py) ─────────────────────────────────

def _run_tests():
    import math

    # Test 1: known synthetic series
    # Series: [0, 1, 2, 3, 4] → diffs all 1.0 → median = 1.0
    assert tank_step_db([0, 1, 2, 3, 4]) == 1.0, "Test 1 failed"

    # Test 2: known answer from pilot (approximate)
    # rt_8 pilot = 1.718; use a series whose median|diff| ≈ 1.718
    series_rt8 = [5.0, 6.718, 5.0, 6.718, 5.0, 6.718, 5.0, 6.718, 5.0, 6.718]
    result = tank_step_db(series_rt8)
    assert abs(result - 1.718) < 1e-9, f"Test 2 failed: {result}"

    # Anti-test: large total range, small step → metric must be small
    # Series slowly drifts from 0 to 10 in 0.1 increments → range=10, step=0.1
    slow_drift = [i * 0.1 for i in range(101)]
    result_slow = tank_step_db(slow_drift)
    assert result_slow < 0.5, f"Anti-test failed: step={result_slow:.3f} (expected <0.5 for slow drift)"
    # Sanity: p95-p5 of same series would be ~9.0, which would FAIL the 2.5 threshold
    p95_p5 = np.percentile(slow_drift, 95) - np.percentile(slow_drift, 5)
    assert p95_p5 > 5.0, f"Anti-test sanity check failed: p95-p5={p95_p5}"

    # Test 3: fewer than 2 values → NaN
    assert math.isnan(tank_step_db([])), "Test 3a failed"
    assert math.isnan(tank_step_db([5.0])), "Test 3b failed"

    # Test 4: NaN/None values are ignored
    result_nan = tank_step_db([1.0, float("nan"), 2.0, None, 3.0])
    assert abs(result_nan - 1.0) < 1e-9, f"Test 4 failed: {result_nan}"

    print("All metrics.py unit tests PASSED")
    print(f"  Anti-test: step={result_slow:.4f} dB  p95-p5={p95_p5:.4f} dB  (metric is {p95_p5/result_slow:.0f}x smaller)")


if __name__ == "__main__":
    _run_tests()
