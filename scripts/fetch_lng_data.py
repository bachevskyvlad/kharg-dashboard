"""
fetch_lng_data.py
─────────────────
Tянет данные уровня ПХГ Европы с GIE AGSI+
и сохраняет в data/lng_data.json
"""

import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

API_KEY  = "d8821dab5251273a95455e0228842254"
BASE_URL = "https://agsi.gie.eu/api"
HEADERS  = {"x-key": API_KEY}

COUNTRIES = {
    "EU": "Europe (aggregate)",
    "DE": "Germany",
    "IT": "Italy",
    "FR": "France",
    "NL": "Netherlands",
    "AT": "Austria",
    "PL": "Poland",
    "HU": "Hungary",
    "ES": "Spain",
    "UA": "Ukraine",
}

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "lng_data.json"


def fetch_country(country_code: str, days_back: int = 1095) -> list:
    date_from = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to   = datetime.today().strftime("%Y-%m-%d")

    all_rows = []
    page = 1

    while True:
        params = {
            "country": country_code,
            "from":    date_from,
            "to":      date_to,
            "size":    300,
            "page":    page,
        }
        try:
            r = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=20)
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"  ⚠️  {country_code}: {e}")
            break

        data  = r.json()
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            all_rows.append({
                "date":         item.get("gasDayStart", ""),
                "full":         _num(item.get("full")),
                "gasInStorage": _num(item.get("gasInStorage")),
                "injection":    _num(item.get("injection")),
                "withdrawal":   _num(item.get("withdrawal")),
                "trend":        item.get("trend", ""),
            })

        last_page = int(data.get("last_page", 1))
        if page >= last_page:
            break
        page += 1

    all_rows.sort(key=lambda x: x["date"])
    return all_rows


def _num(val):
    try:
        return round(float(val), 4) if val not in (None, "", "n/a", "-") else None
    except (ValueError, TypeError):
        return None


def main():
    print(f"🔄  Loading GIE AGSI+  ({datetime.today().strftime('%Y-%m-%d')})")

    result = {
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":     "GIE AGSI+  https://agsi.gie.eu",
        "countries":  {}
    }

    for code, name in COUNTRIES.items():
        print(f"  → {name} ({code})")
        rows = fetch_country(code)
        result["countries"][code] = {"name": name, "data": rows}
        print(f"     {len(rows)} records")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\n✅  Saved: {OUTPUT_PATH}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
