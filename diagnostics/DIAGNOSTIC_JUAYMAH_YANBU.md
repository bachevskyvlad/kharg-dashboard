# Diagnostic: Juwaymah + Yanbu
**Date:** 2026-09-23  
**Author:** automated diagnostic (same framework as Ras Tanura cycle)  
**Status:** Task 0 complete from local data. Tasks 1–3 require Sentinel Hub access (blocked in CI container — run script locally or via GHA with network policy `external`).

---

## Task 0 — What exists in the system

### Registry summary

| Field | Juwaymah | Yanbu | Ras Tanura (reference) |
|---|---|---|---|
| terminal_id | `juwaymah` | `yanbu` | `ras_tanura` |
| country | SA | SA | SA |
| n_tanks | 19 | 12 | 66 |
| radius_m | 50 (all) | 60 (all) | 20×1, 35×54, 60×11 |
| h_max_m | 16 | 18 | 9–18 |
| los_azimuth | 282 | 98 | 282 |
| orbit_state | descending | **ascending** | descending (DESC 35 locked) |
| allowed_orbits set | ❌ not set | ❌ not set | ✅ DESC 35 only |
| lat range | 26.8149–26.8257 N | 23.9591–23.9713 N | 26.6376–26.6897 N |
| lon range | 49.9827–49.9923 E | 38.2441–38.2544 E | 50.1149–50.1604 E |
| bbox area | ~2.5×1.4 km | ~1.1×1.4 km | two clusters ~5.5 km apart |

### Supabase / terminal_observations

Queried from local backup (`backups/ras_tanura_pre_rebuild_2026-09-22/`):

| terminal_id | rows | distinct_dates | distinct_tanks | min_date | max_date |
|---|---|---|---|---|---|
| ras_tanura | 21,110 | 558 | 66 | 2017-03-14 | 2026-09-18 |
| **juwaymah** | **0** | **0** | **0** | — | — |
| **yanbu** | **0** | **0** | **0** | — | — |

**Juwaymah and Yanbu have zero observations in Supabase.** Neither terminal has been processed through the SAR pipeline.

### Volume timeseries

| terminal_id | rows | min_date | max_date |
|---|---|---|---|
| ras_tanura | 315 | 2017-03-14 | 2026-09-18 |
| juwaymah | 0 | — | — |
| yanbu | 0 | — | — |

---

## Task 0 verdict table

| Criterion | Juwaymah | Yanbu |
|---|---|---|
| In registry | ✅ 19 tanks | ✅ 12 tanks |
| Data in Supabase | ❌ none | ❌ none |
| Volume timeseries | ❌ none | ❌ none |
| Same orbit frame as RT | **Likely yes** — DESC, los=282, 26.8°N vs 26.6°N for RT; probable same DESC 35 frame | ❌ **Different orbit** — ASC, los=98, Red Sea side |
| allowed_orbits configured | ❌ not set (risk: multi-orbit contamination, same issue RT had) | ❌ not set |
| Blocker for immediate backfill | `allowed_orbits` not set → need orbit audit before processing | Ascending orbit, los=98 → separate calibration run needed; no existing calibration data |

---

## Task 1 — Optical coverage

**Status: NOT COMPLETED — requires Sentinel Hub / Copernicus access (403 from this container)**

Script to run: `scripts/diag_juwaymah_yanbu.py` (see below), Task 1 section.

Expected to verify:
- Juwaymah: bbox 49.970–50.003E, 26.808–26.830N — confirm 19 tanks physically visible, check floating vs fixed roof
- Yanbu: bbox 38.237–38.261E, 23.955–23.976N — confirm 12 tanks, floating roof classification

**Known from prior knowledge (registry notes + Google Earth cross-check not yet done):**
- Juwaymah Tank Farm is a confirmed crude export terminal adjacent to Ras Tanura complex (~20 km north). Large floating-roof tanks expected.
- Yanbu crude terminal: Red Sea coast. Both Yanbu 1 (crude) and YASREF (refinery) are in the area — registry covers crude tanks only.

---

## Task 2 — SAR coverage

**Status: NOT COMPLETED — Sentinel Hub catalog blocked (403)**

### Known from registry parameters:

**Juwaymah:**
- los_azimuth = 282 → same as Ras Tanura → likely covered by same DESC 35 Sentinel-1 frame
- Distance from RT south cluster: ~20 km north → both fit in one extended S1 IW swath (~250 km wide)
- If DESC 35 covers Juwaymah, backfill is essentially free: existing RT scenes already downloaded cover Juwaymah in the same image
- **This is the key question to verify with catalog query**

