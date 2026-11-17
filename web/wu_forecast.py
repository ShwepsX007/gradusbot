"""Read-only Weather Company hourly forecast for the visible overview.

Keys are stored separately and are never sent to the client or logged. One
in-flight request per process; errors never affect METAR, ensembles or trades.
"""
import copy
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from web import wu_config

_CACHE = {}
_NEXT_KEY = {}
_REQUESTED = {}
_LOCK = threading.Lock()
_BUSY = False
_GENERATION = 0
_BLOCKED_UNTIL = 0
_NEXT_REQUEST = 0
_TTL = 30 * 60
_RETRY = 10 * 60
_MAX_ENTRIES = 64
_MAX_BYTES = 512 * 1024
_URL = 'https://api.weather.com/v3/wx/forecast/hourly/2day'


def invalidate():
    """Do not publish an in-flight result after a key has been changed."""
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        _CACHE.clear()
        _NEXT_KEY.clear()
        _REQUESTED.clear()


def parse_api(data, now):
    if not isinstance(data, dict):
        return []
    stamps, temperatures = data.get('validTimeUtc'), data.get('temperature')
    if not isinstance(stamps, list) or not isinstance(temperatures, list) or len(stamps) != len(temperatures) or len(stamps) > 100:
        return []
    precip, phrases = data.get('precipChance'), data.get('wxPhraseLong')
    points = {}
    for i, (stamp, temp) in enumerate(zip(stamps, temperatures)):
        if (isinstance(stamp, bool) or not isinstance(stamp, int) or not now-86400 <= stamp <= now+3*86400
                or isinstance(temp, bool) or not isinstance(temp, (int, float)) or not math.isfinite(temp) or not -100 <= temp <= 70):
            continue
        if stamp in points:
            return []
        p = {'time': stamp, 'temp': float(temp)}
        if isinstance(precip, list) and i < len(precip):
            value = precip[i]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100:
                p['precipitation'] = value
        if isinstance(phrases, list) and i < len(phrases) and isinstance(phrases[i], str):
            p['condition'] = phrases[i].strip()[:100]
        points[stamp] = p
    return [points[k] for k in sorted(points)]


def remaining_complete(points, day, tz, now):
    zone = ZoneInfo(tz)
    local_now = datetime.fromtimestamp(now, zone)
    if local_now.date() != day:
        return False
    start = datetime.combine(day, datetime.min.time(), zone).timestamp()
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).timestamp()
    remaining = [p['time'] for p in points if now <= p['time'] < end]
    if not remaining or remaining[0] - now > 3600 or end - remaining[-1] > 3600:
        return False
    return now >= start and all(b-a == 3600 for a, b in zip(remaining, remaining[1:]))


def _fetch(code, key):
    """Make exactly one request; never log a URL containing the key."""
    try:
        with requests.get(_URL, params={'icaoCode': code, 'units': 'm', 'language': 'ru-RU', 'format': 'json', 'apiKey': key},
                          timeout=(5, 15), allow_redirects=False, stream=True) as response:
            status = response.status_code
            if status == 429:
                now = time.time()
                raw = response.headers.get('Retry-After', '')
                try:
                    until = now + max(0, int(raw))
                except (ValueError, TypeError):
                    try:
                        date_value = parsedate_to_datetime(raw)
                        until = date_value.timestamp() if date_value.tzinfo else 0
                    except (ValueError, TypeError, OverflowError):
                        until = 0
                return 'throttled', [], max(now + _TTL, until)
            if status in (401, 403):
                return 'access_denied', [], 0
            if status != 200:
                return 'unavailable', [], 0
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > _MAX_BYTES:
                    return 'invalid_response', [], 0
                chunks.append(chunk)
            points = parse_api(json.loads(b''.join(chunks)), time.time())
            return ('ok' if points else 'invalid_response'), points, 0
    except Exception:
        # requests exceptions include URLs containing API keys: do not log them.
        return 'unavailable', [], 0

