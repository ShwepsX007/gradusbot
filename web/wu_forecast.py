"""Read-only, best-effort WU hourly page reader for the visible overview.

A short-lived real browser is needed because the table is rendered by JavaScript.
No WU data is used by the bot, ensembles or trading. Fail closed on uncertain
station, date, units, timestamps or incomplete forecast coverage.
"""
import copy
import math
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CACHE = {}
_LOCK = threading.Lock()
_BUSY = False
_TTL = 30 * 60
_RETRY = 10 * 60
_MAX_ENTRIES = 64


def _instant(day, clock, zone):
    """Only accept wall-clock times that map to exactly one UTC instant."""
    match = re.fullmatch(r'\s*(1[0-2]|[1-9])\s*:\s*(\d{2})\s*([ap])\.?m\.?\s*', clock, re.I)
    if not match or int(match[2]) > 59:
        return None
    hour = int(match[1]) % 12 + (12 if match[3].lower() == 'p' else 0)
    naive = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=int(match[2]))
    candidates = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(int(utc.timestamp()))
    return next(iter(candidates)) if len(candidates) == 1 else None


def parse_rows(rows, day, tz):
    try:
        zone = ZoneInfo(tz)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return []
    points = {}
    for row in rows[:30]:
        if not isinstance(row, dict):
            continue
        clock, text = row.get('time'), row.get('temperature')
        if not isinstance(clock, str) or not isinstance(text, str):
            continue
        stamp = _instant(day, clock, zone)
        temp_match = re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*°\s*([CF])\s*', text, re.I)
        if stamp is None or not temp_match:
            continue
        number = float(temp_match[1])
        temp = (number - 32) * 5 / 9 if temp_match[2].upper() == 'F' else number
        if not math.isfinite(temp) or not -100 <= temp <= 70 or stamp in points:
            continue
        point = {'time': stamp, 'temp': round(temp, 4)}
        condition = row.get('condition')
        if isinstance(condition, str):
            point['condition'] = condition.strip()[:100]
        precip = row.get('precipitation')
        m = re.fullmatch(r'\s*(\d{1,3})\s*%\s*', precip or '') if isinstance(precip, str) else None
        if m and int(m[1]) <= 100:
            point['precipitation'] = int(m[1])
        points[stamp] = point
    return [points[k] for k in sorted(points)]


def remaining_complete(points, day, tz, now):
    """Require an unbroken forecast through the last hour of these local days."""
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


def _validate_page(data, code, day):
    # Do not accept redirected stations, interstitials, or a stale different date.
    from urllib.parse import urlparse
    url = urlparse(data.get('url', ''))
    if url.hostname != 'www.wunderground.com' or code not in url.path.upper().split('/'):
        raise ValueError('station_mismatch')
    station_url = urlparse(data.get('station', ''))
    if station_url.hostname != 'www.wunderground.com' or code not in station_url.path.upper().split('/'):
        raise ValueError('station_mismatch')
    date_match = re.search(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}\b', data.get('text', ''))
    if not date_match or datetime.strptime(date_match.group().replace(',', ''), '%a %b %d %Y').date() != day:
        raise ValueError('date_mismatch')

def _browser_rows(code, day):
    """Extract only the actual rendered hourly table, never bypass a block."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    binary = shutil.which('chromium') or shutil.which('chromium-browser')
    driver_path = shutil.which('chromedriver')
    if not binary or not driver_path:
        raise RuntimeError('browser_unavailable')
    options = Options()
    options.binary_location = binary
    for flag in ('--headless=new', '--disable-dev-shm-usage', '--lang=en-US'):
        options.add_argument(flag)
    driver = None
    try:
        driver = webdriver.Chrome(service=Service(driver_path), options=options)
        driver.set_page_load_timeout(18)
        driver.get('https://www.wunderground.com/hourly/' + code)
        WebDriverWait(driver, 15).until(lambda d: d.find_elements(By.CSS_SELECTOR, '#hourly-forecast-table tbody tr'))
        data = driver.execute_script('''
          const links=[...document.querySelectorAll('a[href*="/history/daily/"]')];
          const rows=[...document.querySelectorAll('#hourly-forecast-table tbody tr')].map(r=>{
            const text=n=>r.querySelector('.cdk-column-'+n)?.innerText?.trim()||'';
            return {time:text('timeHour'),temperature:text('temperature'),condition:text('condition'),precipitation:text('precipitation')};
          });
          return {url:location.href, station:links[0]?.href||'', text:document.body.innerText.slice(0,12000),rows};
        ''')
        _validate_page(data, code, day)
        return data.get('rows', [])
    finally:
        if driver is not None:
            driver.quit()


def _load(key):
    global _BUSY
    code, tz = key
    result = {'status': 'unavailable', 'points': [], 'timezone': tz}
    try:
        now = time.time()
        day = datetime.fromtimestamp(now, ZoneInfo(tz)).date()
        points = parse_rows(_browser_rows(code, day), day, tz)
        now = time.time()
        if points:
            result = {'status': 'ok', 'points': points, 'timezone': tz, 'day': day.isoformat(),
                      'complete_remaining': remaining_complete(points, day, tz, now), 'fetched_at': int(now)}
    except (ImportError, ModuleNotFoundError):
        result['status'] = 'browser_unavailable'
    except RuntimeError as exc:
        if str(exc) == 'browser_unavailable':
            result['status'] = 'browser_unavailable'
    except ValueError:
        result['status'] = 'page_unrecognized'
    except Exception:
        # Never log WU URLs, page content or browser command lines.
        pass
    finally:
        with _LOCK:
            _CACHE[key] = (time.time(), result)
            while len(_CACHE) > _MAX_ENTRIES:
                _CACHE.pop(next(iter(_CACHE)))
            _BUSY = False


def get(code, tz):
    global _BUSY
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z0-9]{4}', code):
        return {'status': 'unsupported', 'points': []}
    try:
        ZoneInfo(tz)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return {'status': 'no_timezone', 'points': []}
    if os.environ.get('GRADUS_WU_ENABLED', '1') != '1':
        return {'status': 'disabled', 'points': []}
    key = (code, tz)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.time() - cached[0] < (_TTL if cached[1]['status'] == 'ok' else _RETRY):
            return copy.deepcopy(cached[1])
        if _BUSY:
            return {'status': 'loading', 'points': []}
        _BUSY = True
        threading.Thread(target=_load, args=(key,), daemon=True, name='wu-hourly').start()
        return {'status': 'loading', 'points': []}
