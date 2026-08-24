# OilRadar Architecture Skill

Карта проекта — где что лежит, что является источником правды, методология, CI, правила HTML.

---

## Источники правды

| Что | Файл | Примечание |
|---|---|---|
| **Координаты и геометрия всех танков** | `scripts/tank_registry.py` | Единственный источник. `locations.py` и `locations.json` читают отсюда |
| Brent / Dubai цена | `data/prices.json` | Генерируется `fetch_prices.py` в CI. Fallback $82/$79.95 |
| Дневные объёмы Харга | `data/data_daily.json` | Из `export_from_supabase.py` → Supabase |
| Недельные сглаженные | `data/data.json` | Из `export_from_supabase.py` |
| Объёмы терминалов | `data/terminal_*.json` | Из `export_terminal_json.py` |
| Сырые SAR наблюдения | Supabase `raw_observations` | Пишет `main_script.py` |
| Gross Flows | Supabase `kharg_gross_flows` | dbt mart |

**НЕ источники правды:**
- `scripts/locations.py` — только адаптер поверх `tank_registry.py`
- `data/locations.json` — генерируется для фронтенда, не правится вручную
- Координаты танков в JS (monitor.html, dashboard.html) — дублируются вручную
- `data/fragility.json` — LEGACY, не использовать

---

## tank_registry.py — структура файла

Файл: `scripts/tank_registry.py`

```
LOCATIONS = [
  {
    "id": "kharg",          # machine id (= terminal_id в Supabase)
    "name": "Kharg Island",
    "country": "IR",        # ISO 3166-1 alpha-2
    "los_azimuth": 282,     # Sentinel-1 LOS azimuth для crescent mask
    "status": "active",
    "tanks": [
      {
        "id":       "t60_0",       # уникален во всём реестре
        "lat":      29.221...,     # WGS84 центр крыши танка
        "lon":      50.316...,
        "radius_m": 60,            # РАДИУС от центра до стенки (м)
                                   # diameter = radius_m × 2
        "h_max_m":  18,            # макс. высота заполнения (API 650)
        # опционально:
        "uncertain": True,         # существование/работа не подтверждены
        "notes": "...",            # аномалия
      },
      ...
    ]
  },
  ...
]
```

**Терминалы в файле:**
| id | name | country | танков |
|---|---|---|---|
| kharg | Kharg Island | IR | 47 |
| jask | Jask | IR | 20 (8 подтверждённых + 12 uncertain) |
| lavan | Lavan Island | IR | 24 |
| siri | Siri Island | IR | 6 |
| al_flaw | Al Flaw | IQ | 24 |

**Размерные классы и h_max (API 650):**
| radius_m | h_max_m | класс |
|---|---|---|
| 60 | 18 | t60 — самые большие |
| 55 | 17 | t55 — основной кластер |
| 40 | 14 | t40 — средние, активные загрузочные |
| 35 | 13 | jk/lv/af |
| 25 | 12 | t25 — самые маленькие |
| 20 | 9  | lv малые |
| 15 | 7  | lv наименьшие |

---

## Методология SAR → dB → объём

### 1. Crescent mask (`sar_fetch.py → build_crescent_masks`)

Маска — зона пикселей где SAR видит тень от плавающей крыши (обратная сторона от радара):

```
crescent = (dist_sq <= r_outer²) AND
           (dist_sq >= r_inner²) AND
           (dot_product > 0)          ← дальняя сторона от радара
```

- `r_outer` = `radius_m / RESOLUTION` (RESOLUTION=5 м/пиксель)
- `r_inner` = `(radius_m - WIDTH_WIDE) / RESOLUTION` (WIDTH_WIDE=15м → кольцо 3 пикселя)
- `dot_product` = проекция вектора (пиксель − центр) на вектор LOS (los_azimuth=282°)
- Маска предвычисляется один раз на прогон (зависит только от геометрии изображения)

### 2. dB per tank (`sar_fetch.py → fetch_new_records`)

```python
# Sentinel Hub возвращает сырую линейную VV мощность (не dB)
# Усредняем в линейном домене (избегает нелинейного bias логарифма)
mean_linear = np.mean(vv_pixels[crescent_mask])   # NaN/Inf отфильтрованы
db_mean = 10 * np.log10(mean_linear)
```

Результат: один `db_mean` на (date, tank_id) → пишем в Supabase `raw_observations`.

**Физика**: чем ниже dB → тем больше нефти (жидкая поверхность гасит радар).

### 3. Объём (`volume.py → compute_volumes`)

```python
# Шаг 1: глобальный клиппинг [p5, p95] по всей истории всех танков
dB_min = df['db_mean'].quantile(0.05)   # 5-й персентиль
dB_max = df['db_mean'].quantile(0.95)   # 95-й персентиль

# Шаг 2: fill_ratio (инвертированный — низкий dB = полный танк)
fill_ratio = (dB_max - clip(db_mean, dB_min, dB_max)) / (dB_max - dB_min)

# Шаг 3: физическая высота (TANK_HEIGHT_MIN=2м — минимальный уровень крыши)
h_current = 2 + fill_ratio × (h_max - 2)

# Шаг 4: объём в баррелях
vol_bbl = π × radius_m² × h_current × 6.2898
```

**Зачем p5/p95 а не min/max:** защита от выбросов (t55_5, t55_10 хронически пустые; t55_31, t60_1 хронически полные). Клиппинг сжимает их к границе вместо того чтобы исказить всю шкалу.

**Важно:** персентили пересчитываются глобально на каждом прогоне — новые данные немного сдвигают исторические объёмы. Это корректно (улучшает калибровку).

