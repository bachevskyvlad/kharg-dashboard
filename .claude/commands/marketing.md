# OilRadar Marketing Skill

Positioning, messaging, SEO rules, and technical checklist for oilradar.io.
Read before any copy, landing page, code, or SEO change.

---

## Positioning Statement

**OilRadar is the only real-time satellite intelligence platform for
Kharg Island crude oil storage — Iran's primary export chokepoint.**

We track 47 floating-roof tanks at Kharg Island via Sentinel-1 SAR,
delivering a 4–7 week lead over EIA, JODI, and IEA reports.

---

## What We Are and Are Not

### We ARE:
- The Kharg Island specialists — deepest coverage, longest track record
- A lead-indicator service (4–7 weeks ahead of official data)
- A physical supply signal, not a price forecast
- Data for commodity traders, macro funds, and energy intelligence buyers

### We are NOT:
- A Persian Gulf multi-country platform (other countries: on request only)
- A price prediction service
- An AIS/vessel tracking service
- A news/analyst service

---

## Target Audience

**Primary**: Oil commodity traders and macro funds with physical crude exposure
**Secondary**: Energy intelligence agencies, government energy analysts, corporate strategists with Iran exposure
**Tertiary**: Defense and geopolitical intelligence units (do not lead with this — it's inbound only)

---

## Tone of Voice (ToV)

- **Analytical, not promotional.** State facts and signal. Never hype.
- **Direct and concise.** No filler. Every sentence must carry information.
- **Expert peer, not salesman.** Speak as one analyst to another.
- **Friendly but not sycophantic.** Conversational where appropriate, but never fawning.
- **Numbers over adjectives.** "−6.35 Mn bbl in 12 days" beats "significant drawdown."
- **Unit format**: always write **Mn bbl** (not MM bbl, not million barrels, not M bbl)
- **No hyperbole**: Never say "the most powerful", "game-changing", "revolutionary", "unprecedented"
- **Iran framing**: factual and neutral. We are an intelligence product, not a political actor.
- **Monospace/terminal aesthetic**: fits DM Mono / dark UI design language

### Social media tone (Russian posts):
- Casual but precise — "пошла суета" level energy, not academic
- Lead with the signal, not the methodology
- Numbers first: "с 25,6 до 20,1 Mn bbl за 12 дней"
- End with oilradar.io/monitor or @channel tag depending on platform

---

## Data Source

**Primary pipeline**: `scripts/main_script.py`

- **Satellite**: Sentinel-1 IW (descending orbit), VV polarization
- **Provider**: Copernicus Data Space (Sentinel Hub API)
- **Resolution**: 5m/pixel
- **Method**: Inverse Crescent Radiometry
  - Each tank has a circular mask. Only the far-side 15m ring (crescent) is sampled
  - The crescent brightens in SAR when roof is LOW (tank empty) → more wall reflection
  - Formula: `fill_ratio = (dB_max − dB_mean) / (dB_max − dB_min)`, then:
    `h_current = h_min(2m) + fill_ratio × (h_max − h_min)`
    `vol_bbl = π × r² × h_current × 6.2898`
  - Scaling: global 5th–95th percentile of all observed dB values across all tanks and dates
- **Tanks**: 47 tanks total — 5 × r60m, 18 × r55m, 12 × r40m, 12 × r25m
  - Coordinates hardcoded in `raw_tanks` list in main_script.py
  - Each tank has `h_max` derived from API 650 floating roof ratios: r60→18m, r55→17m, r40→14m, r25→12m
- **Max theoretical capacity**: ~31.5 Mn bbl (calculated from all tank geometries)
- **Terminal utilization**: `vol / max_capacity × 100%`
- **Frequency**: Sentinel-1 passes ~2×/week; deduplication uses 12h gap filter

**Output files** (served at `/data/`):
- `data/data_daily.json` — raw daily per-observation totals `{dates[], values[]}` in Mn bbl
- `data/data.json` — weekly aggregated + SMA-13 smoothed `{dates[], values[]}` in Mn bbl

**Secondary storage**: Supabase (`supabase.co`)
- Table `raw_observations`: per-tank per-date dB readings
- Table `volume_timeseries`: aggregated daily + weekly volumes

---

## File Locations

```
/marketing/index.html     → oilradar.io/           (landing page)
/marketing/monitor.html   → oilradar.io/monitor     (MAIN public monitor — map + charts)
/marketing/tanks.html     → oilradar.io/tanks       (per-tank Leaflet slider)
/marketing/gas-storage.html → oilradar.io/gas-storage
/marketing/team.html      → oilradar.io/team
/product/index.html       → oilradar.io/app
/product/dashboard.html   → oilradar.io/dashboard
/html/monitor.html        → NOT served publicly (internal/secondary file only)
/_redirects               → Cloudflare Pages routing rules
/_headers                 → Cloudflare cache headers (/data/* = no-cache)
/data/data_daily.json     → daily volume feed
/data/data.json           → weekly smoothed volume feed
/scripts/main_script.py   → main SAR pipeline
```

**CRITICAL**: `/monitor` ALWAYS routes to `marketing/monitor.html` — never to `html/monitor.html`.
Do not change `_redirects` without confirming this mapping.

---

## Monitor Page — Required Layout (marketing/monitor.html)

The monitor page at oilradar.io/monitor must always contain:

### 1. Leaflet.js Map
- Dark tile layer: `https://{s}.basemaps.cartocdn.com/dark_matter/{z}/{x}/{y}{r}.png`
- Centered on Kharg Island: `[29.232, 50.311]`, zoom 14
- All 47 tanks rendered as `L.circle` markers with:
  - Color based on fill level: blue (full) → orange → red (low)
  - Radius proportional to actual tank radius in meters
  - Popup showing tank ID, fill %, volume in Mn bbl
- Latest date shown as map subtitle
- Map height: ~420px

### 2. Volume Chart (Chart.js)
- Data source: `data/data_daily.json` (last 90 days by default)
- Line chart, blue (#3b82f6), filled area
- Y-axis in Mn bbl
- Stat bar above chart: Latest volume | Day-over-day change with % | Status (BUILDING / DRAWDOWN)
- BUILDING = gold (#f0a500), DRAWDOWN = blue (#3b82f6)

### 3. Utilization Chart (Chart.js)
- Shows terminal utilization % over time
- Max capacity reference line

---

## Checklist — Before Every Code Change

1. **Google Analytics**: Every HTML page must have the GA4 snippet with `G-TZQ5VH3GPY`
   ```html
   <script async src="https://www.googletagmanager.com/gtag/js?id=G-TZQ5VH3GPY"></script>
   <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-TZQ5VH3GPY');</script>
   ```
2. **Favicons**: Every HTML page must have the full favicon block in `<head>`:
   ```html
   <link rel="icon" type="image/x-icon" href="/assets/images/favicon.ico">
   <link rel="icon" type="image/png" sizes="32x32" href="/assets/images/favicon-32x32.png">
   <link rel="icon" type="image/png" sizes="16x16" href="/assets/images/favicon-16x16.png">
   <link rel="apple-touch-icon" sizes="180x180" href="/assets/images/apple-touch-icon.png">
   ```
3. **_redirects**: Confirm `/monitor → /marketing/monitor.html` is intact
4. **Units**: All volume figures use **Mn bbl** (not MM bbl, not M bbl)
5. **CTA label**: Use **"Request Data Access"** — not "Schedule a Call", not "Book a Demo"
6. **Calendly link**: `https://calendly.com/bachevsky/60min`
7. **SEO**: Title tag includes "Kharg Island" + "Iran" or "crude oil"; first paragraph mentions both within 50 words
8. **JSON-LD**: Pages should include Dataset or FAQPage schema where relevant
9. **monitor.html layout**: Map present + volume chart present — do not remove either
10. **LLM SEO**: Use complete factual sentences with specific numbers; embed "Kharg Island", "Iran crude oil", "Sentinel-1 SAR" naturally in first 100 words of any page

---

## SEO Strategy

### Focus: Iranian oil export intelligence, Kharg Island crude tracking

### Primary keywords:
- Kharg Island oil
- Kharg Island crude oil
- Kharg Island oil storage
- Kharg Island oil exports
- Iran crude oil exports satellite
- Iran oil storage tracker
- Kharg oil terminal satellite
- SAR satellite oil Iran
- Iran crude oil intelligence

### Secondary keywords:
- Kharg Island chokepoint
- Iran petroleum exports
- Iranian crude export signal
- Shadow fleet Iran
- Iran oil sanctions impact
- Kharg Island floating roof tanks
- Sentinel-1 oil Iran
- Alternative data oil Iran
- Iran EIA alternative

### LLM SEO (AI search visibility):
- Use structured factual sentences: "OilRadar tracks 47 floating-roof tanks at Kharg Island..."
- Include specific numbers, methodologies, and comparisons in copy
- FAQ schema on key pages helps LLMs surface the content as authoritative answers

### Do NOT target:
- "Persian Gulf oil storage" (too broad)
- "Saudi Arabia oil terminal" (not our product)
- "global oil supply" (too generic)

### On-page SEO rules:
1. Title tag must include "Kharg Island" and "Iran" or "crude oil"
2. H1 must include "Kharg" or "Kharg Island"
3. First paragraph must mention "Kharg Island" and "Iran" within first 50 words
4. Meta description must include "Kharg Island" and a specific number (47 tanks, 90%, 4–7 weeks)
5. JSON-LD schema: Dataset + BreadcrumbList minimum; add FAQPage + HowTo where relevant
6. Canonical: `https://oilradar.io/[page]` (no trailing slash variants)

---

## Offer Structure

### Tier 1 — Kharg Island Monitor (CORE, live)
- What: Storage updates 2–3x/week, all 6 intelligence metrics, Iran supply signal
- CTA: "Open Monitor" → oilradar.io/monitor
- Do NOT price on the page

### Tier 2 — Custom Coverage (on request)
- Saudi Arabia, Iraq Basra, Kuwait, UAE Fujairah, Qatar
- CTA: "Request Data Access" → https://calendly.com/bachevsky/60min
- Framing: "Custom coverage for your specific exposure"
- Do NOT promise timelines without a call

### Roadmap (do not take orders):
Russia Primorsk, Venezuela, Libya, Nigeria — "on roadmap"

---

## Competitive Differentiation

| Competitor | Weakness vs OilRadar |
|---|---|
| AIS trackers (Kpler, VesselsValue) | Signal starts when tanker moves — we're 4–7 weeks earlier; shadow fleet goes dark |
| EIA / JODI | 4–8 week lag, survey-based, Iran self-reports |
| Satellite generalists (Planet, Maxar) | They sell imagery; we sell the intelligence layer on top |
| Bloomberg / Reuters | Consensus information; no proprietary physical signal |

---

## Call-to-Action Hierarchy

1. Primary CTA: "Open Monitor" → oilradar.io/monitor
2. Secondary CTA: "Request Data Access" → https://calendly.com/bachevsky/60min
3. Tertiary: oilradar.io/monitor link (free preview / demo)

**Calendly link**: https://calendly.com/bachevsky/60min
