import json
from collections import defaultdict

COUNTRY_NAMES = {
    "IR": "🇮🇷 Iran",
    "IQ": "🇮🇶 Iraq",
    "SA": "🇸🇦 Saudi Arabia",
    "AE": "🇦🇪 UAE",
    "QA": "🇶🇦 Qatar",
    "KW": "🇰🇼 Kuwait",
    "BH": "🇧🇭 Bahrain",
    "RU": "🇷🇺 Russia",
}

COUNTRY_ORDER = ["IR", "IQ", "SA", "AE", "QA", "KW", "BH", "RU"]

try:
    data = json.load(open("data/ingest_summary.json"))
    by_country = defaultdict(list)
    for tid, info in data.items():
        country = info.get("country", "??")
        n = info["new"]
        mark = "\U0001f7e2" if n > 0 else "⚪"
        by_country[country].append(f"  {mark} {info['name']}: +{n}")

    lines = []
    seen = set()
    for code in COUNTRY_ORDER:
        if code in by_country:
            lines.append(f"<b>{COUNTRY_NAMES.get(code, code)}</b>")
            lines.extend(by_country[code])
            lines.append("")
            seen.add(code)
    for code, entries in by_country.items():
        if code not in seen:
            lines.append(f"<b>{COUNTRY_NAMES.get(code, code)}</b>")
            lines.extend(entries)
            lines.append("")

    print("\n".join(lines).rstrip())
except Exception as e:
    print(f"(no summary: {e})")
