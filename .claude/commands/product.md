# OilRadar Product Skill

You are the product engineer for OilRadar — a satellite intelligence platform
tracking crude oil storage at Kharg Island, Iran (47 floating-roof tanks,
~90% of Iranian crude exports). Read this before any product or data task.

---

## Core Data Architecture

### Source Data
- **Satellite**: Sentinel-1 C-band SAR (ESA Copernicus), descending orbit, ~6-day revisit
- **Raw signal**: VV backscatter in dB per tank per pass → stored in `raw_observations`
- **Physics**: Lower dB = more oil (liquid surface dampens radar return)
- **Method**: Inverse crescent radiometry — shadow on the far side of each floating-roof tank

### Tables in Supabase
| Table | Content |
|---|---|
| `raw_observations` | One row per (date, tank_id). Columns: id, date, tank_id, radius_m, diameter, h_max, db_mean, created_at |
| `volume_timeseries` | Aggregated volume: date, vol_mn_bbl, aggregation (daily/weekly), smoothed (bool) |
| `kharg_daily` | dbt mart: date, vol_mn_bbl, vol_5d_ma, day_change, signal, pct_change_30d |
| `kharg_signals` | Streak analysis: consecutive BUILD/DRAWDOWN days with totals |

### Data Files (served to frontend)
| File | Content |
|---|---|
| `data/data.json` | Weekly smoothed + recent daily fill-in: {dates, values} |
| `data/data_daily.json` | Daily granularity: {dates, values} |
| `data/fragility.json` | LEGACY — multi-country Gulf data, no longer primary |

---

## Volume Calculation

```python
dB_min = df['db_mean'].quantile(0.05)   # global 5th percentile
dB_max = df['db_mean'].quantile(0.95)   # global 95th percentile
TANK_HEIGHT_MIN = 2  # meters, minimum fill height assumed
BARREL_PER_M3 = 6.2898

fill_ratio = (dB_max - db_mean_clipped) / (dB_max - dB_min)
h_current  = TANK_HEIGHT_MIN + fill_ratio * (h_max - TANK_HEIGHT_MIN)
vol_bbl    = π × radius_m² × h_current × BARREL_PER_M3
```

Total storage = sum across all 47 tanks on a given date.
Max theoretical capacity ≈ 31.78 Mn bbl.

---

## Tank Inventory

| Type | Count | Radius | h_max | Notes |
|---|---|---|---|---|
| t60 | 5 | 60m | 18m | Largest tanks |
| t55 | 18 | 55m | 17m | Main cluster incl. 1 new (t55_31) |
| t40 | 12 | 40m | 14m | Mid-size, includes most active loading tanks |
| t25 | 12 | 25m | 12m | Smallest, some persistently empty |

Known structural outliers:
- `t55_5`, `t55_10` — mean dB >14 across 18 months → likely idle/offline
- `t55_31`, `t60_1` — mean dB <1 → chronically full, possible strategic reserve
- `t60_2`, `t55_17` — highest std (6+ dB) → primary active loading tanks

---

## Series Health Standard (§3)

Reference health corridors computed from Kharg + Yanbu. See `diagnostics/SERIES_HEALTH_STANDARD.md` for current numbers.

| Metric | Description | Corridor |
|---|---|---|
| cv_level | CV of per-date mean_db (σ/μ) | [lo, hi] from standard |
| vel_std | Std of pass-to-pass Δmean_db (dB/pass) | [lo, hi] |
| delta_p50 | Median absolute Δmean_db (dB) | [lo, hi] |
| delta_p90 | 90th pct absolute Δmean_db (dB) | [lo, hi] |
| delta_p99 | 99th pct absolute Δmean_db (dB) | [lo, hi] |
| dynamic_range | p95 − p5 of mean_db (dB) | [lo, hi] |
| cycles_per_year | Build-draw cycles per year | [lo, hi] |
| cycle_depth_db | Median cycle amplitude (dB) | [lo, hi] |
| max_flat_passes | Longest flat run (passes) | [lo, hi] |
| per_tank_step | Median of per-tank step_db | [lo, hi] |
| live_fraction | Fraction of tanks with step≥0.5 | [lo, hi] |

**Verdict rules**:
- **DEAD**: ≥2 of {delta_p90, live_fraction, dynamic_range} below corridor lo
- **WILD**: any of {delta_p99, vel_std} above corridor hi
- **PASS**: all metrics within corridors

