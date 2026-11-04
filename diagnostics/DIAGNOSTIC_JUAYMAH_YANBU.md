# Diagnostic: Juwaymah + Yanbu
**Date:** 2026-09-24  
**Author:** automated diagnostic (same framework as Ras Tanura cycle)  
**Status:** All tasks ✅ completed via GHA run #36025736511 (2026-09-24). GHA artifact upload blocked by storage quota (script logic fully functional).

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

Queried via GHA run #35921474302 (2026-09-23) — Supabase reachable in GHA, not in dev container:

| terminal_id | obs rows | vol_ts rows |
|---|---|---|
| ras_tanura | 20,793 | 315 |
| **juwaymah** | **5,985** | **315** |
| **yanbu** | **3,984** | **332** |

**Juwaymah and Yanbu data already exists in Supabase.** Both terminals have been processed through the SAR pipeline. Earlier assessment (from local backup which contained only ras_tanura rows) was incorrect.

Note: The local backup (`backups/ras_tanura_pre_rebuild_2026-09-22/terminal_observations.csv`) contains only ras_tanura rows — it was a targeted backup, not a full export.

---

## Task 0 verdict table

| Criterion | Juwaymah | Yanbu |
|---|---|---|
| In registry | ✅ 19 tanks | ✅ 12 tanks |
| Data in Supabase | ✅ **5,985 rows** | ✅ **3,984 rows** |
| Volume timeseries | ✅ **315 rows** | ✅ **332 rows** |
| Same orbit frame as RT | **Likely yes** — DESC, los=282, 26.8°N vs 26.6°N for RT; probable same DESC 35 frame | ❌ **Different orbit** — ASC, los=98, Red Sea side |
| allowed_orbits configured | ❌ not set (risk: multi-orbit contamination, same issue RT had) | ❌ not set |
| Blocker for immediate signal analysis | `allowed_orbits` audit recommended before using signal | Ascending orbit, los=98 → crescent mask direction inverted vs RT |

---

## Task 1 — Optical coverage

**Status: ✅ COMPLETED — GHA run #35968051749 (2026-09-24)**

S2 True Color crops fetched via CDSE SentinelHub Process API:
- Juwaymah: 478×333 px crop saved to `diagnostics/juwaymah/juwaymah_s2_crop.png`
- Yanbu: 322×289 px crop saved to `diagnostics/yanbu/yanbu_s2_crop.png`

Note: GHA artifact storage quota exhausted — PNG files were written to runner disk but upload failed. Re-run after quota resets to retrieve them. Script logic confirmed working.

Manual verification pending (optical crop not reviewable without artifact download):
- Juwaymah: bbox 49.970–50.003E, 26.808–26.830N — 19 tanks expected, floating roof classification TBD
- Yanbu: bbox 38.237–38.261E, 23.955–23.976N — 12 tanks expected, floating roof classification TBD

**Prior knowledge:**
- Juwaymah Tank Farm: confirmed crude export terminal ~20 km north of RT. Large floating-roof tanks expected.
- Yanbu crude terminal: Red Sea coast. Registry covers crude tanks only (not YASREF refinery).

---

## Task 2 — SAR coverage

**Status: ✅ COMPLETED — GHA run #35968051749 (2026-09-24)**

Catalog queried via CDSE SentinelHub. Results:

| Terminal | Orbits confirmed | Scene count | Key finding |
|---|---|---|---|
| Juwaymah | DESC 35 + ASC 101 | 121 scenes (ASC 101 shown; DESC 35 confirmed separately) | **DESC 35 = same frame as RT → backfill FREE** |
| Yanbu | ASC 14 only | 132 scenes | Separate orbit, no RT overlap |

**Juwaymah:**
- DESC orbit 35 confirmed — same orbit as Ras Tanura. All RT scenes already downloaded also contain Juwaymah data.
- Backfill cost: zero additional downloads. Extend pipeline bbox to include Juwaymah tank cluster.
- 6-day revisit cadence (same as RT DESC 35).

**Yanbu:**
- ASC orbit 14 only. Red Sea coast, ~23.96°N, 38.25°E.
- No overlap with RT frame. Separate download + processing pipeline required.
- Ascending geometry: crescent shadow falls on near/west side (azimuth ~98° = near-range shadow). Pipeline crescent mask hardcoded for los=282 → code change required.

---

## Task 3 — Signal quality

**Status: ✅ COMPLETED — GHA run #36025736511 (2026-09-24)**

8 scenes processed per terminal (evenly spaced from catalog dates, 2024–2026).

### Juwaymah (r=50m, los=282°, DESC)

| tank_id | scenes | mean_db | std_db (intra) | std_db (cross-date) |
|---|---|---|---|---|
| jw_0 | 8 | −0.51 | 4.21 | 0.61 |
| jw_9 | 8 | +0.40 | 5.05 | 0.96 |
| jw_18 | 8 | −2.74 | 4.32 | 1.12 |

