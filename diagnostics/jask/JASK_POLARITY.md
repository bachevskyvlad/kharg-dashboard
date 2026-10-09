# JASK POLARITY ASSESSMENT

**Date**: 2026-10-09
**Script**: `scripts/jask_polarity.py`

---

## Verdict: POLARITY_ASSUMED_DIRECT (unverified)

Polarity assumed direct (lower dB = more oil) per regional default.
The 4 reference farms on the same Persian Gulf DESC orbit (rel=93, los=282°) —
Kharg, Lavan, Siri, Halul — all show confirmed direct polarity. No inversions
observed across these terminals. **This assumption is unverified for Jask
specifically.** Recheck at first dated VLCC lifting.

---

## Anchor Test Results

| Anchor | Event | Expected Δdb | Observed Δdb | Status |
|---|---|---|---|---|
| war_2026 | Iran disruption | — | −0.323 dB | inconclusive by construction |
| jask_resume | Post-disruption resume | — | +1.675 dB | inconclusive by construction |
| iran_sanctions_2024h2 | Sanctions tightening | — | SKIP (n=2/5) | insufficient data |

**Score: 0 anchors confirmed — but failure is by construction, not by signal.**

---

## Why Anchors Are Inconclusive by Construction

The war_2026 anchor: an Iranian maritime disruption does not predict whether Jask
specifically would drawdown or fill. Jask is a bypass terminal — under conflict
conditions it could accelerate loading (drawdown) OR receive displaced crude from
other terminals (fill). Direction of db_mean change is ambiguous by design.

The jask_resume anchor: resumption of operations after a disruption does not resolve
fill-first vs draw-first. A terminal restarting could do either depending on
inventory state at the moment of disruption. Direction again ambiguous.

The sanctions anchor: the 2-of-5 minimum was not met (n=2), making this anchor
statistically undefined for the test.

Conclusion: the anchor set was placebo-equivalent for this terminal class. Absence
of confirmation is not evidence of inverted polarity. Polarity assumed direct on
geometric grounds (same DESC orbit, same SAR physics, 4 confirming terminals).

---

## Passport Flag

```
polarity: assumed_direct
polarity_verified: false
polarity_note: "assumed per 4-terminal regional default (Kharg/Lavan/Siri/Halul DESC);
                anchors inconclusive by construction for bypass terminal;
                recheck at first dated VLCC lifting"
```

---

## Export Anchors (quarantined)

UANI/PortWatch Jask export anchors are quarantined per security rule:
"Экспорт-якоря (UANI/PortWatch Jask) в коде не открывать — корреляция = сторона пользователя"

Correlation against export data is user-side only. Pipeline uses only geopolitical anchors.
