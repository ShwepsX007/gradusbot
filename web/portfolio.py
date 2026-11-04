"""Read-only Polymarket portfolio. No trading operations live in this module."""
from web import trading_account as account
import requests

BASE = 'https://data-api.polymarket.com/v2/'
PAGE_LIMIT = 5  # Limit latency; the response explicitly reports truncation.


def _get(path, params):
    response = requests.get(BASE + path, params=params, timeout=(3, 8),
                            headers={'User-Agent': 'gradusbot-web/1.0'})
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, dict) or 'data' not in raw:
        raise account.AccountError('Неверный ответ Data API v2.')
    return raw


def _pages(path, wallet, extra=None):
    params = {'user': wallet, 'limit': 100, **(extra or {})}
    items, cursors = [], set()
    for _ in range(PAGE_LIMIT):
        raw = _get(path, params)
        data, pagination = raw['data'], raw.get('pagination')
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data) or not isinstance(pagination, dict):
            raise account.AccountError('Неверный ответ Data API v2.')
        more, cursor = pagination.get('has_more'), pagination.get('next_cursor')
        if not isinstance(more, bool) or (more and (not isinstance(cursor, str) or not cursor or cursor in cursors)):
            raise account.AccountError('Неверная пагинация Data API v2.')
        items.extend(data)
        if not more:
            return items, False
        cursors.add(cursor)
        params['cursor'] = cursor
    return items, True


def snapshot():
    credentials = account.load()
    if not credentials:
        raise account.AccountError('Сначала настройте аккаунт Polymarket.')
    wallet = credentials['funder'] or credentials['signer']
    result = {'wallet': wallet, 'balance': None, 'orders': None, 'positions': None,
              'activity': None, 'pnl': None, 'value': None, 'errors': [],
              'orders_first_page': True, 'positions_partial': False, 'activity_partial': False}
    try:
        result['balance'] = account._balance(credentials)
    except Exception:
        result['errors'].append('Баланс CLOB недоступен.')
    try:
        result['orders'] = account._orders(credentials)
    except Exception:
        result['errors'].append('Открытые ордера CLOB недоступны.')
    try:
        rows, result['positions_partial'] = _pages('positions', wallet)
        result['positions'] = [{'asset': account._string(p.get('token_id') or p.get('position_id') or p.get('asset_id'), 100),
            'event_slug': account._string(p.get('event_slug')), 'title': account._string(p.get('title')),
            'outcome': account._string(p.get('outcome'), 80), 'size': account._number(p.get('current_size')),
            'value': account._number(p.get('current_value')), 'pnl': account._number(p.get('total_pnl')),
            'redeemable': p.get('redeemable') is True} for p in rows]
    except Exception:
        result['errors'].append('Позиции Data API недоступны.')
    try:
        rows, result['activity_partial'] = _pages('activity', wallet, {'start': 1, 'exclude_deposits_withdrawals': 'false'})
        result['activity'] = [{'event_slug': account._string(p.get('event_slug')), 'title': account._string(p.get('title')),
            'asset': account._string(p.get('token_id') or p.get('asset_id'), 100),
            'outcome': account._string(p.get('outcome'), 80), 'type': account._string(p.get('type'), 40),
            'side': account._string(p.get('side'), 10), 'size': account._number(p.get('size')),
            'value': account._number(p.get('usdc_size')), 'timestamp': account._number(p.get('timestamp') or p.get('block_timestamp'))} for p in rows]
    except Exception:
        result['errors'].append('История Data API недоступна.')
    try:
        data = _get('value', {'user': wallet})['data']
        if not isinstance(data, dict) or account._number(data.get('value')) is None:
            raise ValueError('invalid value')
        result['value'] = account._number(data['value'])
    except Exception:
        result['errors'].append('Стоимость портфеля Data API недоступна.')
    try:
        data = _get('user-pnl', {'user': wallet, 'interval': '1m', 'fidelity': '1d'})['data']
        if not isinstance(data, dict) or not isinstance(data.get('points'), list):
            raise ValueError('invalid pnl')
        result['pnl'] = [{'timestamp': account._number(p.get('timestamp')),
                          'value': account._number(p.get('position_pnl'))} for p in data['points'] if isinstance(p, dict)]
    except Exception:
        result['errors'].append('График PnL Data API недоступен.')
    # Do not return a snapshot for a different account if credentials changed during reads.
    if account.load() != credentials:
        raise account.AccountError('Аккаунт изменился во время загрузки. Обновите портфель.')
    return result
