# Whitelist Gate Resolution

Generated: 2026-09-29  
Branch: `claude/oilradar-dashboard-iran-zchdva`

---

## Task 1 & 2 — Decomposition metrics and verdict

**Status: pending `stage5_decompose` run** (workflow `saudi_stage5_decompose`)

Results to be inserted here after run completes. See `diagnostics/stage5/decompose_report.txt`.

Frozen verdict criteria (not adjustable after run):

| Condition | Verdict |
|---|---|
| corr(Δ_excl,Δ_WL) < 0.2 **AND** smoothed corr(Δ_old,Δ_WL) ≥ 0.9 | SHAPE_PRESERVED — deploy open |
| corr(Δ_excl,Δ_WL) > 0.4 | SIGNAL_LOSS — deploy cancelled |
| 0.2–0.4 or smoothed < 0.9 | HUMAN_REVIEW |

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

**Pending Task 1/2 results from stage5_decompose run.**

After run completes:
1. Insert decomposition metrics table above
2. If SHAPE_PRESERVED → deploy open: merge PR, enable `enabled` flags in registry, run compute for RT/YB
3. If SIGNAL_LOSS → deploy cancelled; top-5 excluded tank analysis required before re-evaluation
4. If HUMAN_REVIEW → manual review of decompose plots before decision
