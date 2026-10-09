# Jask Polarity Check

Run: 2026-10-09 14:06 UTC | obs: 2180 | dates: 109

## Pre-stated anchors (before any calculation)

| # | Anchor | Date | Window | Expected |  Rationale |
|---|---|---|---|---|---|
| 1 | Iran-Israel escalation | 2026-04-01 | 30d | db UP (+) | Aerial strikes on Iranian infrastructure Apr 2026; operations disrupted, export pressure; tanks draw down → dB rises |
| 2 | NIOC Jask pipeline resumption | 2025-06-15 | 45d | db DOWN (−) | New pipeline brought online; first fill ahead of inaugural export; tanks fill → dB falls |
| 3 | Iran sanctions tightening H2 2024 | 2024-07-01 | 60d | db DOWN (−) | Secondary sanctions / tanker fleet restrictions reduce exports; oil accumulates → dB falls |

## Results

| Anchor | pre_db | post_db | Δdb | Expected | Match |
|---|---|---|---|---|---|
| Iran-Israel escalation | 6.80 | 6.48 | -0.323 | UP | ✗ |
| NIOC Jask pipeline resumption | 5.29 | 6.97 | +1.675 | DOWN | ✗ |
| Iran sanctions tightening H2 2024 | — | — | — | DOWN | SKIP (n=2/5) |

## Verdict: **POLARITY_WEAK** (0/2 anchors match)

> NOTE: Export anchors (UANI/PortWatch Jask correlations) are in quarantine — not computed here.