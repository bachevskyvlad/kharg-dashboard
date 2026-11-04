"""
patch_registry_enabled.py — One-shot script to patch tank_registry.py with
enabled/wl_step/wl_date/wl_n_scenes fields from whitelist_results.json.

Run once, then delete (or keep as audit log).
Kharg tanks untouched.
"""
import json, re, sys, os
sys.path.insert(0, os.path.dirname(__file__))

WHITELIST_JSON = "diagnostics/stage3/whitelist_results.json"
REGISTRY_PATH  = "scripts/tank_registry.py"
WL_DATE        = "2026-09-29"
WL_N_SCENES    = 18

with open(WHITELIST_JSON) as f:
    data = json.load(f)

# Build lookup: tank_id → {enabled, step}
wl_map = {}
for r in data["tanks"]:
    wl_map[r["tank_id"]] = {"enabled": r["whitelist"], "step": r["step"]}

with open(REGISTRY_PATH) as f:
    src = f.read()

def _build_wl_fields(tank_id):
    if tank_id not in wl_map:
        return ""
    info = wl_map[tank_id]
    enabled_s = "True" if info["enabled"] else "False"
    step_s    = f"{info['step']:.4f}" if info["step"] is not None else "None"
    return (f', "enabled": {enabled_s}, "wl_step": {step_s},'
            f' "wl_date": "{WL_DATE}", "wl_n_scenes": {WL_N_SCENES}')

# Match each tank dict line and inject fields if not already present
# Pattern: {"id": "xx_nn", ..., "h_max_m": N},
def _replacer(m):
    line = m.group(0)
    # Extract tank id
    id_match = re.search(r'"id":\s*"([^"]+)"', line)
    if not id_match:
        return line
    tank_id = id_match.group(1)
    # Skip Kharg tanks (they don't appear in wl_map)
    if tank_id not in wl_map:
        return line
    # Skip if already patched
    if '"enabled"' in line:
        return line
    wl = _build_wl_fields(tank_id)
    # Insert before the closing brace of the dict
    return re.sub(r'\}(\s*,?\s*#[^\n]*)$', r'{}}\2'.format(wl.replace('\\', '\\\\')), line,
                  flags=re.MULTILINE) if '#' in line else re.sub(r'\}(\s*,?\s*)$', r'{}}\1'.format(wl.replace('\\', '\\\\')), line)

# Process line by line
lines_out = []
for line in src.split("\n"):
    stripped = line.strip()
    if stripped.startswith('{"id":') and any(k in stripped for k in
        ["jw_","rt_","yb_"]):
        id_match = re.search(r'"id":\s*"([^"]+)"', stripped)
        if id_match:
            tank_id = id_match.group(1)
            if tank_id in wl_map and '"enabled"' not in stripped:
                wl = _build_wl_fields(tank_id)
                # Insert before the trailing }, or },
                line = re.sub(r'\}(\s*,?)(\s*#.*)?$', lambda m2:
                    wl + '}' + m2.group(1) + (m2.group(2) or ''), line)
    lines_out.append(line)

new_src = "\n".join(lines_out)

# Verify all 97 Saudi tanks got patched
patched = 0
for tid in wl_map:
    if f'"id": "{tid}"' in new_src and '"enabled"' in new_src[new_src.find(f'"id": "{tid}"'):new_src.find(f'"id": "{tid}"')+200]:
        patched += 1
print(f"Patched {patched}/{len(wl_map)} Saudi tanks")
if patched < len(wl_map):
    print("[WARN] Not all tanks patched — check regex")

with open(REGISTRY_PATH, "w") as f:
    f.write(new_src)
print(f"Written: {REGISTRY_PATH}")
