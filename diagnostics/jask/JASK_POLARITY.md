# JASK POLARITY ASSESSMENT

**Date**: 2026-10-09
**Script**: `scripts/jask_polarity.py`

---

## Verdict: POLARITY_ASSUMED_DIRECT (unverified)

Polarity cannot be confirmed from Jask history alone. Assumed direct (lower dB = more oil)
based on regional reference terminals (Kharg, Lavan) sharing the same Persian Gulf DESC orbit
(rel=93, los=282°). **This assumption is unverified.** Recheck at first confirmed VLCC lifting.

---

## Anchor Test Results

| Anchor | Event | Expected Δdb | Observed Δdb | Match |
|---|---|---|---|---|
| war_2026 | Iran disruption | negative (drawdown) | −0.323 dB | ✗ (too small) |
| jask_resume | Post-disruption resume | positive (fill) | +1.675 dB | ✗ (direction ok but amplitude outside threshold) |
| iran_sanctions_2024h2 | Sanctions tightening | negative | SKIP (n=2/5) | — |

**Score: 0/2 anchors confirmed.** POLARITY_CONFIRMED requires ≥2/3.

---

## Why ASSUMED_DIRECT (not POLARITY_WEAK)

POLARITY_WEAK is the algorithmic result. Human override to POLARITY_ASSUMED_DIRECT because:

1. Physical basis is established: DESC rel=93 los=282° is the same geometry as Kharg and Lavan,
   both confirmed direct. SAR crescent physics does not change between terminals on the same orbit.
2. Anchor failures are amplitude failures, not direction failures. war_2026 Δ=−0.323 is in the
   correct direction (drawdown) but too small to cross the match threshold. jask_resume Δ=+1.675
   is also directionally correct (fill) but amplitude outside the corridor.
3. Jask history is short (~2 years) with sparse geopolitical anchors — the 3-anchor test is
   structurally underpowered at this terminal.

---

## Passport Flag

```
polarity: assumed_direct
polarity_verified: false
polarity_recheck: "first confirmed VLCC lifting at Jask"
```

---

## Export Anchors (quarantined)

UANI/PortWatch Jask export anchors are quarantined per security rule:
"Экспорт-якоря (UANI/PortWatch Jask) в коде не открывать — корреляция = сторона пользователя"

Correlation against export data is user-side only. Pipeline uses only geopolitical anchors.
