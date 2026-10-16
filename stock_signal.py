"""YOYO Options Pro: incremental daily research feed. No trading execution."""

import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

SYMBOLS = (
    'SPY QQQ IWM DIA NVDA AAPL MSFT AMD GOOGL TSLA '
    'AMZN META PLTR NFLX AVGO MU INTC TSM JPM BAC '
    'GS COIN MSTR HOOD SOFI'
).split()

AV = os.getenv('ALPHA_VANTAGE_API_KEY', '').strip()
TRADIER = os.getenv('TRADIER_TOKEN', '').strip()
TRADIER_BASE = os.getenv(
    'TRADIER_BASE_URL', 'https://api.tradier.com'
).rstrip('/')
MASSIVE = os.getenv('MASSIVE_API_KEY', '').strip()

BATCH = max(1, min(25, int(os.getenv('SYMBOLS_PER_RUN', '5'))))
DELAY = max(0, float(os.getenv('REQUEST_DELAY_SECONDS', '15')))
CACHE = Path('stock_signals.json')


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def fetch(url, headers=None):
    req = urllib.request.Request(
        url,
        headers=headers or {'User-Agent': 'YOYO-Options-Pro/1.0'}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def stock(symbol):
    query = urllib.parse.urlencode({
        'function': 'TIME_SERIES_DAILY',
        'symbol': symbol,
        'outputsize': 'compact',
        'apikey': AV
    })

    raw = fetch('https://www.alphavantage.co/query?' + query)
    series = raw.get('Time Series (Daily)')

    if not isinstance(series, dict):
        raise ValueError(
            'Alpha Vantage unavailable or rate-limited: '
            + str(list(raw))
        )

    dates = sorted(series)

    if len(dates) < 22:
        raise ValueError('Need 22 completed candles')

    candles = [series[d] for d in dates]
    close = [float(c['4. close']) for c in candles]
    high = [float(c['2. high']) for c in candles]
    low = [float(c['3. low']) for c in candles]
    vol = [float(c['5. volume']) for c in candles]

    def bb(values, n):
        mean = sum(values) / len(values)
        std = (
            sum((x - mean) ** 2 for x in values) / len(values)
        ) ** 0.5

        return {
            'upper': round(mean + n * std, 4),
            'middle': round(mean, 4),
            'lower': round(mean - n * std, 4)
        }

    current2 = bb(close[-20:], 2)
    current3 = bb(close[-20:], 3)
    prev2 = bb(close[-21:-1], 2)

    previous_volume = sum(vol[-21:-1]) / 20
    vr = vol[-1] / previous_volume if previous_volume else 0

    direction = 'WAIT'
    reason = 'No confirmed experimental setup'

    if (
        close[-2] < prev2['lower']
        and close[-1] > current2['lower']
        and close[-1] > close[-2]
        and vr >= 1.2
    ):
        direction = 'CALL'
        reason = (
            'Experimental lower-band reversal '
            'and volume confirmation'
        )

    elif (
        close[-2] > prev2['upper']
        and close[-1] < current2['upper']
        and close[-1] < close[-2]
        and vr >= 1.2
    ):
        direction = 'PUT'
        reason = (
            'Experimental upper-band rejection '
            'and volume confirmation'
        )

    return {
        'symbol': symbol,
        'timeframe': '1D',
        'date': dates[-1],
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'close': round(close[-1], 4),
        'volume_ratio': round(vr, 2),
        'support': round(min(low[-20:]), 4),
        'resistance': round(max(high[-20:]), 4),
        'bb20_2': current2,
        'bb20_3': current3,
        'signal': direction,
        'reason': reason,
        'status': 'EXPERIMENTAL',
        'note': 'Research only; not an options trade recommendation.'
    }


def tradier_options(symbol):
    """Use Tradier only if its token is configured."""

    if not TRADIER:
        return {
            'status': 'UNAVAILABLE',
            'reason': 'TRADIER_TOKEN not configured'
        }

    headers = {
        'Authorization': 'Bearer ' + TRADIER,
        'Accept': 'application/json'
    }

    base = TRADIER_BASE + '/v1/markets/options/'

    raw = fetch(
        base + 'expirations?'
        + urllib.parse.urlencode({'symbol': symbol}),
        headers
    )

    expirations = raw.get('expirations') or {}
    dates = (
        expirations.get('date', [])
        if isinstance(expirations, dict)
        else []
    )

    if isinstance(dates, str):
        dates = [dates]

    today = datetime.now(timezone.utc).date()

    windows = {
        '14-21': (14, 21),
        '30-45': (30, 45),
        '60-90': (60, 90)
    }

    by_dte = {}

    for label, (start, end) in windows.items():
        candidates = []

        for value in dates:
            try:
                days = (
                    datetime.strptime(value, '%Y-%m-%d').date()
                    - today
                ).days

                if start <= days <= end:
                    candidates.append((days, value))

            except (TypeError, ValueError):
                pass

        if not candidates:
            by_dte[label] = {
                'status': 'UNAVAILABLE',
                'reason': 'No listed expiration in selected DTE window'
            }
            continue

        _, expiration = min(candidates)

        try:
            raw = fetch(
                base + 'chains?'
                + urllib.parse.urlencode({
                    'symbol': symbol,
                    'expiration': expiration,
                    'greeks': 'false'
                }),
                headers
            )

            chain = (raw.get('options') or {}).get('option', [])

            if isinstance(chain, dict):
                chain = [chain]

            items = [
                {
                    'type': x.get('option_type'),
                    'strike': x.get('strike'),
                    'expiration': expiration,
                    'bid': x.get('bid'),
                    'ask': x.get('ask'),
                    'volume': x.get('volume'),
                    'open_interest': x.get('open_interest'),
                    'symbol': x.get('symbol')
                }
                for x in chain
                if isinstance(x, dict)
            ]

            by_dte[label] = {
                'status': 'AVAILABLE' if items else 'UNAVAILABLE',
                'expiration': expiration,
                'contracts': items,
                'source': 'Tradier',
                'updated_at_utc': datetime.now(timezone.utc).isoformat()
            }

        except Exception as exc:
            by_dte[label] = {
                'status': 'UNAVAILABLE',
                'reason': str(exc)[:160]
            }

    return {
        'status': (
            'AVAILABLE'
            if any(
                x.get('status') == 'AVAILABLE'
                for x in by_dte.values()
            )
            else 'UNAVAILABLE'
        ),
        'by_dte': by_dte,
        'source': 'Tradier'
    }


def massive_options(symbol):
    """
    Massive reference contracts endpoint.

    Contract / strike / expiration are actual listed metadata.
    Bid, ask, premium, entry, stop and targets are NOT invented.
    """

    if not MASSIVE:
        return {
            'status': 'UNAVAILABLE',
            'reason': 'MASSIVE_API_KEY not configured'
        }

    today = datetime.now(timezone.utc).date()

    windows = {
        '14-21': (14, 21),
        '30-45': (30, 45),
        '60-90': (60, 90)
    }

    by_dte = {
        label: {
            'status': 'UNAVAILABLE',
            'reason': 'No eligible listed contract returned'
        }
        for label in windows
    }

    contracts_by_window = {
        label: []
        for label in windows
    }

    start_date = (today + timedelta(days=14)).isoformat()
    end_date = (today + timedelta(days=90)).isoformat()

    params = {
        'underlying_ticker': symbol,
        'expiration_date.gte': start_date,
        'expiration_date.lte': end_date,
        'expired': 'false',
        'limit': 1000,
        'sort': 'expiration_date',
        'order': 'asc'
    }

    url = (
        'https://api.massive.com/v3/reference/options/contracts?'
        + urllib.parse.urlencode(params)
    )

    headers = {
        'Authorization': 'Bearer ' + MASSIVE,
        'Accept': 'application/json'
    }

    try:
        pages = 0

        while url and pages < 5:
            raw = fetch(url, headers)
            pages += 1

            for item in raw.get('results', []):
                if not isinstance(item, dict):
                    continue

                expiration = item.get('expiration_date')
                contract_type = item.get('contract_type')

                try:
                    days = (
                        datetime.strptime(
                            expiration, '%Y-%m-%d'
                        ).date() - today
                    ).days
                except (TypeError, ValueError):
                    continue

                if contract_type not in ('call', 'put'):
                    continue

                contract = {
                    'type': contract_type.upper(),
                    'strike': item.get('strike_price'),
                    'expiration': expiration,
                    'symbol': item.get('ticker'),
                    'bid': None,
                    'ask': None,
                    'volume': None,
                    'open_interest': None
                }

                for label, (start, end) in windows.items():
                    if start <= days <= end:
                        contracts_by_window[label].append(contract)

            next_url = raw.get('next_url')

            if not next_url:
                break

            url = next_url

        for label, contracts in contracts_by_window.items():
            if not contracts:
                continue

            expirations = sorted({
                c['expiration']
                for c in contracts
            })

            chosen_expiration = expirations[0]

            selected = [
                c for c in contracts
                if c['expiration'] == chosen_expiration
            ]

            by_dte[label] = {
                'status': 'AVAILABLE',
                'expiration': chosen_expiration,
                'contracts': selected,
                'source': 'Massive Reference',
                'quotes_available': False,
                'updated_at_utc': datetime.now(timezone.utc).isoformat()
            }

        available = any(
            x.get('status') == 'AVAILABLE'
            for x in by_dte.values()
        )

        return {
            'status': 'AVAILABLE' if available else 'UNAVAILABLE',
            'by_dte': by_dte,
            'source': 'Massive Reference',
            'quotes_available': False,
            'updated_at_utc': datetime.now(timezone.utc).isoformat()
        }

    except urllib.error.HTTPError as exc:
        return {
            'status': 'UNAVAILABLE',
            'reason': 'HTTP ' + str(exc.code) + ' from Massive reference API',
            'source': 'Massive Reference'
        }

    except Exception as exc:
        return {
            'status': 'UNAVAILABLE',
            'reason': str(exc)[:160],
            'source': 'Massive Reference'
        }


def options(symbol):
    """Prefer Massive; use Tradier only if available."""

    if MASSIVE:
        result = massive_options(symbol)

        if result.get('status') == 'AVAILABLE':
            return result

        if TRADIER:
            try:
                fallback = tradier_options(symbol)
                if fallback.get('status') == 'AVAILABLE':
                    return fallback
            except Exception:
                pass

        return result

    if TRADIER:
        return tradier_options(symbol)

    return {
        'status': 'UNAVAILABLE',
        'reason': 'No options market-data API key configured'
    }


def main():
    if not AV:
        raise SystemExit('ALPHA_VANTAGE_API_KEY is missing')

    existing = read_json(CACHE, {})

    if not isinstance(existing, dict):
        existing = {}

    state = read_json('.yoyo_cursor.json', {'cursor': 0})
    cursor = int(state.get('cursor', 0)) % len(SYMBOLS)

    subset = [
        SYMBOLS[(cursor + i) % len(SYMBOLS)]
        for i in range(BATCH)
    ]

    updated = 0

    for i, symbol in enumerate(subset):
        try:
            record = stock(symbol)

            try:
                record['options'] = options(symbol)
            except Exception as exc:
                record['options'] = {
                    'status': 'UNAVAILABLE',
                    'reason': str(exc)[:160]
                }

            existing[symbol] = record
            updated += 1

            print(
                symbol,
                record['date'],
                record['signal'],
                record['options'].get('status')
            )

        except Exception as exc:
            print('ERROR', symbol, str(exc)[:160])

        if i < len(subset) - 1:
            time.sleep(DELAY)

    CACHE.write_text(json.dumps(existing, indent=2) + '\n')

    if 'SPY' in existing:
        Path('stock_signal.json').write_text(
            json.dumps(existing['SPY'], indent=2) + '\n'
        )

    Path('.yoyo_cursor.json').write_text(
        json.dumps({
            'cursor': (cursor + BATCH) % len(SYMBOLS)
        }, indent=2) + '\n'
    )

    print(
        'Updated',
        updated,
        'of',
        len(subset),
        'attempted; cached symbols:',
        len(existing)
    )

    if updated == 0:
        raise SystemExit(
            'No market data fetched; existing cache retained'
        )


if __name__ == '__main__':
    main()
