"""Local, read-only trading companion for the existing Gradus bot."""
import math
import os
import re
import sys
import threading
import time
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
import utils

app = Flask(__name__, static_folder='static')
db.init_db()


def init_web_db():
    # Separate from the bot's history: the web UI must not affect bot strategies.
    with db.get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_history (
            station_id INTEGER NOT NULL, obs_time INTEGER NOT NULL, temp REAL NOT NULL,
            PRIMARY KEY (station_id, obs_time))''')


init_web_db()
_lock = threading.RLock()
_cache = {}


def cached(key, ttl, loader):
    # One in-flight request per process; failures are cached too to avoid retry storms.
    with _lock:
        now = time.time()
        if key in _cache and now - _cache[key][0] < ttl:
            return _cache[key][1]
        try:
            value = loader()
        except Exception:
            app.logger.warning('Upstream request failed: %s', key, exc_info=True)
            value = None
        _cache[key] = (now, value)
        return value


def upstream(url, params=None):
    r = requests.get(url, params=params, timeout=8, headers={'User-Agent': 'gradusbot-web/1.0'})
    r.raise_for_status()
    return r.json()


def station_info(code):
    def load():
        # Share the bot's AWC limiter/backoff in this process.
        if time.time() < utils._AWC_STATE['blocked_until'] or not utils.AWC_LIMITER.allow():
            return None
        r = utils.SESSION.get('https://aviationweather.gov/api/data/stationinfo', params={'ids': code, 'format': 'json'}, timeout=8)
        if r.status_code in (403, 429):
            utils._awc_penalty(r.status_code)
            return None
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else None
    return cached(('station', code), 3600, load)


def forecast(lat, lon):
    return cached(('forecast', lat, lon), 900, lambda: upstream('https://api.open-meteo.com/v1/forecast', {
        'latitude': lat, 'longitude': lon, 'timezone': 'auto', 'past_days': 1, 'forecast_days': 2,
        'hourly': 'temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m,wind_direction_10m',
        'daily': 'temperature_2m_max,temperature_2m_min,sunrise,sunset'}))


def humidity(temp, dew):
    if temp is None or dew is None:
        return None
    try:
        return max(0, min(100, round(100 * math.exp((17.625 * dew / (243.04 + dew)) - (17.625 * temp / (243.04 + temp))))))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


@app.after_request
def security(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.before_request
def guard_mutations():
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        # Block cross-origin browser requests, including simple form submissions.
        origin = request.headers.get('Origin')
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error='Недопустимый источник запроса'), 403
        if not request.is_json:
            return jsonify(error='Требуется JSON'), 415


@app.get('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


def public_station(station):
    # WU api_url may contain an API key; never send database URLs to the browser.
    return {k: station.get(k) for k in ('id', 'name', 'station_code', 'station_type', 'enabled', 'last_temp')}


@app.get('/api/stations')
def stations():
    return jsonify([public_station(s) for s in db.get_stations()])


@app.post('/api/stations')
def add_station():
    data = request.get_json() or {}
    code = str(data.get('code', '')).strip().upper()
    name = str(data.get('name', '')).strip()
    if not re.fullmatch(r'[A-Z0-9]{4}', code) or not name or len(name) > 100:
        return jsonify(error='Укажите название и четырёхзначный ICAO-код'), 400
    if any(s['station_code'] == code and s['station_type'] == 'metar' for s in db.get_stations()):
        return jsonify(error='Станция уже добавлена'), 409
    sid = db.add_station(name, '', '', code, 'metar')
    # New stations remain disabled, consistent with the bot.
    return jsonify(public_station(db.get_station(sid))), 201


@app.patch('/api/stations/<int:sid>')
def toggle_station(sid):
    data = request.get_json() or {}
    if not db.get_station(sid): return jsonify(error='Станция не найдена'), 404
    if not isinstance(data, dict) or not data or set(data) - {'enabled', 'name'}:
        return jsonify(error='Укажите название или статус станции'), 400
    changes = {}
    if 'enabled' in data:
        if not isinstance(data['enabled'], bool): return jsonify(error='Укажите enabled'), 400
        changes['enabled'] = int(data['enabled'])
    if 'name' in data:
        if not isinstance(data['name'], str) or not data['name'].strip() or len(data['name'].strip()) > 100:
            return jsonify(error='Название станции должно содержать от 1 до 100 символов'), 400
        changes['name'] = data['name'].strip()
    db.update_station(sid, **changes)
    return jsonify(public_station(db.get_station(sid)))


def _binding_ids(conn, where, value):
    return [r['id'] for r in conn.execute('SELECT id FROM station_market_bindings WHERE ' + where + '=?', (value,))]


def _delete_binding_settings(conn, ids):
    for bid in ids:
        prefix = f"bind_{bid}_"
        conn.execute("DELETE FROM settings WHERE substr(key,1,?)=?", (len(prefix), prefix))


def _has_position(conn, station_id=None, slugs=()):
    # Conservative guard: local position records only; it cannot see exchange orders.
    for row in conn.execute('SELECT * FROM active_positions'):
        pos = dict(row)
        if pos.get('slug') in slugs or (station_id is not None and str(db.position_meta(pos).get('station_id')) == str(station_id)):
            return True
    return False


@app.delete('/api/stations/<int:sid>')
def remove_station(sid):
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM stations WHERE id=?', (sid,)).fetchone():
            return jsonify(error='Станция не найдена'), 404
        slugs = [r['market_slug'] for r in conn.execute('SELECT market_slug FROM station_market_bindings WHERE station_id=?', (sid,))]
        if _has_position(conn, sid, slugs):
            return jsonify(error='В базе бота есть связанная позиция. Сначала проверьте и закройте её в боте.'), 409
        ids = _binding_ids(conn, 'station_id', sid)
        _delete_binding_settings(conn, ids)
        conn.execute('DELETE FROM station_market_bindings WHERE station_id=?', (sid,))
        conn.execute('DELETE FROM station_history WHERE station_id=?', (sid,))
        conn.execute('DELETE FROM web_metar_history WHERE station_id=?', (sid,))
        prefix = f'st_{sid}_'
        conn.execute('DELETE FROM settings WHERE substr(key,1,?)=?', (len(prefix), prefix))
        conn.execute('DELETE FROM stations WHERE id=?', (sid,))
    return jsonify(ok=True)


def record_web_metar(sid, metar):
    try:
        obs_time = int(metar.get('obs_time'))
        temp = float(metar.get('temp'))
    except (TypeError, ValueError, OverflowError):
        return
    now = time.time()
    if not math.isfinite(temp) or obs_time < now - 48 * 3600 or obs_time > now + 300:
        return
    with db.get_db() as conn:
        # Observations are deduplicated by their source timestamp, not poll time.
        conn.execute('INSERT OR IGNORE INTO web_metar_history (station_id,obs_time,temp) SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM stations WHERE id=? AND enabled=1)', (sid, obs_time, temp, sid))
        conn.execute('DELETE FROM web_metar_history WHERE station_id=? AND obs_time<?', (sid, now - 48 * 3600))


def combined_history(sid):
    points = db.get_station_history(sid)
    with db.get_db() as conn:
        points.extend([[r['obs_time'], r['temp']] for r in conn.execute(
            'SELECT obs_time,temp FROM web_metar_history WHERE station_id=? AND obs_time>?',
            (sid, time.time() - 24 * 3600))])
    return sorted(points, key=lambda p: p[0])


@app.get('/api/stations/<int:sid>/weather')
def weather(sid):
    station = db.get_station(sid)
    if not station: return jsonify(error='Станция не найдена'), 404
    result = {'station': public_station(station), 'history': combined_history(sid), 'metar': None, 'forecast': None, 'info': None}
    if not station['enabled']:
        result['message'] = 'Опрос станции выключен'
        return jsonify(result)
    if station.get('station_type') != 'metar':
        result['message'] = 'В веб-терминале поддерживаются станции METAR'
        return jsonify(result)
    code = station.get('station_code', '').upper()
    if not re.fullmatch(r'[A-Z0-9]{4}', code):
        result['message'] = 'Некорректный ICAO-код'
        return jsonify(result)
    # Additional cache prevents aggressive polling from multiple browser tabs.
    metar = cached(('metar', code), max(60, int(db.get_setting('metar_interval', '60'))), lambda: utils.fetch_metar(code))
    if metar:
        metar = dict(metar)
        metar['humidity'] = humidity(metar.get('temp'), metar.get('dew_point'))
    result['metar'] = metar
    if metar:
        record_web_metar(sid, metar)
        result['history'] = combined_history(sid)
    info = station_info(code)
    result['info'] = info
    if info and info.get('lat') is not None and info.get('lon') is not None:
        result['forecast'] = forecast(info['lat'], info['lon'])
    if not metar: result['message'] = 'METAR временно недоступен'
    return jsonify(result)


@app.get('/api/stations/<int:sid>/bindings')
def bindings(sid):
    if not db.get_station(sid): return jsonify(error='Станция не найдена'), 404
    return jsonify(db.get_bindings(sid))


def valid_slug(value):
    value = str(value).strip()
    if '://' in value:
        u = urlparse(value)
        if u.hostname not in ('polymarket.com', 'www.polymarket.com') or not u.path.startswith('/event/'):
            return None
        value = u.path.split('/')[2]
    return value if re.fullmatch(r'[a-z0-9-]{1,200}', value) else None


@app.post('/api/stations/<int:sid>/bindings')
def add_binding(sid):
    if not db.get_station(sid): return jsonify(error='Станция не найдена'), 404
    slug = valid_slug((request.get_json() or {}).get('slug', ''))
    if not slug: return jsonify(error='Укажите slug или ссылку на событие Polymarket'), 400
    if any(b['market_slug'] == slug for b in db.get_bindings(sid)):
        return jsonify(error='Рынок уже привязан'), 409
    market = cached(('market', slug), 60, lambda: utils.fetch_market(slug))
    if not market: return jsonify(error='Событие не найдено или Polymarket недоступен'), 502
    if not db.get_market_by_slug(slug): db.add_market(market['title'], slug)
    bid = db.add_binding(sid, slug, market['title'])
    return jsonify(db.get_binding(bid)), 201


@app.delete('/api/bindings/<int:bid>')
def delete_binding(bid):
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        binding = conn.execute('SELECT * FROM station_market_bindings WHERE id=?', (bid,)).fetchone()
        if not binding: return jsonify(error='Привязка не найдена'), 404
        if _has_position(conn, slugs=(binding['market_slug'],)):
            return jsonify(error='В базе бота есть позиция на этом рынке. Сначала проверьте и закройте её в боте.'), 409
        _delete_binding_settings(conn, [bid])
        conn.execute('DELETE FROM station_market_bindings WHERE id=?', (bid,))
    return jsonify(ok=True)


@app.get('/api/markets')
def list_markets():
    return jsonify([{k: m.get(k) for k in ('id', 'name', 'slug', 'enabled')} for m in db.get_markets()])


@app.patch('/api/markets/<slug>')
def rename_market(slug):
    if not valid_slug(slug): return jsonify(error='Некорректный slug'), 400
    data = request.get_json() or {}
    name = data.get('name') if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        return jsonify(error='Название рынка должно содержать от 1 до 200 символов'), 400
    name = name.strip()
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM markets WHERE slug=?', (slug,)).fetchone() and not conn.execute('SELECT 1 FROM station_market_bindings WHERE market_slug=?', (slug,)).fetchone():
            return jsonify(error='Рынок не найден'), 404
        conn.execute('UPDATE markets SET name=? WHERE slug=?', (name, slug))
        conn.execute('UPDATE station_market_bindings SET market_name=? WHERE market_slug=?', (name, slug))
    return jsonify(ok=True, name=name)


@app.delete('/api/markets/<slug>')
def remove_market(slug):
    if not valid_slug(slug): return jsonify(error='Некорректный slug'), 400
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        markets = conn.execute('SELECT id FROM markets WHERE slug=?', (slug,)).fetchall()
        ids = _binding_ids(conn, 'market_slug', slug)
        if not markets and not ids: return jsonify(error='Рынок не найден'), 404
        if _has_position(conn, slugs=(slug,)):
            return jsonify(error='В базе бота есть позиция на этом рынке. Сначала проверьте и закройте её в боте.'), 409
        _delete_binding_settings(conn, ids)
        conn.execute('DELETE FROM station_market_bindings WHERE market_slug=?', (slug,))
        for market in markets:
            conn.execute('DELETE FROM market_history WHERE market_id=?', (market['id'],))
        conn.execute('DELETE FROM markets WHERE slug=?', (slug,))
    return jsonify(ok=True)


@app.get('/api/market/<slug>')
def market(slug):
    if not valid_slug(slug): return jsonify(error='Некорректный slug'), 400
    data = cached(('market', slug), 60, lambda: utils.fetch_market(slug))
    if not data: return jsonify(error='Рынок временно недоступен'), 502
    m = db.get_market_by_slug(slug)
    return jsonify(market=data, history=db.get_market_history(m['id']) if m else [], positions=[{k: p.get(k) for k in ('id','is_demo','slug','side','size','sl','tp','entry_price','question','outcome')} for p in db.get_positions() if p.get('slug') == slug])


@app.get('/api/search')
def search():
    q = request.args.get('q', '').strip()
    if len(q) < 2 or len(q) > 100: return jsonify(error='Введите не менее двух символов'), 400
    data = cached(('search', q.lower()), 120, lambda: upstream('https://gamma-api.polymarket.com/public-search', {'q': q, 'limit_per_type': 20, 'events_status': 'active'}))
    if data is None: return jsonify(error='Поиск Polymarket временно недоступен'), 502
    events = data.get('events', []) if isinstance(data, dict) else []
    return jsonify([{'slug': e.get('slug'), 'title': e.get('title')} for e in events if e.get('slug') and e.get('title') and not e.get('closed')])


# Known Polymarket weather station cities. For other stations use the user's
# city name or AWC's station location as a best-effort hint, not as proof.
STATION_CITIES = {
    'KLGA': 'new-york', 'KJFK': 'new-york', 'KEWR': 'new-york',
    'EGLL': 'london', 'EGLC': 'london', 'EGKK': 'london',
    'LFPG': 'paris', 'LFPB': 'paris', 'LFPO': 'paris',
    'RKSI': 'seoul', 'RKSS': 'seoul', 'RJTT': 'tokyo', 'RJAA': 'tokyo',
    'RCSS': 'taipei', 'RCTP': 'taipei', 'KORD': 'chicago', 'KMDW': 'chicago',
    'KMIA': 'miami', 'KATL': 'atlanta', 'KSEA': 'seattle', 'KLAX': 'los-angeles',
    'LTAC': 'ankara', 'LTFM': 'istanbul', 'UUEE': 'moscow',
}
CITY_ALIASES = {'нью-йорк': 'new-york', 'ньюйорк': 'new-york', 'лондон': 'london',
                'париж': 'paris', 'анкара': 'ankara', 'сеул': 'seoul',
                'токио': 'tokyo', 'майами': 'miami', 'чикаго': 'chicago',
                'москва': 'moscow', 'стамбул': 'istanbul', 'сиэтл': 'seattle'}


def station_city(station):
    code = (station.get('station_code') or '').upper()
    if code in STATION_CITIES: return STATION_CITIES[code]
    name = (station.get('name') or '').strip().lower()
    if name in CITY_ALIASES: return CITY_ALIASES[name]
    # A label entered by the user can be a city. Avoid network calls to AWC
    # for a disabled station and do not infer a city from arbitrary URLs.
    if re.fullmatch(r'[a-z]+(?:[ -][a-z]+)*', name): return name.replace(' ', '-')
    return None


@app.get('/api/stations/<int:sid>/market-search')
def search_station_markets(sid):
    station = db.get_station(sid)
    if not station: return jsonify(error='Станция не найдена'), 404
    city = station_city(station)
    if not city:
        return jsonify(city=None, events=[], message='Город автоматически не определён. Используйте ручной поиск по-английски.')
    query = 'temperature in ' + city.replace('-', ' ')
    data = cached(('search', query), 120, lambda: upstream('https://gamma-api.polymarket.com/public-search', {
        'q': query, 'limit_per_type': 50, 'events_status': 'active'}))
    if data is None: return jsonify(error='Поиск Polymarket временно недоступен'), 502
    events = data.get('events', []) if isinstance(data, dict) else []
    prefix = re.compile(r'^(?:highest|lowest)-temperature-in-' + re.escape(city) + r'-on-')
    found = [{'slug': e['slug'], 'title': e['title']} for e in events
             if isinstance(e, dict) and e.get('title') and isinstance(e.get('slug'), str)
             and prefix.match(e['slug']) and not e.get('closed')]
    return jsonify(city=city.replace('-', ' '), events=found)


SETTINGS = {'metar_interval': (60, 3600), 'awc_rate_per_min': (1, 60), 'm_interval': (30, 3600)}
@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        data = request.get_json() or {}
        values = {}
        for key, (low, high) in SETTINGS.items():
            try: v = int(data[key])
            except (KeyError, ValueError, TypeError): return jsonify(error=f'Некорректное значение {key}'), 400
            if not low <= v <= high: return jsonify(error=f'{key}: допустимо {low}–{high}'), 400
            values[key] = v
        units = data.get('units', db.get_setting('units', 'C'))
        if units not in ('C', 'F', 'B'): return jsonify(error='Выберите °C, °F или обе единицы'), 400
        values['units'] = units
        with db.get_db() as conn:
            for key, value in values.items():
                conn.execute('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', (key, str(value)))
    result = {key: db.get_setting(key, str(bounds[0])) for key, bounds in SETTINGS.items()}
    result['units'] = db.get_setting('units', 'C')
    return jsonify(result)


if __name__ == '__main__':
    app.run(host=os.environ.get('WEB_HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')), threaded=True)