### 4. Агрегация

- **Daily** (`aggregate_daily`): среднее по (день, tank_id) → сумма по всем танкам → `vol_mn_bbl`
- **Weekly** (`aggregate_weekly`): выравнивание к понедельнику недели, потом EWMA α=0.1 (~10-недельная память). Только для графика/маркетинга (`data.json`). Аналитики используют `data_daily.json`.

---

## CI — расписание и джобы (`monitor.yml`)

**Расписание:** `0 3 * * *` и `0 15 * * *` (03:00 и 15:00 UTC ежедневно)
**Ручной запуск:** `workflow_dispatch` с опцией `force_reset`

**Джобы:**

```
gate ──────────────────────────────────────────────────────────────────────────
  Проверяет: сколько дней прошло с последней точки данных?
  Если < REVISIT_GATE_DAYS (5 дней) → run=false, пропускаем тяжёлый fetch.
  Если ≥ 5 дней или manual → run=true.
  Cost: секунды (только читает data_daily.json).

kharg (needs: gate, if: run==true) ───────────────────────────────────────────
  1. pip install -r scripts/requirements.txt
  2. Restore cache: public/data_raw_cache.json
  3. python scripts/main_script.py
     (Sentinel Hub → raw_observations в Supabase)
  4. Save cache: public/volume_graph.png
  5. Telegram notify SUCCESS / FAILED

commit_data (needs: gate+kharg, if: always && run==true) ─────────────────────
  1. pip install supabase pandas yfinance
  2. python scripts/fetch_prices.py    → data/prices.json
  3. python scripts/export_from_supabase.py → data/data.json, data_daily.json
  4. python scripts/export_terminal_json.py → data/terminal_*.json
  5. git add data/ volume_graph.png && git commit && git push
  6. Telegram notify FAILED (если упало)
```

**Secrets:** `CLIENT_ID`, `CLIENT_SECRET` (Sentinel Hub), `NOTIFY_SECRET` (Telegram)

---

## HTML файлы и правила оформления

### Карта файлов
| Файл | URL | Назначение |
|---|---|---|
| `marketing/index.html` | oilradar.io/ | Лендинг |
| `marketing/monitor.html` | oilradar.io/monitor | Публичный монитор (Leaflet + chart) |
| `marketing/tanks.html` | oilradar.io/tanks | Per-tank Leaflet slider |
| `marketing/gas-storage.html` | oilradar.io/gas-storage | — |
| `marketing/team.html` | oilradar.io/team | — |
| `product/index.html` | oilradar.io/app | Auth gate |
| `product/dashboard.html` | oilradar.io/dashboard | Платный дашборд (полный доступ) |

**Критично:** `/monitor` → `marketing/monitor.html`. Никогда не `html/monitor.html`. Не менять `_redirects` без проверки этого маппинга.

### Обязательные элементы каждой HTML страницы

**1. Google Analytics GA4:**
```html
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TZQ5VH3GPY"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('js',new Date());gtag('config','G-TZQ5VH3GPY');</script>
```

**2. Calendly CTA:**
Любая кнопка "Schedule a Call" / "Request Access" / "Get Access" →
`https://calendly.com/bachevsky/60min`

**3. monitor.html:** всегда содержит Leaflet map + volume chart. Нельзя убирать ни то ни другое.

**4. SEO (marketing pages):**
- Title содержит "Kharg Island" + "Iran" или "crude oil"
- Первый абзац упоминает оба термина в первых 50 словах

**5. Единицы:** везде **Mb** в UI/графиках/текстах. Не MM bbl, не Mn bbl, не M bbl.

**6. data-cache:** `_headers` устанавливает `Cache-Control: no-cache` на `/data/*`. Не менять.

---

## Tone of Voice

- **Аналитичный, не рекламный.** Сигнал важнее хайпа.
- **Цифры вместо прилагательных.** "−6.35 Mb за 12 дней" а не "значительный drawdown"
- **Иранская тема:** фактически и нейтрально. Без политических оценок.
- **Никогда:** "unprecedented", "game-changing", "revolutionary"
- **Оценки:** всегда помечать как оценки. "Est. at Dubai $X −$Y/bbl discount. Actual deal terms unknown."

---

## Файловая структура

```
scripts/
  tank_registry.py        ← ИСТОЧНИК ПРАВДЫ: все танки, все терминалы
  locations.py            ← адаптер: читает tank_registry → raw_tanks, TERMINALS
  main_script.py          ← SAR пайплайн (Sentinel Hub → Supabase)
  volume.py               ← физическая модель (p5/p95, fill_ratio, vol_bbl)
  sar_fetch.py            ← crescent mask, fetch SAR imagery
  export_from_supabase.py ← data.json + data_daily.json
  export_terminal_json.py ← terminal_*.json
  fetch_prices.py         ← prices.json (yfinance BZ=F)
  multi_terminal_pipeline.py ← SAR для не-Харга терминалов

data/
  locations.json          ← генерируется из tank_registry.py (для фронтенда)
  data_daily.json         ← daily объёмы Харга
  data.json               ← недельные сглаженные
  prices.json             ← Brent/Dubai
  terminal_*.json         ← объёмы терминалов

.github/workflows/
  monitor.yml             ← gate → kharg → commit_data (03:00 + 15:00 UTC)

dbt/                      ← трансформации на Supabase
_redirects                ← Cloudflare Pages routing (/monitor → marketing/monitor.html)
_headers                  ← Cache-Control: no-cache на /data/*
```