**Yanbu:**
- los_azimuth = 98 → ascending orbit, opposite geometry
- Red Sea coast, ~23.96°N, 38.25°E
- Nearest ascending S1 relative orbit needs to be identified (likely ASC orbit ~14 or ~87 for this longitude — to be confirmed)
- No existing calibrated scenes; full new pipeline run required
- Ascending geometry changes crescent direction: shadow falls on near side (west/northwest), not far side

### Cadence estimate (from RT baseline):
- RT DESC 35: 6-day revisit (ESA Sentinel-1 standard repeat cycle)
- Juwaymah (if same frame): same 6-day cadence with zero extra download cost
- Yanbu (ASC): 6-day revisit expected but separate orbit → separate download + processing pipeline

---

## Task 3 — Signal quality

**Status: NOT COMPLETED — no SAR data available locally for Juwaymah/Yanbu**

### Expected signal quality assessment (from tank geometry):

**Juwaymah (r=50m tanks, h_max=16m):**
- Tank volume capacity per tank ≈ π × 50² × 16 × 6.2898 bbl ≈ **790,000 bbl ≈ 0.79 Mb per tank**
- 19 tanks → theoretical max ≈ 15 Mb total
- r=50m → crescent arc length ~157m → well-resolved at 5m/px (31 px diameter)
- Expected std dB: similar to RT t40 (r=40m, std ~1.0–1.5 dB) or better given larger radius
- Signal should be stronger than RT's 35m tanks

**Yanbu (r=60m tanks, h_max=18m):**
- Same geometry as RT's t60 cluster (largest tanks in RT registry)
- Volume per tank ≈ π × 60² × 18 × 6.2898 ≈ **1.28 Mb per tank**
- 12 tanks → theoretical max ≈ 15 Mb total  
- r=60m → largest resolvable tanks; excellent crescent geometry at 5m/px
- **BUT**: ascending orbit, los=98 — different crescent orientation. Shadow mask azimuth ~east (98°), not west (282°). Pipeline crescent mask hardcoded for los=282 → **mask code needs modification for Yanbu**

---

## Task 4 — Summary verdict

| Criterion | Juwaymah | Yanbu |
|---|---|---|
| In registry | ✅ 19 tanks, r=50m | ✅ 12 tanks, r=60m |
| In Supabase | ❌ zero data | ❌ zero data |
| Tank coverage % (optical) | ⬜ not checked (needs SH) | ⬜ not checked (needs SH) |
| Floating roof % | ⬜ not checked | ⬜ not checked |
| Orbit / cadence | ⬜ catalog blocked; likely DESC 35, 6d | ⬜ catalog blocked; ASC ~6d |
| Same frame as RT (backfill cheap?) | **Likely YES** — needs catalog confirm | ❌ NO — separate ASC orbit |
| SAR data available | ❌ none downloaded | ❌ none downloaded |
| Crescent mask compatible | ✅ los=282 same as RT | ❌ los=98, mask code needs change |
| allowed_orbits set | ❌ risk of multi-orbit contamination | ❌ same risk |
| Pipeline blockers | 1. Set `allowed_orbits` (check DESC 35 covers bbox); 2. Download + process historical scenes | 1. Set `allowed_orbits` (ASC orbit TBD); 2. Modify crescent mask for los=98; 3. Separate calibration run; 4. No RT reference to cross-validate |
| Estimated effort to first signal | **Low** if DESC 35 confirmed — reuse existing RT pipeline, extend bbox | **Medium-high** — new orbit, new mask direction, new calibration |
| Recommended priority | **HIGH** — likely free backfill, same infrastructure | **MEDIUM** — separate development track |

### Recommendation

**Juwaymah first.** If DESC 35 covers the Juwaymah bbox (high probability given geographic proximity and same los_azimuth=282), then every RT scene already downloaded also contains Juwaymah data. The backfill is effectively free — only registry `allowed_orbits` needs setting and the bbox needs adding to the fetch parameters. Expected: same signal quality as RT 50m tanks, similar 6-day cadence.

**Yanbu second, separate track.** Ascending orbit with los=98 requires: (1) orbit identification, (2) crescent mask code modification for 98° azimuth, (3) fresh calibration with no existing reference. Not a blocker but a separate development sprint.

---

## Blockers requiring Sentinel Hub access

To complete this diagnostic fully, run `scripts/diag_juwaymah_yanbu.py` in an environment with outbound HTTPS to `services.sentinel-hub.com`. The script performs:

1. Catalog search (2024-2026) for all three terminals → orbit table
2. Sentinel-2 True Color crop download → optical tank count + floating/fixed classification
3. S1 scene download (6–8 scenes per terminal) → per-tank dB stats
4. Crescent profile angular check → mask geometry validation
5. Dynamic range assessment

**Note:** This container's network policy blocks `services.sentinel-hub.com:443` and `yoekhkczwdeyauwarsyy.supabase.co:443`. Both confirmed 403 Forbidden via proxy.
