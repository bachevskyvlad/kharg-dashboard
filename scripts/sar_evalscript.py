"""
sar_evalscript.py — canonical SAR evalscript and dB conversion helpers.

Single source of truth for ingest, panels, and smoke test.

Physics:
  - Evalscript returns raw linear VV power (not dB).
  - We average in linear domain first, then convert to dB — avoids
    Jensen's inequality bias of averaging log values directly.
  - CDSE Process API does not accept a 'units' string for S1-GRD;
    dB conversion must be done in Python after pixel extraction.
  - nodata / fill pixels arrive as NaN or ≤0 — clip to 1e-10 before
    log, so result stays finite (no -inf).
"""

import numpy as np

# Canonical evalscript — returns raw linear VV power.
# Identical to sar_fetch._EVALSCRIPT (Kharg ingest path).
SAR_EVALSCRIPT = """//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""


def linear_to_db(arr: np.ndarray, nodata_sentinel: float = 0.0) -> np.ndarray:
    """
    Convert linear VV power array to dB, setting nodata pixels to NaN.

    nodata_sentinel: value the API uses for no-data (0.0 for S1-GRD fill).
    Returns float32 array with NaN where the pixel is nodata.
    """
    out = arr.astype(np.float32, copy=True)
    nodata_mask = ~np.isfinite(out) | (out <= nodata_sentinel)
    out[nodata_mask] = np.nan
    # Clip positives away from zero before log
    valid = ~nodata_mask
    out[valid] = 10.0 * np.log10(np.clip(out[valid], 1e-10, None))
    return out


def mean_db(arr: np.ndarray, nodata_sentinel: float = 0.0) -> float:
    """
    Compute mean dB over a pixel patch:
      1. Mask nodata
      2. Average in linear domain (avoids Jensen bias)
      3. Convert mean to dB

    Returns NaN if no valid pixels.
    """
    a = arr.astype(np.float64)
    valid = np.isfinite(a) & (a > nodata_sentinel)
    if not np.any(valid):
        return float("nan")
    mean_linear = float(np.mean(a[valid]))
    return 10.0 * np.log10(np.clip(mean_linear, 1e-10, None))
