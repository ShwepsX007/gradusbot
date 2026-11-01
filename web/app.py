"""Local, read-only trading companion for the existing Gradus bot."""
import math
import hashlib
import hmac
import secrets
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory, render_template, redirect, session, url_for
import requests
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
import utils
from web.weather import cloud_description, precipitation_description
from web.market_station import identify_station
from web.orderbook import fetch_event_books
from web.temperature_scan import event_summary, apply_timezone_batch
from collections import OrderedDict

app = Flask(__name__, static_folder='static')
# Authentication is fail-closed; a missing or weak secret never enables the panel.
_auth_secret = os.environ.get('GRADUS_SESSION_SECRET', '')
app.config.update(
    SECRET_KEY=_auth_secret or secrets.token_urlsafe(48),
    WEB_USERNAME=os.environ.get('GRADUS_USERNAME', ''),
    WEB_PASSWORD_HASH=os.environ.get('GRADUS_PASSWORD_HASH', ''),
    WEB_AUTH_CONFIGURED=bool(len(_auth_secret) >= 32 and os.environ.get('GRADUS_USERNAME') and os.environ.get('GRADUS_PASSWORD_HASH')),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_REFRESH_EACH_REQUEST=False,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('WEB_COOKIE_SECURE', '1') != '0',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=1024 * 1024,
    WEB_TUNNEL_ONLY=os.environ.get('WEB_TUNNEL_ONLY') == '1',
    WEB_TRUST_PROXY=os.environ.get('WEB_TRUST_PROXY') == '1',
)
# Enable ONLY when a trusted reverse proxy is the sole entry to Gunicorn.
if os.environ.get('WEB_TRUST_PROXY') == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
db.init_db()


