# OilRadar — Kharg dashboard

SAR (Sentinel-1) tank-level estimates for Kharg Island and other terminals.
Pipeline: `ingest.py` → `compute.py` → `export.py` (`.github/workflows/monitor.yml`).

## Where the math lives

Everything that turns radar backscatter (dB) into barrels runs in Python, in two files:

| File | What it computes | Output |
|---|---|---|
| `scripts/volume.py` | dB → fill → height → barrels (`compute_volumes`, global p5/p95 calibration), daily / weekly aggregation | `volume_timeseries`, `terminal_volume_timeseries` (written by `compute.py`) |
| `scripts/gross_flows.py` | gross in / out flows per pass, built on `volume.compute_volumes`, so it uses the same calibration as the main series | `kharg_gross_flows_v2` (upsert on `date_to`, called from `export.py`) |

Constants are in `scripts/config.py` (`BARREL_PER_M3`, `TANK_HEIGHT_MIN`, `DB_FLOOR`).
The database only stores rows. It holds no functions, triggers or views that compute volumes.
Invariant: `net_mb` in `kharg_gross_flows_v2` equals the Δ of `volume_timeseries` (daily)
between the same two dates (±0.001 Mb from rounding).

## Deprecated (transition period until 2026-11-20)

| Object | Status | Replacement |
|---|---|---|
| Supabase RPC `refresh_gross_flows()` | **DEPRECATED.** The pipeline no longer calls it (`export_data.refresh_gross_flows` raises) | `scripts/gross_flows.py` |
| Supabase matview `kharg_gross_flows` | **DEPRECATED.** It is frozen at the last refresh and used a different calibration: p5/p95 per tank. The dashboard reads it only as a fallback while `kharg_gross_flows_v2` is empty | table `kharg_gross_flows_v2` |

Both objects are dropped after 2026-11-20. The SQL for that is at the bottom of
`dbt/migrations/2026-11-06_kharg_gross_flows_v2.sql`. When they are dropped, also
remove the fallback in `product/dashboard.html` / `product/zh-dashboard.html` (`loadFlows`).

## Gross flows: compare with the legacy matview

```bash
python scripts/gross_flows.py --compare 20   # read-only, prints legacy vs v2 for the last 20 passes
```
