import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")

if not API_KEY:
    raise SystemExit("Missing ALPHA_VANTAGE_API_KEY")

params = urllib.parse.urlencode({
    "function": "TIME_SERIES_DAILY",
    "symbol": "SPY",
    "outputsize": "compact",
    "apikey": API_KEY
})

url = "https://www.alphavantage.co/query?" + params

with urllib.request.urlopen(url, timeout=30) as response:
    data = json.load(response)

series = data.get("Time Series (Daily)")

if not series:
    raise SystemExit(
        "Stock data unavailable: " +
        str(list(data.keys()))
    )

dates = sorted(series.keys())

if len(dates) < 21:
    raise SystemExit("Not enough candles")

closes = [
    float(series[d]["4. close"])
    for d in dates
]

highs = [
    float(series[d]["2. high"])
    for d in dates
]

lows = [
    float(series[d]["3. low"])
    for d in dates
]

def bollinger(values, deviation):
    recent = values[-20:]
    mean = sum(recent) / 20

    variance = sum(
        (x - mean) ** 2 for x in recent
    ) / 20

    std = variance ** 0.5

    return {
        "middle": mean,
        "upper": mean + deviation * std,
        "lower": mean - deviation * std
    }

bb2 = bollinger(closes, 2)
bb3 = bollinger(closes, 3)

close = closes[-1]
previous = closes[-2]

signal = "WAIT"
reason = "No confirmed setup"

# Example experimental signal rules.
# These are not validated trading recommendations.
if previous <= bb2["lower"] and close > bb2["lower"]:
    signal = "CALL"
    reason = "Price recovered above lower BB2"

elif previous >= bb2["upper"] and close < bb2["upper"]:
    signal = "PUT"
    reason = "Price returned below upper BB2"

result = {
    "symbol": "SPY",
    "timeframe": "1D",
    "date": dates[-1],
    "updated_at_utc": datetime.now(
        timezone.utc
    ).isoformat(),
    "close": round(close, 4),
    "bb20_2": {
        k: round(v, 4)
        for k, v in bb2.items()
    },
    "bb20_3": {
        k: round(v, 4)
        for k, v in bb3.items()
    },
    "signal": signal,
    "reason": reason,
    "status": "EXPERIMENTAL",
    "note": (
        "Stock-price analysis only. "
        "No verified option contract or premium."
    )
}

with open("stock_signal.json", "w") as f:
    json.dump(result, f, indent=2)

print("YOYO OPTIONS PRO")
print("Date:", dates[-1])
print("SPY Close:", round(close, 2))
print("BB20,2:", bb2)
print("BB20,3:", bb3)
print("Signal:", signal)
print("Reason:", reason)
print("Saved stock_signal.json")
