"""
terminal_checks.py — Runner for terminal health checks (Block B).

Usage:
  python terminal_checks.py --terminal yanbu
  python terminal_checks.py --terminal ras_tanura --checks C11,C04,C05
  python terminal_checks.py --terminal kharg --orbit 93   # filter by orbit

Outputs:
  diagnostics/checks/<terminal>/C<NN>.csv   — per-check results
  diagnostics/checks/CHECK_MATRIX.md        — terminal × check summary

Each CSV has run_date and git_sha columns (C17 provenance rule is satisfied
by presence of these columns in every output).

Terminals: yanbu, ras_tanura, kharg (read-only), al_flaw, jask, juwaymah.
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from supabase import create_client as _create_client

_sb_url = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
_sb_key = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
if not _sb_url or not _sb_key:
    sys.exit("FATAL: SUPABASE_URL and SUPABASE_KEY env vars must be set")
sb = _create_client(_sb_url, _sb_key)

# ─── terminal_observations fetch ──────────────────────────────────────────────

def fetch_terminal_obs(terminal_id: str, orbit: int = None) -> pd.DataFrame:
    """Fetch all rows for a terminal from terminal_observations (paginated)."""
    rows, offset = [], 0
    while True:
        q = (sb.table("terminal_observations")
               .select("date,tank_id,db_mean,relative_orbit,orbit_direction")
               .eq("terminal_id", terminal_id))
        if orbit is not None:
            q = q.eq("relative_orbit", orbit)
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_kharg_obs(orbit: int = None) -> pd.DataFrame:
    """Fetch Kharg from raw_observations (read-only reference)."""
    rows, offset = [], 0
    while True:
        q = sb.table("raw_observations").select("date,tank_id,db_mean")
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
        df["relative_orbit"] = None
        df["orbit_direction"] = None
    return df


# ─── registry lookup ──────────────────────────────────────────────────────────

def get_registry_tanks(terminal_id: str):
    from tank_registry import LOCATIONS
    for loc in LOCATIONS:
        if loc["id"] == terminal_id:
            return loc["tanks"]
    raise ValueError(f"Terminal '{terminal_id}' not found in tank_registry.LOCATIONS")


# ─── check imports ────────────────────────────────────────────────────────────

AVAILABLE_CHECKS = ["C04", "C05", "C06", "C06B", "C07", "C11", "C12", "C14", "C15", "C16"]

def load_check(check_id: str):
    import importlib
    mod = importlib.import_module(f"checks.{check_id.lower()}")
    return mod.run


# ─── output helpers ──────────────────────────────────────────────────────────

def save_csv(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"  → {path} ({len(df)} rows)")


def update_check_matrix(matrix_path: Path, terminal_id: str, check_id: str, summary_value: str):
    """Append/update one cell in the CHECK_MATRIX.md table."""
    # Read existing or start fresh
    if matrix_path.exists():
        text = matrix_path.read_text()
    else:
        text = "# CHECK_MATRIX\n\nTerminal × check → number / NOT_RUN / N_A / NOT_DETECTABLE\n\n"

    # Append a row record (simple log format; full table rebuilt at end by build_matrix)
    entry = f"| {terminal_id} | {check_id} | {summary_value} |\n"
    if entry not in text:
        text += entry
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    matrix_path.write_text(text)


# ─── matrix builder ───────────────────────────────────────────────────────────

def build_matrix(out_root: Path) -> str:
    """Scan all CSVs in out_root and build a clean terminal × check markdown table."""
    from checks import RUN_DATE, SHA

    records = {}  # (terminal, check) → summary value

    for csv_file in sorted(out_root.glob("*/*.csv")):
        terminal_id = csv_file.parent.name
        check_id = csv_file.stem.upper()
        df = pd.read_csv(csv_file)

        if "verdict" not in df.columns:
            val = str(len(df))
        else:
            verdicts = df["verdict"].dropna().unique().tolist()
            if len(verdicts) == 1 and verdicts[0] in ("NOT_RUN", "NOT_DETECTABLE", "N_A", "NO_DATA"):
                val = verdicts[0]
            else:
                # Summarise: count rows or count flagged
                n_flagged = int((df["verdict"].isin(["STUCK_CANDIDATE", "FLAG", "DEAD", "WILD"])).sum())
                val = f"n={len(df)}" + (f" flag={n_flagged}" if n_flagged else "")

        records[(terminal_id, check_id)] = val

    if not records:
        return "*(no results yet)*"

    terminals = sorted(set(t for t, _ in records))
    checks = sorted(set(c for _, c in records))

    header = "| Terminal | " + " | ".join(checks) + " |"
    sep    = "| --- | " + " | ".join(["---"] * len(checks)) + " |"
    rows = [header, sep]
    for term in terminals:
        cells = [records.get((term, c), "NOT_RUN") for c in checks]
        rows.append("| " + term + " | " + " | ".join(cells) + " |")

    lines = [
        "# CHECK_MATRIX",
        "",
        f"Run date: {RUN_DATE}  SHA: {SHA}",
        "",
        "\n".join(rows),
    ]
    return "\n".join(lines)


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", required=True, help="Terminal ID, e.g. yanbu")
    parser.add_argument("--checks", default=",".join(AVAILABLE_CHECKS),
                        help="Comma-separated check IDs, e.g. C11,C04")
    parser.add_argument("--orbit", type=int, default=None, help="Filter by relative orbit")
    args = parser.parse_args()

    terminal_id = args.terminal
    check_ids = [c.strip().upper() for c in args.checks.split(",")]

    out_root = Path(__file__).parent.parent / "diagnostics" / "checks"
    term_dir = out_root / terminal_id
    matrix_path = out_root / "CHECK_MATRIX.md"

    print(f"[terminal_checks] terminal={terminal_id}  checks={check_ids}  orbit={args.orbit}")

    # Fetch data
    if terminal_id == "kharg":
        print("  fetching raw_observations (Kharg, READ-ONLY)…")
        df = fetch_kharg_obs(orbit=args.orbit)
    else:
        print(f"  fetching terminal_observations for {terminal_id}…")
        df = fetch_terminal_obs(terminal_id, orbit=args.orbit)

    print(f"  {len(df):,} rows fetched, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")

    registry_tanks = get_registry_tanks(terminal_id)
    print(f"  registry: {len(registry_tanks)} tanks")

    # Run each check
    for check_id in check_ids:
        if check_id not in AVAILABLE_CHECKS:
            print(f"  skip {check_id}: not yet implemented")
            continue

        print(f"  running {check_id}…")
        run_fn = load_check(check_id)

        if check_id == "C11":
            detail_df, summary_df = run_fn(terminal_id, df, registry_tanks)
            save_csv(detail_df, term_dir / "C11_detail.csv")
            save_csv(summary_df, term_dir / "C11.csv")
        else:
            result_df = run_fn(terminal_id, df, registry_tanks)
            save_csv(result_df, term_dir / f"{check_id}.csv")

    # Rebuild CHECK_MATRIX.md
    matrix_text = build_matrix(out_root)
    matrix_path.write_text(matrix_text + "\n")
    print(f"  → {matrix_path}")

    print("done.")


if __name__ == "__main__":
    main()
