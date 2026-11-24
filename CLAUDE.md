# OilRadar — правила работы с git и деплоем

Этот файл читается каждой сессией Claude Code. Правила обязательны.

---

## A. Git — одна ветка main

### A1. Старт любой задачи
```
git fetch origin
git checkout main
git reset --hard origin/main
```
Работать прямо в main. Если среда требует отдельную ветку — создать её от
свежего `origin/main` и смерджить в main в той же сессии, сразу после проверки.

### A2. Перед каждым push
```
git pull --rebase origin main
```
Конфликт решать **построчно**, сохраняя изменения **обеих** сторон.

**Запрещено** брать файл целиком (`--ours` / `--theirs` / `git checkout HEAD -- file`).
Если конфликт не удаётся разрешить построчно — остановиться и написать K.

### A3. Старые ветки
После мерджа ветка удаляется немедленно:
```
git push origin --delete <branch>
```
Оставлять в remote только `main`.

---

## B. Деплой

### B1. После любой правки сайта
Файлы `product/`, `marketing/`, `assets/`, `_worker.js` → обязательно запустить
`deploy.yml` и дождаться **success**. Задача закрыта только после деплоя.

### B2. check_site.py — запускать перед деплоем
```
python3 scripts/check_site.py
```
Падает, если в `product/dashboard.html` нет любого из маркеров:
- `assets/js/config.js`
- `showSection('hormuz')`
- `initGulfExit`
- `bo2-`
- `kharg_gross_flows_v2`
- `k === 'overviewVelocity'`

или файл стал короче предыдущего коммита более чем на 5%.

Шаг подключён в `deploy.yml` и `pipeline.yml` перед deploy.

---

## C. Оперативные ограничения (§A)

- `on: push` запрещён как класс в любом workflow.
- Секреты: только `${{ secrets.X }}`, без fallback. Пусто → FATAL.
- CDSE не использовать для новых детекторов.
- Planetary Computer first для SAR (sentinel-1-rtc).
- БД: любая запись/удаление/миграция — только после (1) бэкап-CSV коммитом,
  (2) dry-run сводки в чат, (3) go от K.
- Kharg = READ-ONLY (калибровка/референс).
- One pipeline. No other workflow may have a `schedule:`.
- Production code в `scripts/` на main. Ничего из `diagnostics/` в pipeline.
- Writes via `SUPABASE_SERVICE_KEY`. Publishable key — read-only.
- No invented data. No fallback constants as data.

---

## D. Финальная проверка

Делает K: скрин с `app.oilradar.io/dashboard` → Berth Occupancy.
Локальный рендер не считается.
