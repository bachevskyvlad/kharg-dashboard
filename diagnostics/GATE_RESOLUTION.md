# Whitelist Gate Resolution

Generated: 2026-09-29  
Branch: `claude/oilradar-dashboard-iran-zchdva`

---

## Task 1 & 2 — Decomposition metrics and verdict

**Status: Run 3 complete** (workflow `saudi_stage5_decompose`, 2026-09-29)

All three sanity checks PASS for both terminals.

### Sanity checks

| Terminal | #1 Additive | #2 Reproduce stage4 | #3 Var decomp |
|---|---|---|---|
| Ras Tanura | PASS (4.44e-15 Mb) | PASS (got 0.7885 vs ref 0.7866) | PASS |
| Yanbu | PASS (2.22e-15 Mb) | PASS (got 0.7758 vs ref 0.7764) | PASS |

### Decomposition metrics

| Terminal | n_WL | n_excl | n_dates | corr(Δ_old,Δ_WL) | corr(Δ_excl,Δ_WL) | smoothed_corr(Δ_old,Δ_WL) |
|---|---|---|---|---|---|---|
| Ras Tanura | 45 | 21 | 316 | 0.7885 | **0.0929** | 0.6479 |
| Yanbu | 8 | 4 | 332 | 0.7758 | **−0.0900** | 0.5736 |

Top-5 excluded tanks by \|corr(Δ_tank,Δ_WL)\|:

**RT:** rt_58 (+0.1424), rt_53 (+0.1422), rt_48 (+0.1140), rt_43 (+0.1004), rt_55 (+0.0624)

**YB:** yb_1 (−0.0895), yb_10 (−0.0708), yb_6 (−0.0313), yb_4 (−0.0063)

### Frozen verdict criteria

| Condition | Verdict |
|---|---|
| corr(Δ_excl,Δ_WL) < 0.2 **AND** smoothed corr(Δ_old,Δ_WL) ≥ 0.9 | SHAPE_PRESERVED — deploy open |
| corr(Δ_excl,Δ_WL) > 0.4 | SIGNAL_LOSS — deploy cancelled |
| 0.2–0.4 or smoothed < 0.9 | HUMAN_REVIEW |

Both terminals: corr(Δ_excl,Δ_WL) < 0.2 ✓ but smoothed_corr < 0.9 ✗

**VERDICT RT: ___ (pending Kharg control)**

**VERDICT YB: ___ (pending Kharg control)**

---

## Task 3 — SAR crop verdicts

Scene dates: YB 2026-09-22, JW 2026-09-24

### Yanbu (YB) — 8 whitelist tanks

| Tank | Verdict | Reason |
|---|---|---|
| yb_0 | ON_WALL | Clear ring on circular structure; red crescent lands on far-side tank wall |
| yb_2 | ON_WALL | Clean concentric ring; crescent on far wall, no ambiguity |
| yb_3 | ON_WALL | Ring on circular structure; crescent on far wall in expected shadow zone |
| yb_5 | ON_WALL | Ring on tank center; crescent on wall; multiple adjacent tanks confirm cluster |
| yb_7 | ON_WALL | Ring structure centered; crescent on right-side wall, clean shadow |
| yb_8 | ON_WALL | Circular ring with crescent on far-right wall; typical half-full pattern |
| yb_9 | ON_WALL | Ring present; crescent spans near-to-far wall arc; isolated from background |
| yb_11 | ON_WALL | Smaller ring, crescent on right wall; pattern consistent with partial fill |

YB OFF_TANK: **0**  YB UNCLEAR: **0**

### Juwaymah (JW) — 4 sample tanks

| Tank | Verdict | Reason |
|---|---|---|
| jw_9  | ON_WALL | Ring structure visible; crescent on wall; background cluttered but target unambiguous |
| jw_3  | ON_WALL | Clean circular structure; crescent on left wall; well-isolated from surrounding clutter |
| jw_11 | ON_WALL | Ring and crescent on wall; cluttered surroundings but target structure clear |
| jw_16 | ON_WALL | Well-isolated tank; crescent cleanly on left wall; no ambiguity |

JW OFF_TANK: **0**  JW UNCLEAR: **0**

**UNCLEAR ≤ 3 → no alternative criterion required.**

### Whitelist impact of crop QA

No OFF_TANK verdicts → whitelist unchanged:

| Terminal | All tanks | Whitelist | % cap |
|---|---|---|---|
| Ras Tanura (RT) | 66 | 45 | 63.3% |
| Yanbu (YB) | 12 | 8 | 66.7% |
| Juwaymah (JW) | 19 | 9 | 47.4% |

Venue verdicts unchanged: RT TERMINAL_SERIES, YB TERMINAL_SERIES, JW TANK_LEVEL_OR_DROP.

---

## Task 4 — Final deploy status

**Pending Kharg control (workflow `kharg_control`, run in progress).**

Kharg smoothed_corr(Δ_all47, Δ_top30-by-step) = ___ (pending)

Decision rule:
- Kharg ~0.6–0.7 → threshold was unrealistic → RT/YB = SHAPE_PRESERVED → go deploy
- Kharg ~0.9 → visual review of decompose plots before decision

After user go:
1. Merge PR
2. Enable `enabled` flags in registry for RT/YB tanks
3. Run compute for RT/YB (JW not published per agreement)