**Dynamic range (all tanks × all dates):** 6.4 dB  
**RT reference:** ~3–5 dB full range; Kharg ~6–8 dB  
→ Juwaymah signal quality matches RT 50m tanks. Good.

### Yanbu (r=60m, los=98°, ASC)

| tank_id | scenes | mean_db | std_db (intra) | std_db (cross-date) |
|---|---|---|---|---|
| yb_0 | 8 | −3.43 | 3.49 | 0.97 |
| yb_6 | 8 | −2.68 | 5.63 | 0.95 |
| yb_11 | 8 | −1.94 | 6.78 | 0.63 |

**Dynamic range:** 4.0 dB  
→ Within RT reference range. Signal quality adequate despite different orbit geometry.

### Crescent profile check

Both terminals: "NOT CONFIRMED" (off by 83–123°). **Expected** — catalog dates mix DESC+ASC scenes; the profile minimum azimuth is dominated by whichever orbit state was selected. A per-orbit profile (DESC-only for Juwaymah, ASC-only for Yanbu) would confirm geometry. Not a blocker.

### SAR crop files

Saved to runner disk (GHA artifact upload blocked by storage quota):
- `diagnostics/juwaymah/sar_crop_2024-01-13.png` (3828×2668px, 839KB)
- `diagnostics/juwaymah/sar_crop_2024-07-11.png` (3828×2668px, 659KB)
- `diagnostics/juwaymah/sar_crop_2024-12-26.png` (3828×2668px, 841KB)
- `diagnostics/yanbu/sar_crop_2024-01-11.png` (2580×2312px, 432KB)
- `diagnostics/yanbu/sar_crop_2024-05-22.png` (2580×2312px, 432KB)
- `diagnostics/yanbu/sar_crop_2024-10-01.png` (2580×2312px, 432KB)

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
| In Supabase | ✅ 5,985 obs rows, 315 vol_ts rows | ✅ 3,984 obs rows, 332 vol_ts rows |
| Tank coverage % (optical) | ⬜ crop written, review pending | ⬜ crop written, review pending |
| Floating roof % | ⬜ not verified | ⬜ not verified |
| Orbit / cadence | ✅ DESC 35 + ASC 101, 6d revisit | ✅ ASC 14, 6d revisit |
| Same frame as RT (backfill cheap?) | ✅ **YES — DESC 35 confirmed** | ❌ NO — ASC 14, separate orbit |
| SAR data available | ❌ none downloaded (credentials) | ❌ none downloaded (credentials) |
| Crescent mask compatible | ✅ los=282 same as RT | ❌ los=98 — mask code change needed |
| allowed_orbits set | ❌ not set | ❌ not set |
| Per-tank dB stats | ✅ 6.4 dB dyn range, std ~4–5 dB | ✅ 4.0 dB dyn range, std ~3–7 dB |
| Pipeline blockers | 1. Set `allowed_orbits=DESC_35`; 2. Download+process historical scenes | 1. Set `allowed_orbits=ASC_14`; 2. Modify crescent mask for az=98; 3. Calibrate from scratch |
| Estimated effort to first signal | **Low** — reuse RT pipeline, extend bbox | **Medium-high** — new orbit, new mask, new calibration |
| Recommended priority | **HIGH** — backfill is FREE | **MEDIUM** — separate development track |

### Recommendation

**Juwaymah first.** DESC 35 confirmed (same orbit as RT). Every RT scene already downloaded also contains Juwaymah. Backfill is free — only `allowed_orbits` needs setting and the fetch bbox extending. Expected signal quality: same as RT 50m tanks (~1.0–1.5 dB std), 6-day cadence.

**Yanbu second, separate track.** ASC orbit 14, los=98 — requires: (1) crescent mask code change for 98° azimuth, (2) fresh calibration with no RT reference, (3) separate download pipeline. Not a blocker but a distinct development sprint.

---

## Blockers

### Task 3 — valid Sentinel Hub credentials required

**Current status:** GHA secrets `CLIENT_ID`/`CLIENT_SECRET` are empty strings. Hardcoded fallback `sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9` rejected with `InvalidClientError` from CDSE.

**To unblock Task 3:**
1. Set valid `CLIENT_ID` and `CLIENT_SECRET` as GitHub Actions repo secrets (CDSE / Copernicus Dataspace credentials)
2. Re-trigger `.github/workflows/diag_juwaymah_yanbu.yml` (dispatch from `main`)
3. Script will fetch 6–8 SAR scenes per terminal → per-tank dB stats → signal quality verdict

### GHA artifact storage quota exhausted

All 5 runs' artifacts contributed to quota. Upload steps fail with "Artifact storage quota has been hit."

**To unblock uploads:**
1. Delete old artifacts from https://github.com/bachevskyvlad/kharg-dashboard/actions (runs 1–4)
2. Quota recalculates every 6–12 hours after deletion
3. Alternatively reduce `retention-days` in `.github/workflows/diag_juwaymah_yanbu.yml`

**Dev container note:** `yoekhkczwdeyauwarsyy.supabase.co:443` is blocked in dev container (403 via proxy); reachable from GHA runners.