Corridors = union(Kharg, Yanbu) × [0.5, 2.0]. **Fixed at generation time — do not adjust post-hoc.**

Script: `scripts/series_health.py --terminal X` → PASS / FLAG (exit 0 / 1)
Standard: `scripts/series_health_standard.py` → `diagnostics/SERIES_HEALTH_STANDARD.md`

---

## Intelligence Metrics

All 6 metrics are canonical. Use exact definitions below.

### 1. Fill Rate
```
fill_rate = (vol[t] - vol[t - window_days]) / window_days
```
- Window: 7 days (default), configurable
- Units: Mn bbl/day
- Positive = accumulation (bearish for oil price)
- Negative = drawdown/loading (bullish)

### 2. Tank Activity Index
```
TAI(tank, date) = std(db_mean) over rolling 30-day window
```
- Requires raw_observations
- High TAI = tank is actively cycling (loading/unloading)
- Used to identify which tanks are operationally active vs idle

### 3. Export Pulse Score
```
Triggered when: (vol[t₀] - vol[t₁]) >= 2.0 Mn bbl
Where: 6 <= (t₁ - t₀ in days) <= 12
```
- Each triggered event = 1 Export Pulse
- Interpretation: probable VLCC tanker loading event
- Lead time vs AIS confirmation: 7–14 days

### 4. Structural Idle Ratio
```
SIR = count(tanks where 90d_mean_db > 12) / total_tanks × 100
```
- Current static value: 4.3% (2 of 47 tanks)
- Recompute quarterly from raw_observations

### 5. Storage Pressure Index
```
SPI = vol_current / max_capacity × 100
max_capacity = 31.78 Mn bbl
```
- Thresholds:
  - SPI < 70%: Normal
  - 70% ≤ SPI < 85%: Elevated
  - SPI ≥ 85%: Critical (historical precedent: forced price concessions within 4–8 weeks)

### 6. Volatility Spike Alert
```
rolling_std_30d = std(vol) over 30-day rolling window
threshold = mean(rolling_std_30d) + 1.5 × std(rolling_std_30d)
ALERT when rolling_std_30d[date] > threshold
```
- Proxy metric (ideally computed from cross-tank σ in raw_observations)
- Signals abnormal simultaneous activity across multiple tanks

---

## Data Quality Rules

1. **Deduplication**: raw_observations has UNIQUE (date, tank_id). Pipeline deduplicates
   Sentinel-1 scenes within 12h of each other (same physical pass, straddling UTC midnight).
2. **No gap filling**: never interpolate missing SAR dates. Missing data is missing.
3. **Outlier handling**: clip db_mean to [5th, 95th] global percentile before fill_ratio calc.
4. **Weekly smoothing**: SMA-13 centered on weekly aggregates. Predictive edge: 10 weeks.
5. **Tank exclusion rules** (§5.1):
   - **Min-radius rule**: r_fit_m < 20 m → `uncertain: True` in registry, excluded from ingest. At 5 m/px Sentinel-1 resolution, r < 20 m gives < 4-px radius — crescent annulus degenerates below reliable detection threshold.
   - **Dome-suspect rule**: ESRI 16-ray fit rms_m > 8 m → `uncertain: True, note: "dome_suspect_rmsX.X"`. High rms indicates a non-flat roof (cone/dome) where gradient signal is roof geometry, not shadow crescent. Run center-gate before final decision.
   - **Center-gate flag**: `center_gate: True` in registry marks tanks where A/B center comparison (eye-placed vs ESRI) is pending before the uncertain tag is finalized.
6. **Series health check** (§5.1, step 6.5): run `scripts/series_health.py --terminal X` after Stage-1 ingest and before any events analysis. A FLAG result (DEAD or WILD) stops the pipeline — do not compute events or publish a terminal passport until the FLAG is diagnosed and resolved. Corridors are defined in `diagnostics/SERIES_HEALTH_STANDARD.md`.

---

## Key Signals and Their Interpretations

| Signal | db_mean | vol_mn_bbl | Interpretation |
|---|---|---|---|
| FULL tank | Low (<2 dB) | High | Accumulation, export friction |
| EMPTY tank | High (>12 dB) | Low | Tank inactive or recently drained |
| ACTIVE tank | High std (>5 dB) | Volatile | Loading/unloading cycle |
| EXPORT PULSE | — | Drop >2 Mn in 6–12d | VLCC loading event |
| PRESSURE CRITICAL | — | >27.0 Mn bbl | SPI >85%, forced sell risk |

