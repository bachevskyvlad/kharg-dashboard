"""
patch_registry_orbits.py — Apply orbit_survey_registry_patch.json to tank_registry.py.

Reads diagnostics/orbit_survey_registry_patch.json (output of orbit_survey.py --force).
For each terminal_id in the patch:
  - Adds/replaces the `allowed_orbits` key in the LOCATIONS entry.
  - Removes the bare `los_azimuth` key ONLY IF allowed_orbits is being set
    (los_azimuth becomes redundant — it's encoded in allowed_orbits[*].los_azimuth).

Operates as a targeted string-level rewrite of tank_registry.py so it
preserves all comments, whitespace and ordering.

Usage:
  cd scripts
  python patch_registry_orbits.py --dry-run   # print diff only
  python patch_registry_orbits.py --apply     # write tank_registry.py in place

ALWAYS review the diff before --apply.
"""

import sys, json, re
from pathlib import Path

DRY = "--apply" not in sys.argv

# Accept --patch <file> to use a different patch file
_patch_arg = None
if "--patch" in sys.argv:
    _patch_arg = sys.argv[sys.argv.index("--patch") + 1]

PATCH_PATH    = Path(_patch_arg) if _patch_arg else \
                Path(__file__).parent.parent / "diagnostics" / "orbit_survey_registry_patch.json"
REGISTRY_PATH = Path(__file__).parent / "tank_registry.py"

def main():
    if not PATCH_PATH.exists():
        print(f"ERROR: patch file not found: {PATCH_PATH}")
        print("  Run: python orbit_survey.py --force")
        sys.exit(1)

    patch = json.loads(PATCH_PATH.read_text())
    print(f"Patch contains {len(patch)} terminals: {sorted(patch)}")
    print()

    src = REGISTRY_PATH.read_text()

    for tid, allowed_orbits in sorted(patch.items()):
        # Build the allowed_orbits block as Python source
        ao_lines = []
        for o in allowed_orbits:
            ao_lines.append(
                f'            {{"relative_orbit": {o["relative_orbit"]}, '
                f'"direction": "{o["direction"]}", '
                f'"los_azimuth": {o["los_azimuth"]}}}'
            )
        # Terminal fields use 8-space indent; list items 12-space
        ao_block = "        \"allowed_orbits\": [\n" + ",\n".join(ao_lines) + "\n        ],\n"

        # Find the terminal block in source: locate '"id": "tid"'
        # Terminal blocks are indented 4 spaces; fields inside 8 spaces.
        id_pat = re.compile(
            r'(    \{[^{}]*?"id":\s*"' + re.escape(tid) + r'".*?)(    \})',
            re.DOTALL
        )
        m = id_pat.search(src)
        if not m:
            print(f"  [{tid}] WARN: could not find terminal block in registry — skipping")
            continue

        block_start, block_end = m.start(), m.end()
        block = m.group(0)

        # If allowed_orbits already present, replace it
        if '"allowed_orbits"' in block:
            ao_existing = re.search(
                r'\s*"allowed_orbits":\s*\[.*?\],?\n',
                block, re.DOTALL
            )
            if ao_existing:
                new_block = block[:ao_existing.start()] + "\n" + ao_block + block[ao_existing.end():]
            else:
                new_block = block  # can't parse existing, leave
        else:
            # Insert allowed_orbits after the "id" line
            id_line = re.search(r'"id":\s*"' + re.escape(tid) + r'"[^\n]*\n', block)
            if id_line:
                ins = id_line.end()
                new_block = block[:ins] + ao_block + block[ins:]
            else:
                new_block = block

        # Remove bare los_azimuth if we're setting allowed_orbits
        new_block = re.sub(r'\s*"los_azimuth":\s*\d+,?\n', '\n', new_block)

        # Show diff
        if new_block != block:
            print(f"  [{tid}] PATCH:")
            old_lines = block.splitlines()
            new_lines = new_block.splitlines()
            for l in old_lines:
                if l not in new_lines:
                    print(f"    - {l}")
            for l in new_lines:
                if l not in old_lines:
                    print(f"    + {l}")
            src = src[:block_start] + new_block + src[block_end:]
        else:
            print(f"  [{tid}] no change needed")

    if DRY:
        print()
        print("DRY-RUN — pass --apply to write tank_registry.py")
    else:
        REGISTRY_PATH.write_text(src)
        print()
        print(f"Written: {REGISTRY_PATH}")


if __name__ == "__main__":
    main()
