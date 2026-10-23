# diagnostics/mega — Full diagnostic export

Generated: 2026-10-23T08:59:37Z  SHA: bd6b363cc5a7  Run: 39928487859
Sections run: ['1', '2', '3.3', '5', '6']

## Files

| File | Description | Status |
|------|-------------|--------|
| `registry_full.csv` | All tanks, all fields from main registry | ✓ 11 KB |
| `registry_history.csv` | Git diff history of tank_registry.py | ✓ 219 KB |
| `esri_crops/farm_*.png` | ESRI z18 farm overview per terminal with circles | ✓ 128 files |
| `esri_crops/<tank_id>.png` | Per-tank ESRI z18 crop (3× diameter window) | ✓ 128 files |
| `source_points.txt` | Source-points search results in repo | ✓ 1 KB |
| `scenes.csv` | All scenes: V0 manifest + DB dates, in_db flag | ✓ 66 KB |
| `evalscript_and_params.txt` | Exact evalscript and SentinelHub request params | ✓ 2 KB |
| `prod_tank_series.csv` | Per-tank fill/vol series from prod code + DB | ✓ 5316 KB |
| `calibration_code/` | Copies of sar_fetch, volume, compute, config, etc. | ✓ 7 files |
| `tvt_all.csv` | volume_timeseries + terminal_volume_timeseries + n_tanks | ✓ 120 KB |
| `anchors_all/` | All anchor files from repo + Supabase + event_csv | ✓ 6 files |
| `anchor_mapping.txt` | Anchor-to-terminal wiring (file:line) | ✓ 80 KB |
| `past_results/` | Correlation result CSVs and run logs | ✓ 12 files |
| `tank_scene_variants.csv.gz` | All (terminal,tank,scene) metric variants — section 4 | pending |
| `range_profiles_*.npz` | LOS range profiles tank×scene×pts — section 4 | pending |
| `crops/<terminal_orbit>/` | Per-scene float16 .npz crops — section 3.1 | pending |
| `mean_stack/` | Mean stack .npz + PNG per orbit — section 3.2 | pending |

## Data quality checks (section 4)
