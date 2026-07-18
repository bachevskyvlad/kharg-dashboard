# Kharg Per-Tank Interpretation Layer — v2

Data: 7,708 obs · 47 tanks · 164 passes · 2022-01-11→2026-07-14 (4.5 yrs)
Weekly grid: 236 weeks · terminal mean 18.5 Mn bbl (min 15.4, max 26.2)

## 1. Multi-year trend
| year | mean | min | max |
|---|---|---|---|
| 2022 | 18.7 | 15.7 | 23.1 |
| 2023 | 18.1 | 15.5 | 21.3 |
| 2024 | 18.2 | 16.1 | 21.2 |
| 2025 | 17.5 | 15.4 | 19.8 |
| 2026 | 21.4 | 17.9 | 26.2 |

→ Terminal average has **risen 2.7 Mn bbl** from 2022 to 2026 (18.7→21.4). Peak year: 2026 (21.4). Structural fill vs capacity (~33.2): 64% now vs 56% at start.

## 2. Seasonality (month-of-year)
| month | avg level (Mn bbl) | avg net change |
|---|---|---|
| Jan | 18.2 | +0.93 |
| Feb | 18.2 | +0.13 |
| Mar | 18.5 | +0.37 |
| Apr | 19.1 | +0.50 |
| May | 21.0 | +1.77 |
| Jun | 19.1 | -1.58 |
| Jul | 18.2 | -1.07 |
| Aug | 18.2 | +0.39 |
| Sep | 17.8 | -0.44 |
| Oct | 18.0 | +0.18 |
| Nov | 17.5 | -0.45 |
| Dec | 17.5 | -0.02 |

→ **Build months** (storage tends to rise): Jan, Feb, Mar, Apr, May, Aug, Oct
→ **Draw months** (storage tends to fall — export-heavy): Jun, Jul, Sep, Nov
→ Fullest month on average: **May** (21.0); emptiest: **Nov** (17.5). Seasonal swing ≈ 3.5 Mn bbl.

## 3. Cycle length (build→draw rhythm)
- Autocorrelation of detrended weekly level peaks at lag **2 weeks** (≈14 days), r=0.13 → dominant fill↔draw cycle.
- Peak-to-peak spacing averages **9.6 weeks** (67 days), 24 peaks / 24 troughs over 4.5 yrs.

## 4. Dependencies — who leads whom (weekly cross-correlation)
| pair | best lag (wk) | corr | reading |
|---|---|---|---|
| north↔south | +7 | +0.17 | north leads south by 7w |
| north↔central | +0 | +0.38 | move together |
| south↔central | +2 | +0.18 | south leads central by 2w |
| loading↔reserve | +2 | +0.21 | loading leads reserve by 2w |
| loading↔swing | +0 | +0.34 | move together |

## 5. Which tanks drive terminal moves (variance-share of weekly change)
- Top movers explain the bulk of terminal variance. Leaders:
  - **t60_2** (loading, south): 10% of terminal weekly variance
  - **t55_15** (loading, north): 8% of terminal weekly variance
  - **t55_8** (loading, north): 8% of terminal weekly variance
  - **t55_17** (loading, north): 8% of terminal weekly variance
  - **t55_7** (swing, north): 7% of terminal weekly variance
  - **t60_4** (loading, south): 4% of terminal weekly variance
  - **t55_6** (loading, north): 4% of terminal weekly variance
  - **t55_14** (swing, north): 4% of terminal weekly variance
- Top-8 tanks account for **52%** of all terminal movement → the terminal is driven by a small active core, not all 47 evenly.

## 5B. Geographic clusters — north / central / south
| cluster | tanks | avg lvl | share | σ | drawn | var% | peak-draw mo | draw months | 2025→26 |
|---|---|---|---|---|---|---|---|---|---|
| north | 16 {'t55': 16} | 8.7 | 47% | 1.09 | 55.9 | 54% | Jul | Feb;Jun;Jul;Aug;Sep;Nov;Dec | +2.1 |
| central | 15 {'t40': 12, 't55': 2, 't25': 1} | 4.5 | 24% | 0.57 | 32.7 | 25% | Jun | Mar;Jun;Jul;Sep | +0.7 |
| south | 16 {'t25': 11, 't60': 5} | 5.3 | 29% | 0.74 | 37.0 | 21% | Jun | Feb;Jun;Jul;Oct;Nov;Dec | +1.1 |

→ Most active (drives most terminal movement): **north** (54% of variance).
→ 2026 build carried mostly by: **north** cluster.

## 6. Reserve behaviour (is the strategic hold ever tapped?)
- **t55_31**: mean 81% fill, floor 26% on 2022-04-17. Drawn during stress.
- **t60_1**: mean 80% fill, floor 25% on 2023-12-08. Drawn during stress.

## 6B. Build/draw dispersion by year (volatility of weekly change)
| year | weekly Δ std | net change |
|---|---|---|
| 2022 | 0.98 | -1.1 |
| 2023 | 0.94 | +1.3 |
| 2024 | 0.66 | +0.4 |
| 2025 | 0.83 | +2.4 |
| 2026 | 1.61 | -0.5 |

→ Dispersion rose 0.98→1.61 Mn bbl. Most volatile year: 2026 (1.61).

## 5C. Tank size classes (which class is more active)
| class | tanks | total vol | vol/tank | avg σ fill | var-share |
|---|---|---|---|---|---|
| t60 | 5 | 4.3 | 0.87 | 25.1 | 17% |
| t55 | 18 | 9.8 | 0.54 | 24.2 | 60% |
| t40 | 12 | 3.4 | 0.28 | 23.9 | 19% |
| t25 | 12 | 1.0 | 0.08 | 25.6 | 4% |

→ Top-8 mover tanks hold on average **4.8 Mn bbl** (26% of terminal volume) — they carry 52% of the movement on that share.

## Peak draw event
- Largest single-week draw: **-3.93 Mn bbl**, week of 2026-02-22
- Largest 6–14d pulse: **−5.06 Mn bbl**, 2026-06-21 → 2026-07-05 (14d)

## 7. Signal dictionary (assertions only — weak patterns dropped)

| # | Pattern | Interpretation | Confirmed on |
|---|---|---|---|
| 1 | Terminal −≥2 Mn bbl within 6–14d | Export pulse (VLCC loading) | 21 events, 4.5y |
| 2 | Draw-season window (Jun, Jul, Sep, Nov) | Seasonally export-heavy | 5 yrs of monthly data |
| 3 | Fill↔draw cycle ≈ 14 days | Base operating rhythm | ACF r=0.13 |
| 4 | Core movers t60_2, t55_15, t55_8 shift | Terminal about to move | top-8 = 52% of variance |
| 5 | Idle t55_10, t55_5 <20% flat | Offline / out of service | 2/2, full period |
| 6 | Reserve t55_31, t60_1 near-full | Strategic hold; a draw = real stress | see §6 |
| 7 | Export pulses cluster in **Jun** | Seasonal loading peak | 6 pulses that month |

_Generated by analyze_patterns.py v2_
DONE