def init_web_db():
    # Separate from the bot's history: the web UI must not affect bot strategies.
    with db.get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_history (
            station_id INTEGER NOT NULL, obs_time INTEGER NOT NULL, temp REAL NOT NULL,
            PRIMARY KEY (station_id, obs_time))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_station_solar (
            station_id INTEGER NOT NULL, day TEXT NOT NULL, sunrise TEXT, sunset TEXT, timezone TEXT NOT NULL,
            PRIMARY KEY (station_id, day))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_auth_password (
            id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL)''')


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
        'latitude': lat, 'longitude': lon, 'timezone': 'auto', 'past_days': 6, 'forecast_days': 2,
        'hourly': 'temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m,wind_direction_10m,precipitation_probability',
        'daily': 'temperature_2m_max,temperature_2m_min,sunrise,sunset'}))


def humidity(temp, dew):
    if temp is None or dew is None:
        return None
    try:
        return max(0, min(100, round(100 * math.exp((17.625 * dew / (243.04 + dew)) - (17.625 * temp / (243.04 + temp))))))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


_login_lock = threading.Lock()
_login_attempts = {}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_ATTEMPTS = 5


def current_password_hash():
    with db.get_db() as conn:
        row = conn.execute('SELECT password_hash FROM web_auth_password WHERE id=1').fetchone()
    return row['password_hash'] if row else app.config['WEB_PASSWORD_HASH']


def auth_version():
    material = app.config['WEB_USERNAME'] + '\0' + current_password_hash()
    return hashlib.sha256(material.encode()).hexdigest()


def is_authenticated():
    return session.get('auth_user') == app.config['WEB_USERNAME'] and hmac.compare_digest(str(session.get('auth_version', '')), auth_version())


def csrf_token():
    token = session.get('csrf')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf'] = token
    return token


def login_allowed(address):
    now = time.monotonic()
    with _login_lock:
        # Bound memory even under distributed scans; this limit is per process.
        if len(_login_attempts) >= 4096:
            for key in list(_login_attempts):
                if not any(now - t < LOGIN_WINDOW_SECONDS for t in _login_attempts[key]):
                    del _login_attempts[key]
        if address not in _login_attempts and len(_login_attempts) >= 4096:
            return False
        hits = [t for t in _login_attempts.get(address, []) if now - t < LOGIN_WINDOW_SECONDS]
        if len(hits) >= LOGIN_ATTEMPTS:
            _login_attempts[address] = hits
            return False
        hits.append(now)
        _login_attempts[address] = hits
        return True


def origin_ok():
    origin = request.headers.get('Origin')
    if not origin:
        return True  # Non-browser clients still need the unpredictable CSRF token.
    try:
        parsed = urlparse(origin)
        return parsed.scheme == request.scheme and parsed.netloc == request.host
    except ValueError:
        return False


def trusted_ssh_tunnel():
    # Gunicorn sets SERVER_NAME from the listener's bound address, not the Host
    # header. Refuse other WSGI servers and reverse proxies rather than trusting
    # client-supplied forwarding headers. This requires a loopback-only listener.
    return (app.config['WEB_TUNNEL_ONLY'] and not app.config['WEB_TRUST_PROXY']
            and request.environ.get('gunicorn.socket') is not None
            and request.environ.get('SERVER_NAME') == '127.0.0.1'
            and request.remote_addr == '127.0.0.1'
            and re.fullmatch(r'127\.0\.0\.1(?::[0-9]{1,5})?', request.host or '') is not None)


@app.after_request
def security(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@app.before_request
def access_control():
    if app.config['WEB_TUNNEL_ONLY'] and not trusted_ssh_tunnel():
        return 'Доступ разрешён только через SSH-туннель к локальному Gunicorn.', 403
    if not app.config['WEB_AUTH_CONFIGURED']:
        return 'Вход не настроен. Настройте GRADUS_USERNAME, GRADUS_PASSWORD_HASH и GRADUS_SESSION_SECRET на сервере.', 503
    public = request.endpoint == 'login' or request.path == '/static/login.css'
    if not public and not is_authenticated():
        if request.path.startswith('/api/'):
            return jsonify(error='Требуется вход'), 401
        return redirect(url_for('login'))
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not origin_ok():
            return jsonify(error='Недопустимый источник запроса'), 403
        if request.endpoint == 'login':
            supplied = request.form.get('csrf_token', '')
        else:
            if not request.is_json:
                return jsonify(error='Требуется JSON'), 415
            supplied = request.headers.get('X-CSRF-Token', '')
        expected = session.get('csrf', '')
        if not expected or not supplied or not hmac.compare_digest(str(expected).encode('utf-8'), str(supplied).encode('utf-8')):
            return jsonify(error='Проверка безопасности не пройдена. Обновите страницу.'), 403


@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_authenticated():
        return redirect(url_for('index'))
    error = None
    status = 200
    if request.method == 'POST':
        address = request.remote_addr or 'unknown'
        if not login_allowed(address):
            error, status = 'Слишком много попыток. Попробуйте через 15 минут.', 429
        else:
            username = request.form.get('username', '')
            password = request.form.get('password', '')
            # Verify the hash even for an incorrect username to reduce timing leakage.
            try:
                password_ok = check_password_hash(current_password_hash(), password[:256])
            except (ValueError, TypeError):
                password_ok = False
            if hmac.compare_digest(username.encode('utf-8'), app.config['WEB_USERNAME'].encode('utf-8')) and password_ok and len(password) <= 256:
                with _login_lock:
                    _login_attempts.pop(address, None)
                session.clear()
                session.permanent = True
                session['auth_user'] = app.config['WEB_USERNAME']
                session['auth_version'] = auth_version()
                csrf_token()
                return redirect(url_for('index'))
            error, status = 'Неверный логин или пароль.', 401
    return render_template('login.html', csrf=csrf_token(), error=error, insecure=not request.is_secure and not trusted_ssh_tunnel(), tunneled=trusted_ssh_tunnel()), status


@app.get('/api/session')
def session_info():
    return jsonify(csrf=csrf_token())


@app.get('/api/security-info')
def security_info():
    return jsonify(tunnel=bool(trusted_ssh_tunnel()), trading_enabled=False)


@app.post('/api/change-password')
def change_password():
    if not trusted_ssh_tunnel():
        return jsonify(error='Смена пароля доступна только через настроенный SSH-туннель.'), 403
    address = 'password:' + (request.remote_addr or 'unknown')
    if not login_allowed(address):
        return jsonify(error='Слишком много попыток. Повторите через 15 минут.'), 429
    data = request.get_json(silent=True)
    if not isinstance(data, dict): return jsonify(error='Некорректный запрос.'), 400
    old, new = data.get('current_password'), data.get('new_password')
    if not isinstance(old, str) or not isinstance(new, str) or not 16 <= len(new) <= 256 or len(old) > 256:
        return jsonify(error='Пароль должен содержать от 16 до 256 символов.'), 400
    old_hash = current_password_hash()
    try:
        valid = check_password_hash(old_hash, old)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        return jsonify(error='Неверный текущий пароль.'), 400
    new_hash = generate_password_hash(new)
    # Prevent concurrent requests, validated against the old password, from
    # overwriting a newly changed password.
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT password_hash FROM web_auth_password WHERE id=1').fetchone()
        current = row['password_hash'] if row else app.config['WEB_PASSWORD_HASH']
        if not hmac.compare_digest(current, old_hash):
            return jsonify(error='Пароль уже изменился. Войдите заново.'), 409
        conn.execute('INSERT OR REPLACE INTO web_auth_password(id,password_hash) VALUES (1,?)', (new_hash,))
    with _login_lock: _login_attempts.pop(address, None)
    session.clear()
    return jsonify(ok=True, message='Пароль изменён. Войдите заново на всех устройствах.')


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify(ok=True)


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
        conn.execute('DELETE FROM web_station_solar WHERE station_id=?', (sid,))
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
    if not math.isfinite(temp) or obs_time < now - 8 * 24 * 3600 or obs_time > now + 300:
        return
    with db.get_db() as conn:
        # Deduplicate repeated polls, but retain a corrected report for the same observation time.
        conn.execute('''INSERT INTO web_metar_history (station_id,obs_time,temp)
            SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM stations WHERE id=? AND enabled=1)
            ON CONFLICT(station_id,obs_time) DO UPDATE SET temp=excluded.temp''', (sid, obs_time, temp, sid))
        conn.execute('DELETE FROM web_metar_history WHERE station_id=? AND obs_time<?', (sid, now - 8 * 24 * 3600))


def combined_history(sid):
    points = db.get_station_history(sid, hours=8 * 24)
    with db.get_db() as conn:
        points.extend([[r['obs_time'], r['temp']] for r in conn.execute(
            'SELECT obs_time,temp FROM web_metar_history WHERE station_id=? AND obs_time>?',
            (sid, time.time() - 8 * 24 * 3600))])
    return sorted(points, key=lambda p: p[0])


def metar_chart_history(sid):
    # Bot history stores poll times, not METAR observation times; do not mix them.
    with db.get_db() as conn:
        return [[r['obs_time'], r['temp']] for r in conn.execute(
            'SELECT obs_time,temp FROM web_metar_history WHERE station_id=? AND obs_time>=? ORDER BY obs_time',
            (sid, time.time() - 24 * 3600))]


def save_solar_days(sid, forecast_data):
    if not isinstance(forecast_data, dict): return
    tz = forecast_data.get('timezone')
    daily = forecast_data.get('daily') or {}
    if not isinstance(tz, str) or not tz or not isinstance(daily, dict): return
    dates, rises, sets = daily.get('time'), daily.get('sunrise'), daily.get('sunset')
    if not all(isinstance(x, list) for x in (dates, rises, sets)): return
    rows = []
    for day, rise, sunset in zip(dates, rises, sets):
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day): continue
        # Preserve dates even when polar conditions have no sunrise or sunset.
        if rise is not None and (not isinstance(rise, str) or not rise.startswith(day + 'T')): continue
        if sunset is not None and (not isinstance(sunset, str) or not sunset.startswith(day + 'T')): continue
        rows.append((sid, day, rise, sunset, tz, sid))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
    with db.get_db() as conn:
        for row in rows:
            conn.execute("""INSERT OR REPLACE INTO web_station_solar (station_id,day,sunrise,sunset,timezone)
                SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM stations WHERE id=? AND enabled=1)""", row)
        conn.execute('DELETE FROM web_station_solar WHERE station_id=? AND day<?', (sid, cutoff))


def get_solar_days(sid):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute('SELECT day,sunrise,sunset,timezone FROM web_station_solar WHERE station_id=? AND day>=? ORDER BY day', (sid, cutoff))]


@app.get('/api/stations/<int:sid>/weather')
def weather(sid):
    station = db.get_station(sid)
    if not station: return jsonify(error='Станция не найдена'), 404
    result = {'station': public_station(station), 'history': combined_history(sid), 'metar_history': metar_chart_history(sid), 'solar_days': get_solar_days(sid), 'metar': None, 'forecast': None, 'info': None}
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
        metar['cloud'] = cloud_description(metar.get('raw_metar'), metar.get('wx_phrase'))
        metar['precipitation'] = precipitation_description(metar.get('raw_metar'))
        try:
            wind = float(metar.get('wind_speed'))
            metar['wind_ms'] = round(wind * 0.514444, 1) if math.isfinite(wind) and wind >= 0 else None
        except (TypeError, ValueError):
            metar['wind_ms'] = None
    result['metar'] = metar
    if metar:
        record_web_metar(sid, metar)
        result['history'] = combined_history(sid)
        result['metar_history'] = metar_chart_history(sid)
    info = station_info(code)
    result['info'] = info
    if info and info.get('lat') is not None and info.get('lon') is not None:
        result['forecast'] = forecast(info['lat'], info['lon'])
        save_solar_days(sid, result['forecast'])
        result['solar_days'] = get_solar_days(sid)
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


def event_for_station(slug, fresh=False):
    def load():
        data = upstream('https://gamma-api.polymarket.com/events/slug/' + slug)
        if isinstance(data, list):
            data = data[0] if data else None
        return data if isinstance(data, dict) and isinstance(data.get('title'), str) and data['title'].strip() else None
    if fresh:
        try: return load()
        except Exception:
            app.logger.warning('Failed to refresh market rules for %s', slug, exc_info=True)
            return None
    return cached(('event-rules', slug), 120, load)


def market_input():
    data = request.get_json() or {}
    return data if isinstance(data, dict) else {}


def market_resolution(slug):
    event = event_for_station(slug, fresh=True)
    if not event:
        return None, None
    result = identify_station(event)
    result.update({'slug': slug, 'title': event['title']})
    candidate = result['candidate']
    result['existing_stations'] = [public_station(s) for s in db.get_stations()
        if candidate and s.get('station_type') == 'metar' and (s.get('station_code') or '').strip().upper() == candidate['code']]
    return event, result


@app.post('/api/market-station/resolve')
def resolve_market_station():
    slug = valid_slug(market_input().get('slug', ''))
    if not slug: return jsonify(error='Укажите slug или ссылку на событие Polymarket'), 400
    _, result = market_resolution(slug)
    if result is None: return jsonify(error='Не удалось загрузить правила события из Polymarket'), 502
    return jsonify(result)


def city_label(slug, code):
    match = re.search(r'(?:highest|lowest)-temperature-in-(.+?)-on-', slug)
    city = match.group(1).replace('-', ' ').title() if match else ''
    return (city[:80] if city else code)


@app.post('/api/market-station/add')
def add_market_station():
    data = market_input()
    slug = valid_slug(data.get('slug', ''))
    if not slug: return jsonify(error='Укажите ссылку на событие'), 400
    if data.get('confirm') is not True: return jsonify(error='Подтвердите источник и станцию'), 400
    event, result = market_resolution(slug)
    if result is None: return jsonify(error='Не удалось загрузить правила события'), 502
    if result['status'] != 'found' or data.get('code') != result['candidate']['code']:
        return jsonify(error='Источник не подтверждён или станция изменилась. Проверьте правила заново.'), 409
    code = result['candidate']['code']
    # Never turn on a newly discovered station or strategy. Atomic reuse/add avoids duplicates.
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        station = conn.execute("SELECT * FROM stations WHERE station_type='metar' AND UPPER(TRIM(station_code))=? ORDER BY id LIMIT 1", (code,)).fetchone()
        created = station is None
        if created:
            sid = conn.execute("INSERT INTO stations (name,api_url,wu_url,station_code,station_type,enabled) VALUES (?,?,?,?,?,0)",
                (city_label(slug, code), '', '', code, 'metar')).lastrowid
            station = conn.execute('SELECT * FROM stations WHERE id=?', (sid,)).fetchone()
        sid = station['id']
        market = conn.execute('SELECT * FROM markets WHERE slug=? ORDER BY id LIMIT 1', (slug,)).fetchone()
        name = market['name'] if market else event['title']
        if not market:
            conn.execute("INSERT INTO markets (name,slug,enabled,last_probs) VALUES (?,?,1,'{}')", (name, slug))
        binding = conn.execute('SELECT id FROM station_market_bindings WHERE station_id=? AND market_slug=? LIMIT 1', (sid, slug)).fetchone()
        already_bound = binding is not None
        if not binding:
            conn.execute('INSERT INTO station_market_bindings (station_id,market_slug,market_name) VALUES (?,?,?)', (sid, slug, name))
    return jsonify(station=public_station(dict(station)), created_station=created, already_bound=already_bound, slug=slug)


@app.post('/api/markets')
def save_market_without_station():
    slug = valid_slug(market_input().get('slug', ''))
    if not slug: return jsonify(error='Укажите ссылку на событие'), 400
    event = event_for_station(slug)
    if not event: return jsonify(error='Не удалось загрузить событие Polymarket'), 502
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        market = conn.execute('SELECT id FROM markets WHERE slug=? ORDER BY id LIMIT 1', (slug,)).fetchone()
        already_exists = market is not None
        if not market:
            conn.execute("INSERT INTO markets (name,slug,enabled,last_probs) VALUES (?,?,1,'{}')", (event['title'], slug))
    return jsonify(ok=True, already_exists=already_exists)


_scan_lock = threading.Lock()
_scan_cache = None
_scan_zones = OrderedDict()
SCAN_PAGE_SIZE = 100
SCAN_MAX_PAGES = 5
SCAN_TIME_BUDGET = 55
SCAN_TAGS = ('daily-temperature', 'temperature')


def scan_station_locations(codes, deadline):
    """Batch station lookup and share the bot's in-process AWC backoff/limiter."""
    found = {}
    for start in range(0, len(codes), 100):
        batch = codes[start:start+100]
        if time.monotonic() >= deadline or time.time() < utils._AWC_STATE['blocked_until'] or not utils.AWC_LIMITER.allow():
            break
        try:
            r = utils.SESSION.get('https://aviationweather.gov/api/data/stationinfo',
                params={'ids': ','.join(batch), 'format':'json'}, timeout=6)
            if r.status_code in (403, 429):
                utils._awc_penalty(r.status_code)
                break
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list): continue
            for item in data:
                if not isinstance(item, dict): continue
                code = str(item.get('icaoId') or item.get('id') or '').upper()
                try:
                    lat, lon = float(item['lat']), float(item['lon'])
                    if code in batch and math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180:
                        found[code] = (lat, lon)
                except (TypeError, ValueError, KeyError):
                    continue
        except (requests.RequestException, ValueError):
            app.logger.warning('Station lookup failed during temperature scan')
            break
    return found


def scan_station_zones(codes, deadline):
    # Successful station-to-timezone mappings only; no city-name guesses.
    found = {code: _scan_zones[code] for code in codes if code in _scan_zones}
    pending = [code for code in codes if code not in found]
    locations = scan_station_locations(pending, deadline) if pending else {}
    for start in range(0, len(pending), 100):
        batch = [code for code in pending[start:start+100] if code in locations]
        if not batch or time.monotonic() >= deadline: continue
        try:
            data = upstream('https://api.open-meteo.com/v1/forecast', {
                'latitude': ','.join(str(locations[c][0]) for c in batch),
                'longitude': ','.join(str(locations[c][1]) for c in batch),
                'timezone':'auto', 'forecast_days':1, 'daily':'sunrise'})
            zones = apply_timezone_batch(batch, data)
            found.update(zones)
            for code, zone in zones.items():
                _scan_zones[code] = zone
                _scan_zones.move_to_end(code)
            while len(_scan_zones) > 512: _scan_zones.popitem(last=False)
        except Exception:
            app.logger.warning('Timezone lookup failed during temperature scan')
    return found


def build_temperature_scan():
    deadline = time.monotonic() + SCAN_TIME_BUDGET
    discovery_deadline = deadline - 16  # reserve time for station and timezone lookup
    events, seen, incomplete, successes = [], set(), False, 0
    # Gamma distinguishes daily-temperature from the broader temperature tag.
    for tag in SCAN_TAGS:
        for page in range(SCAN_MAX_PAGES):
            if time.monotonic() >= discovery_deadline:
                incomplete = True
                break
            try:
                data = upstream('https://gamma-api.polymarket.com/events', {
                    'tag_slug':tag, 'active':'true', 'closed':'false',
                    'limit':SCAN_PAGE_SIZE, 'offset':page*SCAN_PAGE_SIZE, 'order':'id', 'ascending':'false'})
                if not isinstance(data, list): raise ValueError('Invalid events response')
                successes += 1
            except Exception:
                incomplete = True
                break
            quotes_at = time.time()  # Gamma's snapshot for this page, not the later station lookup time
            for event in data:
                item = event_summary(event)
                if item and item['slug'] not in seen:
                    item['quotes_at'] = quotes_at
                    events.append(item); seen.add(item['slug'])
            if len(data) < SCAN_PAGE_SIZE: break
        else:
            incomplete = True  # page limit reached; don't claim the list is complete
    if not successes:
        raise RuntimeError('Temperature event listings unavailable')
    codes = sorted({e['station_code'] for e in events if e['station_code']})
    zones = scan_station_zones(codes, deadline)
    for event in events:
        if event['station_code'] in zones:
            event['timezone'] = zones[event['station_code']]
            event['timezone_basis'] = 'Координаты станции AWC / Open-Meteo'
    return {'events':events, 'incomplete':incomplete, 'scanned_at':time.time(),
            'unknown_timezones':sum(1 for e in events if not e['timezone'])}


@app.get('/api/temperature-scan')
def temperature_scan():
    global _scan_cache
    with _scan_lock:
        if _scan_cache and time.time() - _scan_cache[0] < 120:
            return jsonify(_scan_cache[1])
        try:
            data = build_temperature_scan()
        except Exception:
            app.logger.warning('Temperature scan failed', exc_info=True)
            return jsonify(error='Polymarket временно недоступен. Повторите сканирование позже.'), 502
        _scan_cache = (time.time(), data)
    return jsonify(data)


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


# Keep CLOB network waits independent of weather/market caches and cap cache size.
_books_lock = threading.Lock()
_books_cache = OrderedDict()


@app.get('/api/market/<slug>/books')
def market_books(slug):
    if not valid_slug(slug): return jsonify(error='Некорректный slug'), 400
    with _books_lock:
        entry = _books_cache.get(slug)
        if entry and time.time() - entry[0] < 20:
            _books_cache.move_to_end(slug)
            return jsonify(entry[1])
        event = event_for_station(slug)
        if not event: return jsonify(error='Не удалось загрузить данные события'), 502
        data = fetch_event_books(event)
        _books_cache[slug] = (time.time(), data)
        _books_cache.move_to_end(slug)
        while len(_books_cache) > 32:
            _books_cache.popitem(last=False)
    return jsonify(data)


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
