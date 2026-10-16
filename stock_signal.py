import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()

if not API_KEY:
    raise SystemExit("Missing ALPHA_VANTAGE_API_KEY")

SYMBOL = "SPY"

params = urllib.parse.urlencode({
    "function": "TIME_SERIES_DAILY",
    "symbol": SYMBOL,
    "outputsize": "compact",
    "apikey": API_KEY
})

url = "https://www.alphavantage.co/query?" + params

request = urllib.request.Request(
    url,
    headers={"User-Agent": "YOYO-OPTIONS-PRO/1.0"}
)

with urllib.request.urlopen(request, timeout=30) as response:
    data = json.load(response)

series = data.get("Time Series (Daily)")

if not series:
    raise SystemExit(
        "Stock data unavailable: " + str(list(data.keys()))
    )

dates = sorted(series.keys())

if len(dates) < 22:
    raise SystemExit("Not enough daily candles")

candles = []

for date in dates:
    row = series[date]

    candles.append({
        "date": date,
        "open": float(row["1. open"]),
        "high": float(row["2. high"]),
        "low": float(row["3. low"]),
        "close": float(row["4. close"]),
        "volume": float(row["5. volume"])
    })


def bollinger(values, deviation=2):
    recent = values[-20:]
    mean = sum(recent) / len(recent)

    variance = sum(
        (value - mean) ** 2 for value in recent
    ) / len(recent)

    std = variance ** 0.5

    return {
        "middle": mean,
        "upper": mean + deviation * std,
        "lower": mean - deviation * std
    }


closes = [c["close"] for c in candles]

bb2 = bollinger(closes, 2)
bb3 = bollinger(closes, 3)

# Previous completed day's Bollinger Bands
previous_bb2 = bollinger(closes[:-1], 2)

today = candles[-1]
yesterday = candles[-2]

close = today["close"]
open_price = today["open"]

# Volume confirmation: today's volume vs previous 20 days
previous_volumes = [
    candle["volume"] for candle in candles[-21:-1]
]

average_volume = sum(previous_volumes) / len(previous_volumes)

volume_ratio = (
    today["volume"] / average_volume
    if average_volume > 0 else 0
)

volume_confirmed = volume_ratio >= 1.2

# Candle confirmation
bullish_candle = close > open_price
bearish_candle = close < open_price

# Simple support/resistance from preceding 20 sessions
history = candles[-21:-1]

support = min(c["low"] for c in history)
resistance = max(c["high"] for c in history)

# Reversal detection using each day's own BB
lower_reversal = (
    yesterday["close"] <= previous_bb2["lower"]
    and close > bb2["lower"]
)

upper_reversal = (
    yesterday["close"] >= previous_bb2["upper"]
    and close < bb2["upper"]
)

# Price proximity to support/resistance (within 1%)
support_confirmed = (
    today["low"] <= support * 1.01
    and close > support
)

resistance_confirmed = (
    today["high"] >= resistance * 0.99
    and close < resistance
)

signal = "WAIT"
reason = "No confirmed setup"

if (
    lower_reversal
    and bullish_candle
    and volume_confirmed
    and support_confirmed
):
    signal = "CALL"
    reason = (
        "Lower BB reversal + bullish candle "
        "+ volume + support confirmation"
    )

elif (
    upper_reversal
    and bearish_candle
    and volume_confirmed
    and resistance_confirmed
):
    signal = "PUT"
    reason = (
        "Upper BB rejection + bearish candle "
        "+ volume + resistance confirmation"
    )

result = {
    "symbol": SYMBOL,
    "timeframe": "1D",
    "date": today["date"],
    "updated_at_utc": datetime.now(
        timezone.utc
    ).isoformat(),

    "open": round(today["open"], 4),
    "high": round(today["high"], 4),
    "low": round(today["low"], 4),
    "close": round(close, 4),

    "bb20_2": {
        k: round(v, 4) for k, v in bb2.items()
    },

    "bb20_3": {
        k: round(v, 4) for k, v in bb3.items()
    },

    "support": round(support, 4),
    "resistance": round(resistance, 4),

    "volume_ratio": round(volume_ratio, 2),
    "volume_confirmed": volume_confirmed,

    "bullish_candle": bullish_candle,
    "bearish_candle": bearish_candle,

    "signal": signal,
    "reason": reason,

    "status": "EXPERIMENTAL",
    "note": (
        "Daily stock-price signal only. "
        "Not a verified options contract. "
        "No strike, expiration or premium."
    )
}

with open("stock_signal.json", "w") as file:
    json.dump(result, file, indent=2)

print("YOYO OPTIONS PRO")
print("Stock:", SYMBOL)
print("Date:", today["date"])
print("Close:", round(close, 2))
print("Volume:", round(volume_ratio, 2), "x")
print("Support:", round(support, 2))
print("Resistance:", round(resistance, 2))
print("Signal:", signal)
print("Reason:", reason)
print("Saved stock_signal.json")
