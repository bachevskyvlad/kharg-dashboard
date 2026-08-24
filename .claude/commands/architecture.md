# OilRadar Architecture Skill

Карта проекта — где что лежит, что является источником правды, что нет.

---

## Источники правды (менять только здесь)

| Что | Файл | Примечание |
|---|---|---|
| Координаты, радиус, высота всех танков по всем терминалам | `data/locations.json` | Единственный источник. `scripts/locations.py` читает отсюда |
| Цена Brent / Dubai | `data/prices.json` | Генерируется `scripts/fetch_prices.py` в CI. Fallback $82/$79.95 |
| Дневные объёмы Харга | `data/data_daily.json` | Генерируется `scripts/export_from_supabase.py` из Supabase |
| Сглаженные недельные объёмы | `data/data.json` | Генерируется `scripts/export_from_supabase.py` |
| Объёмы иранских терминалов (Jask и др.) | `data/terminal_*.json` | Генерируется `scripts/export_terminal_json.py` |
| Сырые SAR наблюдения | Supabase → `raw_observations` | Пишет `scripts/main_script.py` |
| Агрегированные временные ряды | Supabase → `volume_timeseries` | Пишет dbt поверх `raw_observations` |
| Gross Flows (экспорт/приток) | Supabase → `kharg_gross_flows` | dbt mart |

---

## Что НЕ является источником правды

| Файл | Почему не источник |
|---|---|
| `scripts/locations.py` | Только читает `data/locations.json`, не хранит данные |
| `marketing/monitor.html`, `product/dashboard.html` | Координаты танков продублированы в JS — должны совпадать с `locations.json`, но не генерируются из него автоматически |
| `data/fragility.json` | LEGACY. Не используется в основном дашборде |
| `public/data_raw_cache.json` | Кэш SAR-пайплайна, пересчитывается при force_reset |

---

## Структура файлов

```
data/
  locations.json        ← ИСТОЧНИК ПРАВДЫ: все терминалы, все танки, геометрия
  data_daily.json       ← daily объёмы Харга (из Supabase)
  data.json             ← недельные сглаженные объёмы Харга (из Supabase)
  prices.json           ← Brent/Dubai цена (из yfinance в CI)
  terminal_kharg.json   ← объёмы терминала Харг (из Supabase)
  terminal_jask.json    ← объёмы терминала Jask (из Supabase)
  terminal_iran_total.json ← суммарный иранский экспорт

scripts/
  locations.py          ← читает locations.json → raw_tanks, TERMINALS, get_super_bbox
  main_script.py        ← SAR-пайплайн Харга (Sentinel Hub → Supabase)
  export_from_supabase.py ← data.json + data_daily.json
  export_terminal_json.py ← terminal_*.json
  fetch_prices.py       ← prices.json
  multi_terminal_pipeline.py ← SAR для других терминалов

marketing/
  index.html            → oilradar.io/           (лендинг)
  monitor.html          → oilradar.io/monitor    (публичный монитор)
  tanks.html            → oilradar.io/tanks
  gas-storage.html      → oilradar.io/gas-storage
  team.html             → oilradar.io/team

product/
  index.html            → oilradar.io/app
  dashboard.html        → oilradar.io/dashboard  (платный дашборд)

functions/
  auth/                 ← OTP аутентификация, Cloudflare Workers
  api/v1/               ← REST API (ключи, танки, хранилище)

.github/workflows/
  monitor.yml           ← CI: gate → kharg SAR → commit_data (2× в день)

dbt/                    ← трансформации поверх raw_observations в Supabase
```

---

## Поток данных

```
Sentinel Hub (SAR)
    ↓ main_script.py
Supabase: raw_observations
    ↓ dbt
Supabase: volume_timeseries, kharg_daily, kharg_gross_flows
    ↓ export_from_supabase.py + export_terminal_json.py
data/*.json
    ↓ Cloudflare Pages (auto-deploy от main)
dashboard.html / monitor.html читают /data/*.json
```

---

## Правила

1. **Танки и координаты** — правишь только `data/locations.json`
2. **Координаты в JS** (monitor.html, dashboard.html) — держи в синхронизации с `locations.json` вручную при добавлении новых танков
3. **`data/fragility.json`** — не трогать, legacy, в коде закомментировано
4. **`max_capacity = 33.2 Mn bbl`** — обновлять только если добавлены новые танки в `locations.json`
5. **CI pipeline** — данные коммитит бот от имени `bot@github.com`, не конфликтуй с ним при пуше
