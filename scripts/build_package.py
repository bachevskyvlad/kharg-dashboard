"""
build_package.py — Single entrypoint for the OilRadar client package.

Runs Tasks 0–5 in sequence:
  Task 0: Fetch PortWatch + Supabase storage + events table
  Task 1: RT event study (2026 conflict events vs placebo)
  Task 2: Kharg benchmark vs JODI Iran crude exports
  Task 3: 3-phase correlation (Saudi storage ↔ PortWatch exports)
  Task 4: AIS-blind argument figure
  Task 5: Assemble PROOF_OF_VALUE.md

Usage:
  cd /path/to/kharg-dashboard
  python scripts/build_package.py [--skip-fetch] [--tasks 1,2,5]

  --skip-fetch   skip Task 0 (use cached CSVs under client_package/data/)
  --tasks N,...  run only these task numbers (comma-separated)

Outputs land in client_package/:
  PROOF_OF_VALUE.md
  data/*.csv
  figures/*.png
"""

import argparse
import os
import sys
import traceback

# Ensure scripts/ is on path so client_pkg imports work
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from client_pkg import task0_anchors
from client_pkg import task1_event_study
from client_pkg import task2_kharg_benchmark
from client_pkg import task3_correlation
from client_pkg import task4_ais_blind
from client_pkg import task5_report


def _run_task(name, fn, results_store, key):
    print(f"\n{'='*60}")
    print(f"  RUNNING {name}")
    print(f"{'='*60}")
    try:
        result = fn()
        results_store[key] = result or {}
        print(f"  [{name}] OK")
    except Exception as e:
        print(f"  [{name}] FAILED: {e}")
        traceback.print_exc()
        results_store[key] = {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Build OilRadar client package")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip Task 0 (use cached CSVs)")
    parser.add_argument("--tasks", default="",
                        help="Comma-separated task numbers to run (default: all)")
    args = parser.parse_args()

    if args.tasks:
        run_set = set(int(t.strip()) for t in args.tasks.split(",") if t.strip())
    else:
        run_set = {0, 1, 2, 3, 4, 5}

    results = {}

    if 0 in run_set and not args.skip_fetch:
        _run_task("Task 0 — External anchors", task0_anchors.run, results, "task0")
    elif args.skip_fetch:
        print("\n[Task 0] Skipped (--skip-fetch)")

    if 1 in run_set:
        _run_task("Task 1 — RT event study", task1_event_study.run, results, "task1")

    if 2 in run_set:
        _run_task("Task 2 — Kharg benchmark vs JODI", task2_kharg_benchmark.run, results, "task2")

    if 3 in run_set:
        _run_task("Task 3 — Correlation analysis", task3_correlation.run, results, "task3")

    if 4 in run_set:
        _run_task("Task 4 — AIS-blind argument", task4_ais_blind.run, results, "task4")

    if 5 in run_set:
        _run_task("Task 5 — Assemble report", lambda: task5_report.run(results), results, "task5")

    print(f"\n{'='*60}")
    print("  BUILD COMPLETE")
    print(f"{'='*60}")
    print("  client_package/PROOF_OF_VALUE.md")
    print("  client_package/data/  (CSVs)")
    print("  client_package/figures/  (PNGs)")


if __name__ == "__main__":
    main()
