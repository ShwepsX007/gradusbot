"""Public CLOB price history, separate from executable bid/ask snapshots."""
import math
import time
import requests

PERIODS = {'1h': 1, '6h': 5, '1d': 15, '1w': 60, 'max': 180}
MAX_POINTS = 2000


def fetch_history(token, period):
    try:
        response = requests.get('https://clob.polymarket.com/prices-history',
            params={'market': token, 'interval': period, 'fidelity': PERIODS[period]},
            timeout=(3, 6), headers={'User-Agent': 'gradusbot-web/1.0'})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('history'), list):
            return None
        points = {}
        now = time.time()
        for item in data['history'][-20000:]:
            if not isinstance(item, dict) or isinstance(item.get('t'), bool) or isinstance(item.get('p'), bool):
                continue
            try:
                t, p = float(item['t']), float(item['p'])
                if math.isfinite(t) and math.isfinite(p) and 0 < t <= now + 300 and 0 <= p <= 1:
                    points[t] = p
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
        ordered = [{'t': t, 'p': points[t]} for t in sorted(points)]
        if len(ordered) > MAX_POINTS:
            ordered = [ordered[i * (len(ordered) - 1) // (MAX_POINTS - 1)] for i in range(MAX_POINTS)]
        return {'history': ordered, 'fidelity_minutes': PERIODS[period], 'fetched_at': time.time()}
    except (requests.RequestException, ValueError, TypeError):
        return None
