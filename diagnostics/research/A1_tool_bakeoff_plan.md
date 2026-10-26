# А-1: Tool Bake-Off Plan — Kharg DESC-35 (3 scenes)

**Status**: PLAN ONLY — SentinelHub quota required for actual fetch  
**Branch**: research/2026-10-auto  
**Protocol**: §6 — Kharg-only, results CSV only in diagnostics/research/.../image/

---

## Scenes

| # | Date | Rationale |
|---|------|-----------|
| 1 | 2024-09-21 | Mid-period baseline |
| 2 | 2024-10-03 | 12-day follow-up (one full revisit) |
| 3 | 2026-05-14 | Recent (2026 data) |

---

## Tools Under Evaluation

| Tool | Method | Source |
|------|--------|--------|
| **A** | Lee filter (kernel 7×7, sigma=0.5) | scipy/skimage |
| **B** | Refined Lee (edge-preserving, 7×7) | custom Python (Ulaby et al.) |
| **C** | Quegan multi-look coherence (2×8 looks) | numpy |
| **D** | ESA SNAP — Speckle Filter (Refined Lee) | via `snappy` or graph XML |
| **E** | SLC → calibrate → deburst → geocode UTM 5m (CDSE Burst API) | CDSE catalog + SNAP |
| **F** | GRD baseline (no filter, GAMMA0_TERRAIN) | current production |

Co-registration: `scikit-image.registration.phase_cross_correlation` on reference scene, applied to all 3 scenes.

---

## Evaluation Criteria (5)

| # | Metric | Definition | Target |
|---|--------|-----------|--------|
| 1 | **noise_sigma** | std(dB) in homogeneous background ring (r+10..+30m) across 47 tanks | Lower = better |
| 2 | **episode_contrast_1** | ring15_far mean dB on known HIGH date minus known LOW date (2024-09-21 vs 2024-10-03) | Higher = better |
| 3 | **episode_contrast_2** | same, DESC-35 pair with >2Mb volume delta in prod series | — |
| 4 | **episode_contrast_3** | t60_2 (high-variance loading tank) std across 3 scenes | Higher = better |
| 5 | **SNR** | (contrast_1) / noise_sigma ratio | Higher = better |

Secondary:
- **step_db**: per-tank median step between consecutive scenes  
- **IQR**: interquartile range of ring15_far across 3 scenes per tank

---

## Co-registration Protocol

```python
from skimage.registration import phase_cross_correlation
# Reference: scene 1 (2024-09-21), full farm image
# Shift: phase_cross_correlation(ref, scene_n, upsample_factor=10)
# Apply: scipy.ndimage.shift(scene_n, (-dy, -dx))
# Max allowed shift: ±2 px (20m); if exceeded → flag scene as co-reg FAIL
```

---

## Output

```
diagnostics/research/A1_image/
  tool_bakeoff_metrics.csv   # tool × metric table (57 cols same schema as tank_scene_variants)
  tool_bakeoff_summary.csv   # tool | noise_sigma | contrast1 | contrast2 | contrast3 | SNR | step | IQR | selected
  coregistration_shifts.csv  # scene | tool | dx_px | dy_px | corr_peak
```

---

## Selection Rule

Selected tool = argmin(noise_sigma) subject to contrast1 >= 0.95 × max(contrast1).  
Ties broken by SNR.

---

## GHA Job

Will run as section "A1" in mega_export.yml when SentinelHub quota available.  
Inputs: `scenes=A1`, `terminal=kharg`, `orbit=DESC-35`

---

## Note on ESA SNAP

SNAP 9.0 graph XML can be called via CLI (`gpt`). If `gpt` not installed in GHA ubuntu runner,  
use docker `docker.io/mundialis/esa-snap:9.0` — available on ubuntu-latest with docker pre-installed.  
Alternatively, implement Refined Lee in pure numpy (reference: Ulaby & Long, 2014, eq. 5.38).

---

*Created: 2026-10-26 | research/2026-10-auto | SHA=ffc5996*