---

## Frontend Architecture

- **Marketing site**: `marketing/index.html` (static, deployed to oilradar.io)
- **App**: `product/dashboard.html` (auth-gated, app.oilradar.io/dashboard)
- **Auth**: Cloudflare Workers in `functions/auth/`
- **Data pipeline**: `scripts/main_script.py` (Sentinel Hub → Supabase → JSON files)
- **dbt**: `dbt/` folder — staging + marts on top of raw_observations

### dashboard.html — Per-Tank Map
- Leaflet 1.9.4 (`unpkg.com/leaflet@1.9.4`)
- ESRI World Imagery tiles
- SVG `L.divIcon` markers, same rendering as monitor.html but **no date restriction**
- Supabase credentials: `SUPABASE_URL`, `SUPABASE_KEY` (publishable key, same as monitor.html)
- TANKS coordinates: identical to `marketing/monitor.html` — keep in sync if adding new tanks
- Slider shows all dates including latest 2 weeks — this is the paid product feature
- Map section labelled "Per-Tank Storage Map — Full Access"

## Deployment

- Cloudflare Pages → wrangler.jsonc
- Data pipeline runs via GitHub Actions (cron, weekly + on-demand)
- Push to `main` → auto-deploy to oilradar.io

---

## Development Rules

1. All Kharg metrics must use the canonical definitions above — do not invent alternative formulas
2. Do not add countries to the main dashboard without explicit scope approval
3. Multi-country code in `product/dashboard.html` is LEGACY — keep commented, never delete
4. Max capacity is 31.78 Mn bbl — update only if new tanks are mapped and added to raw_tanks
5. Always deduplicate raw_observations on (date, tank_id) before any aggregation
6. **All UI text, chart labels, descriptions, and tooltips must be in English.** This is an English-only product. Never write Russian in any HTML, JS, or template file.

---

## File Locations

```
/marketing/index.html       → oilradar.io/           (landing page)
/marketing/monitor.html     → oilradar.io/monitor     (MAIN public monitor — map + charts)
/marketing/tanks.html       → oilradar.io/tanks       (per-tank Leaflet slider)
/marketing/gas-storage.html → oilradar.io/gas-storage
/marketing/team.html        → oilradar.io/team
/product/index.html         → oilradar.io/app
/product/dashboard.html     → oilradar.io/dashboard
/html/monitor.html          → NOT served publicly (internal/secondary file only)
/_redirects                 → Cloudflare Pages routing rules
/_headers                   → Cloudflare cache headers (/data/* = no-cache)
/data/data_daily.json       → daily volume feed
/data/data.json             → weekly smoothed volume feed
/scripts/main_script.py     → main SAR pipeline
```

**CRITICAL**: `/monitor` ALWAYS routes to `marketing/monitor.html` — never `html/monitor.html`.
Do not change `_redirects` without confirming this mapping.

---

## Checklist — Before Every Code Change

1. **Google Analytics**: Every HTML page must have GA4 with `G-TZQ5VH3GPY`
   ```html
   <script async src="https://www.googletagmanager.com/gtag/js?id=G-TZQ5VH3GPY"></script>
   <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-TZQ5VH3GPY');</script>
   ```
2. **_redirects**: Confirm `/monitor → /marketing/monitor.html` is intact after any routing change
3. **Units**: All volume figures use **Mb** in UI/charts (not MM bbl, not Mn bbl, not M bbl)
4. **Calendly CTA**: Any "Schedule a Call" / "Request Access" button → `https://calendly.com/bachevsky/intro`
5. **monitor.html layout**: Must always have Leaflet map + volume chart — do not remove either
6. **SEO**: Title includes "Kharg Island" + "Iran" or "crude oil"; first paragraph mentions both within 50 words

---

## Tone of Voice

- **Analytical, not promotional.** Signal over hype.
- **Numbers over adjectives.** "−6.35 Mn bbl in 12 days" not "significant drawdown"
- **Unit format**: always **Mb** in UI/charts/copy
- **No hyperbole**: never "game-changing", "revolutionary", "unprecedented"
- **Iran framing**: factual and neutral

---

## Key Links

- **Calendly**: https://calendly.com/bachevsky/intro
- **Monitor**: https://oilradar.io/monitor
- **GA4 ID**: G-TZQ5VH3GPY