def _load(cache_key, keys, generation, key_index):
    global _BUSY, _BLOCKED_UNTIL, _NEXT_REQUEST
    code, tz = cache_key
    result = {'status': 'unavailable', 'points': [], 'timezone': tz}
    blocked_until = 0
    try:
        with _LOCK:
            _NEXT_REQUEST = time.monotonic() + 20
        status, points, blocked_until = _fetch(code, keys[key_index])
        result['status'] = 'waiting' if status == 'access_denied' and key_index + 1 < len(keys) else status
        if status == 'ok':
            now = time.time()
            day = datetime.fromtimestamp(now, ZoneInfo(tz)).date()
            result.update(points=points, day=day.isoformat(),
                          complete_remaining=remaining_complete(points, day, tz, now), fetched_at=int(now))
    except Exception:
        pass
    finally:
        with _LOCK:
            _BLOCKED_UNTIL = max(_BLOCKED_UNTIL, blocked_until)
            if generation == _GENERATION:
                if result['status'] == 'waiting':
                    _NEXT_KEY[cache_key] = key_index + 1
                else:
                    _NEXT_KEY.pop(cache_key, None)
                _CACHE[cache_key] = (time.time(), result)
                while len(_CACHE) > _MAX_ENTRIES:
                    expired_key = next(iter(_CACHE))
                    _CACHE.pop(expired_key)
                    _NEXT_KEY.pop(expired_key, None)
            _BUSY = False


def get(code, tz):
    global _BUSY, _NEXT_REQUEST
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z0-9]{4}', code):
        return {'status': 'unsupported', 'points': []}
    try:
        ZoneInfo(tz)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return {'status': 'no_timezone', 'points': []}
    if os.environ.get('GRADUS_WU_ENABLED', '1') != '1':
        return {'status': 'disabled', 'points': []}
    cache_key = (code, tz)
    with _LOCK:
        try:
            keys = wu_config.private_keys()
        except Exception:
            return {'status': 'config_error', 'points': []}
        if not keys:
            return {'status': 'no_key', 'points': []}
        _REQUESTED[cache_key] = time.monotonic()
        while len(_REQUESTED) > _MAX_ENTRIES:
            _REQUESTED.pop(next(iter(_REQUESTED)))
        if time.time() < _BLOCKED_UNTIL:
            return {'status': 'throttled', 'points': []}
        cached = _CACHE.get(cache_key)
        if cached and cached[1]['status'] != 'waiting' and time.time() - cached[0] < (_TTL if cached[1]['status'] == 'ok' else _RETRY):
            return copy.deepcopy(cached[1])
        if _BUSY:
            return {'status': 'loading', 'points': []}
        if time.monotonic() < _NEXT_REQUEST:
            return {'status': 'waiting', 'points': []}
        _BUSY = True
        try:
            threading.Thread(target=_load, args=(cache_key, keys, _GENERATION, min(_NEXT_KEY.get(cache_key, 0), len(keys)-1)), daemon=True, name='wu-hourly').start()
        except Exception:
            _BUSY = False
            return {'status': 'unavailable', 'points': []}
        return {'status': 'loading', 'points': []}


def peek(code, tz, now=None):
    """Return an already-fetched, fresh snapshot; never issue network requests."""
    now = time.time() if now is None else now
    with _LOCK:
        entry = _CACHE.get((code, tz))
        if not entry or entry[1].get('status') != 'ok' or not 0 <= now-entry[0] < _TTL:
            return None
        return copy.deepcopy(entry[1])


def poll_requested(code, tz):
    """Only poll a station/zone previously requested by a valid overview."""
    with _LOCK:
        requested = _REQUESTED.get((code, tz))
        if requested is None or not 0 <= time.monotonic()-requested <= 300:
            return {'status': 'not_loaded', 'points': []}
    return get(code, tz)
