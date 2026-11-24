# OilRadar Skill — часть A: Git и деплой

## A1. Старт задачи — всегда

```bash
git fetch origin
git checkout main
git reset --hard origin/main
```

Работать в main. Отдельная ветка — только если среда это требует;
смердж в main в той же сессии сразу после проверки.

## A2. Перед push

```bash
git pull --rebase origin main
```

Конфликт — построчно, сохранять изменения обеих сторон.
`--ours` / `--theirs` / `git checkout HEAD -- file` **запрещены**.
Не получается → стоп, написать K.

## A3. Ветки

После мерджа — сразу удалить:
```bash
git push origin --delete <branch>
```
В remote только `main`.

## A4. После правки сайта

`product/` / `marketing/` / `assets/` / `_worker.js` →
обязательно `deploy.yml` → дождаться success.

## A5. check_site.py

Запускать перед деплоем:
```bash
python3 scripts/check_site.py
```

Маркеры, которые должны быть в `product/dashboard.html`:
- `assets/js/config.js`
- `showSection('hormuz')`
- `initGulfExit`
- `bo2-`
- `kharg_gross_flows_v2`
- `k === 'overviewVelocity'`

Файл не должен уменьшиться более чем на 5% от предыдущего коммита.

## A6. Оперативные ограничения

| Правило | Значение |
|---|---|
| `on: push` | запрещён в любом workflow |
| Секреты | только `${{ secrets.X }}`, без fallback |
| CDSE | не использовать для новых детекторов |
| SAR source | Planetary Computer first (sentinel-1-rtc) |
| DB writes | бэкап-CSV → dry-run в чат → go от K |
| Kharg | READ-ONLY |
| Schedules | только в pipeline.yml, нигде больше |
| Production code | только в `scripts/` на main |
| Writes | только SUPABASE_SERVICE_KEY |
| Invented data | запрещено |
