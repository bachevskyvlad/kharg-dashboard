import json, sys

try:
    data = json.load(open("data/ingest_summary.json"))
    lines = []
    for tid, info in data.items():
        n = info["new"]
        mark = "\U0001f7e2" if n > 0 else "⚪"
        lines.append(f"{mark} {info['name']}: +{n}")
    print("\n".join(lines))
except Exception as e:
    print(f"(no summary: {e})")
