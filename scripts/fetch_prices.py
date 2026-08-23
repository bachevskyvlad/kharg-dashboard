"""
fetch_prices.py — Fetch latest Brent/Dubai crude price and write data/prices.json.

Dubai estimated as Brent * 0.975 (historical average spread).
Used by the dashboard to compute estimated export revenue.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

OUTPUT = Path(__file__).parent.parent / "data" / "prices.json"

FALLBACK = {"brent_usd": 82.0, "dubai_usd": 79.95, "date": str(date.today()), "source": "fallback"}


def fetch():
    try:
        import yfinance as yf
        end = date.today()
        start = end - timedelta(days=10)
        ticker = yf.Ticker("BZ=F")
        hist = ticker.history(start=str(start), end=str(end))
        if hist.empty:
            return None
        brent = round(float(hist["Close"].dropna().iloc[-1]), 2)
        dubai = round(brent * 0.975, 2)
        price_date = hist.index[-1].strftime("%Y-%m-%d")
        return {"brent_usd": brent, "dubai_usd": dubai, "date": price_date, "source": "yfinance:BZ=F"}
    except Exception as e:
        print(f"  [!] yfinance failed: {e}")
        return None


data = fetch() or FALLBACK
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT, "w") as f:
    json.dump(data, f)
print(f"  prices.json: Brent=${data['brent_usd']} Dubai=${data['dubai_usd']} ({data['source']})")
