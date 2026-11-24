"""
Pre-deploy integrity check for product/dashboard.html.
Exits with code 1 (FAIL) if any required marker is missing, any forbidden
marker is present, or the file shrank more than 5% vs the previous commit.
"""
import sys
import subprocess
import os

DASHBOARD = os.path.join(os.path.dirname(__file__), "..", "product", "dashboard.html")

REQUIRED_MARKERS = [
    "config.js",
    "showSection('hormuz')",
    "initGulfExit",
    "bo2-",
    "kharg_gross_flows_v2",
    "anchorage.json",
]
FORBIDDEN_MARKERS = ["Export Signals"]

MAX_SHRINK_PCT = 5  # fail if file is more than 5% shorter than previous commit


def run():
    path = os.path.abspath(DASHBOARD)
    if not os.path.exists(path):
        print(f"FATAL: {path} not found")
        sys.exit(1)

    text = open(path).read()
    cur_lines = text.count("\n")

    errors = []

    for m in REQUIRED_MARKERS:
        if m not in text:
            errors.append(f"MISSING required marker: {m!r}")

    for m in FORBIDDEN_MARKERS:
        if m in text:
            errors.append(f"FORBIDDEN marker present: {m!r}")

    # Compare line count vs HEAD (previous commit)
    try:
        prev = subprocess.check_output(
            ["git", "show", "HEAD:product/dashboard.html"],
            cwd=os.path.dirname(path),
            stderr=subprocess.DEVNULL,
        ).decode()
        prev_lines = prev.count("\n")
        if prev_lines > 0:
            shrink_pct = (prev_lines - cur_lines) / prev_lines * 100
            if shrink_pct > MAX_SHRINK_PCT:
                errors.append(
                    f"FILE SHRANK {shrink_pct:.1f}% "
                    f"({prev_lines} → {cur_lines} lines, threshold {MAX_SHRINK_PCT}%)"
                )
    except subprocess.CalledProcessError:
        pass  # no previous commit — skip size check

    if errors:
        print("check_site.py FAILED:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    print(
        f"check_site.py OK — {cur_lines} lines, "
        f"all {len(REQUIRED_MARKERS)} markers present, no forbidden markers"
    )


if __name__ == "__main__":
    run()
