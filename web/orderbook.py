"""Read-only Polymarket CLOB snapshots. Never infer a YES/NO mapping."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

import requests

MAX_TOKENS = 100  # bound work and upstream traffic for unusually large events
BATCH_SIZE = 50
DEPTH = 5


def _list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
        return value if isinstance(value, list) else []
    return []


def event_tokens(event):
    markets = event.get('markets', []) if isinstance(event, dict) else []
    if not isinstance(markets, list):
        return []
    pairs = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        outcomes = [str(x).strip().lower() for x in _list(market.get('outcomes'))]
        ids = _list(market.get('clobTokenIds'))
        if len(outcomes) != 2 or sorted(outcomes) != ['no', 'yes'] or len(ids) != 2:
            continue
        yes, no = (str(ids[outcomes.index(side)]) for side in ('yes', 'no'))
        if yes == no or not all(re.fullmatch(r'[0-9]{1,80}', x) for x in (yes, no)):
            continue
        pairs.append({'yes': yes, 'no': no})
    return pairs


def _levels(levels, reverse):
    if not isinstance(levels, list):
        raise ValueError('Invalid book levels')
    prices = {}
    for level in levels:
        try:
            price, size = Decimal(str(level['price'])), Decimal(str(level['size']))
            if not price.is_finite() or not size.is_finite() or not 0 <= price <= 1 or size <= 0:
                continue
            prices[price] = prices.get(price, Decimal(0)) + size
        except (TypeError, KeyError, InvalidOperation):
            continue
    return [{'price': str(price), 'size': str(prices[price])} for price in sorted(prices, reverse=reverse)[:DEPTH]]


def _parse_book(data, token):
    if not isinstance(data, dict) or ('asset_id' in data and str(data['asset_id']) != token):
        return None
    if 'bids' not in data or 'asks' not in data:
        return None
    try:
        return {'bids': _levels(data['bids'], True), 'asks': _levels(data['asks'], False),
                'fetched_at': time.time()}
    except ValueError:
        return None


def fetch_book(token):
    """No credentials: return None on failure, distinct from an empty book."""
    try:
        response = requests.get('https://clob.polymarket.com/book', params={'token_id': token},
                                timeout=(3, 4), headers={'User-Agent': 'gradusbot-web/1.0'})
        response.raise_for_status()
        return _parse_book(response.json(), token)
    except (requests.RequestException, ValueError, TypeError):
        return None


def fetch_batch(tokens):
    # Batch avoids one upstream request per outcome under normal conditions.
    empty = dict.fromkeys(tokens)
    try:
        response = requests.post('https://clob.polymarket.com/books',
            json=[{'token_id': token} for token in tokens], timeout=(3, 4),
            headers={'User-Agent': 'gradusbot-web/1.0'})
        if response.status_code in (400, 404):
            # One nonexistent/closed book can reject a batch. Isolate such books.
            with ThreadPoolExecutor(max_workers=8) as pool:
                return dict(zip(tokens, pool.map(fetch_book, tokens)))
        response.raise_for_status()  # do not amplify 429s, timeouts or server errors
        data = response.json()
        if not isinstance(data, list):
            return empty
        for item in data:
            if isinstance(item, dict):
                token = str(item.get('asset_id', ''))
                if token in empty:
                    empty[token] = _parse_book(item, token)
        return empty
    except (requests.RequestException, ValueError, TypeError):
        return empty


def fetch_event_books(event):
    pairs = event_tokens(event)
    tokens = list(dict.fromkeys(token for pair in pairs for token in (pair['yes'], pair['no'])))
    selected = tokens[:MAX_TOKENS]
    batches = [selected[i:i+BATCH_SIZE] for i in range(0, len(selected), BATCH_SIZE)]
    books = {}
    # Parallel batch requests, while falling back to at most 8 requests per batch.
    if batches:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for batch_result in pool.map(fetch_batch, batches):
                books.update(batch_result)
    return {'pairs': pairs, 'books': books, 'limited': len(tokens) > MAX_TOKENS,
            'requested_at': time.time()}
