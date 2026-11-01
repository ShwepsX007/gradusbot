"""Discovery helpers for Polymarket's temperature category (no writes)."""
import html
import json
import math
import re
from datetime import date
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from web.market_station import identify_station, resolution_fields, URL_RE

MONTHS = {name: n for n, name in enumerate(('january','february','march','april','may','june',
                                            'july','august','september','october','november','december'), 1)}
DAILY = re.compile(r'^(?:highest|lowest)-temperature-in-.+?-on-(' + '|'.join(MONTHS) + r')-(\d{1,2})-(\d{4})$')
SLUG = re.compile(r'[a-z0-9-]{1,200}')


def observation_date(slug):
    match = DAILY.fullmatch(slug or '')
    if not match:
        return None
    try:
        return date(int(match[3]), MONTHS[match[1]], int(match[2])).isoformat()
    except ValueError:
        return None


def is_hong_kong_observatory(event):
    # An explicit official source is enough for its time zone, not for a METAR station.
    for _, text in resolution_fields(event):
        for match in URL_RE.finditer(html.unescape(text)):
            try:
                url = urlparse(match.group(0).rstrip('.,);]'))
                if (url.scheme == 'https' and url.hostname in ('weather.gov.hk','www.weather.gov.hk')
                    and url.username is None and url.password is None and url.port in (None, 443)):
                    return True
            except ValueError:
                pass
    return False



def _array(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, list) else None


def yes_price_summary(markets):
    """Extract explicit YES prices, never guessing outcome ordering or a tradable price."""
    quotes = []
    for market in markets:
        if (not isinstance(market, dict) or market.get('closed') is True or
            market.get('active') is False or market.get('acceptingOrders') is False):
            continue
        outcomes, prices = _array(market.get('outcomes')), _array(market.get('outcomePrices'))
        if not outcomes or not prices or len(outcomes) != 2 or len(prices) != 2:
            continue
        if not all(isinstance(x, str) for x in outcomes):
            continue
        normalized = [x.strip().lower() for x in outcomes]
        if sorted(normalized) != ['no', 'yes']:
            continue
        raw = prices[normalized.index('yes')]
        if isinstance(raw, bool) or raw is None or not isinstance(raw, (str, int, float)):
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(price) or not 0 <= price <= 1:
            continue
        label = market.get('groupItemTitle')
        if not isinstance(label, str) or not label.strip():
            label = market.get('question')
        if not isinstance(label, str) or not label.strip():
            continue
        label = label.strip()
        if len(label) > 180:
            label = label[:179] + '…'
        quotes.append({'label':label, 'price':price,
                       'accepting_orders':market.get('acceptingOrders') is True})
    quotes.sort(key=lambda quote: (-quote['price'], quote['label'].casefold()))
    positive = [quote for quote in quotes if quote['price'] > 0]
    # Highest decimal multiplier is the *lowest* nonzero YES price, not the highest percentage.
    maximum = min(positive, key=lambda quote: (quote['price'], quote['label'].casefold())) if positive else None
    return quotes[:3], maximum

def event_summary(event):
    if not isinstance(event, dict) or event.get('closed') is True or event.get('active') is False:
        return None
    slug, title = event.get('slug'), event.get('title')
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or not isinstance(title, str) or not title.strip():
        return None
    resolution = identify_station(event)
    candidate = resolution['candidate']
    timezone, basis = None, None
    if (resolution['status'] == 'unknown' and observation_date(slug) and
        re.match(r'^(?:highest|lowest)-temperature-in-hong-kong-on-', slug) and
        is_hong_kong_observatory(event)):
        timezone, basis = 'Asia/Hong_Kong', 'Hong Kong Observatory (часовой пояс источника; не METAR)'
    markets = event.get('markets') if isinstance(event.get('markets'), list) else []
    active = [m for m in markets if isinstance(m, dict) and m.get('closed') is not True and m.get('active') is not False]
    suspended = bool(active) and all(m.get('acceptingOrders') is False for m in active)
    top_quotes, max_odds_quote = yes_price_summary(active)
    return {'slug':slug, 'title':title, 'date':observation_date(slug), 'station':resolution,
            'timezone':timezone, 'timezone_basis':basis, 'orders_suspended':suspended,
            'station_code':candidate['code'] if candidate else None,
            'top_yes_prices':top_quotes, 'max_odds_yes':max_odds_quote}


def valid_zone(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        return False
    try:
        ZoneInfo(value)
        return True
    except (ValueError, ZoneInfoNotFoundError):
        return False


def apply_timezone_batch(codes, data):
    """Open-Meteo location_id indexes submitted coordinates; reject inconsistent batches."""
    if isinstance(data, dict) and len(codes) == 1:
        data = [data]
    if not isinstance(data, list) or len(data) != len(codes):
        return {}
    found = {}
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            return {}
        location_id = item.get('location_id')
        if (index == 0 and location_id not in (None, 0)) or (index > 0 and location_id != index):
            return {}
        tz = item.get('timezone')
        if valid_zone(tz):
            found[codes[index]] = tz
    return found
