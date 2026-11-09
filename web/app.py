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
from web import metar_archive, forecast_ensemble, forecast_history, ai_forecast, opportunity_scan
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
        conn.execute('''CREATE TABLE IF NOT EXISTS web_trading_control (
            id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0)''')
        conn.execute('INSERT OR IGNORE INTO web_trading_control(id,enabled) VALUES(1,0)')
        # Previous builds required an additional environment gate. When removing it,
        # never silently activate a previously enabled database setting on upgrade.
        conn.execute('CREATE TABLE IF NOT EXISTS web_trading_migrations (name TEXT PRIMARY KEY)')
        migration=conn.execute('INSERT OR IGNORE INTO web_trading_migrations(name) VALUES(?)',('manual-opt-in-v1',))
        if migration.rowcount: conn.execute('UPDATE web_trading_control SET enabled=0 WHERE id=1')
        migration=conn.execute('INSERT OR IGNORE INTO web_trading_migrations(name) VALUES(?)',('reconciliation-opt-in-v1',))
        if migration.rowcount: conn.execute('UPDATE web_trading_control SET enabled=0 WHERE id=1')
        # The market-order units and unknown-outcome policy changed; require a fresh opt-in.
        migration=conn.execute('INSERT OR IGNORE INTO web_trading_migrations(name) VALUES(?)',('market-orders-opt-in-v1',))
        if migration.rowcount: conn.execute('UPDATE web_trading_control SET enabled=0 WHERE id=1')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_order_intents (
            nonce TEXT PRIMARY KEY, wallet TEXT NOT NULL, slug TEXT NOT NULL, asset TEXT NOT NULL,
            side TEXT NOT NULL, price TEXT NOT NULL, size TEXT NOT NULL, order_type TEXT NOT NULL,
            state TEXT NOT NULL, order_id TEXT, created REAL NOT NULL, updated REAL NOT NULL)''')
        intent_columns={r['name'] for r in conn.execute('PRAGMA table_info(web_order_intents)')}
        if 'purpose' not in intent_columns:
            conn.execute("ALTER TABLE web_order_intents ADD COLUMN purpose TEXT NOT NULL DEFAULT 'manual'")
        for name, declaration in (('execution_mode', "TEXT NOT NULL DEFAULT 'limit'"),
                                  ('signed_size', 'TEXT'), ('signed_price', 'TEXT'),
                                  ('acknowledged_unknown', 'TEXT')):
            if name not in intent_columns:
                conn.execute(f'ALTER TABLE web_order_intents ADD COLUMN {name} {declaration}')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_order_reconciliation (
            nonce TEXT PRIMARY KEY, expected_id TEXT, credential_fingerprint TEXT,
            assessment TEXT NOT NULL DEFAULT 'unverified', checked_at REAL,
            order_status TEXT, size_matched TEXT, confirmed_size TEXT, failed_size TEXT,
            reason TEXT, FOREIGN KEY(nonce) REFERENCES web_order_intents(nonce))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_monitor_control (
            id INTEGER PRIMARY KEY CHECK(id=1), interval_seconds INTEGER NOT NULL DEFAULT 30)''')
        conn.execute('INSERT OR IGNORE INTO web_monitor_control(id,interval_seconds) VALUES(1,30)')
    metar_archive.init_db()
    forecast_ensemble.init_db()
    forecast_history.init_db()
    ai_forecast.init_db()



init_web_db()
from web import copy_observer, auto_scalp
copy_observer.init_db()
auto_scalp.init_db()
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
    return cached(('forecast', lat, lon), 900, lambda: forecast_ensemble.normalize_open_meteo(upstream('https://api.open-meteo.com/v1/forecast', {
        'latitude': lat, 'longitude': lon, 'timezone': 'auto', 'past_days': 6, 'forecast_days': 2,
        'hourly': 'temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m,wind_direction_10m,precipitation_probability',
        'daily': 'temperature_2m_max,temperature_2m_min,sunrise,sunset', 'timeformat':'unixtime'})))


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
    tunnel=bool(trusted_ssh_tunnel())
    if not tunnel: return jsonify(tunnel=False, trading_enabled=False)
    from web import trading_orders
    return jsonify(tunnel=True, trading_enabled=trading_orders.enabled())


@app.route('/api/trading/account-config', methods=['GET', 'POST', 'DELETE'])
def trading_account_config():
    if not trusted_ssh_tunnel():
        return jsonify(error='Настройки аккаунта доступны только через SSH-туннель.'), 403
    from web import trading_account
    if request.method == 'GET':
        try: return jsonify(trading_account.public_config())
        except trading_account.AccountError as exc: return jsonify(error=str(exc)), 503
    if not login_allowed('trading-config:' + (request.remote_addr or 'unknown')):
        return jsonify(error='Слишком много попыток. Повторите через 15 минут.'), 429
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('current_password'), str):
        return jsonify(error='Введите текущий пароль терминала.'), 400
    try: valid = len(data['current_password']) <= 256 and check_password_hash(current_password_hash(), data['current_password'])
    except (ValueError, TypeError): valid = False
    if not valid: return jsonify(error='Неверный пароль терминала.'), 400
    # Correct reauthentication clears the password-attempt counter immediately.
    # Invalid wallet fields or CLOB failures must not cause a password lockout.
    with _login_lock: _login_attempts.pop('trading-config:' + (request.remote_addr or 'unknown'), None)
    try:
        from web import trading_orders
        trading_orders.set_enabled(False)  # credentials are changing; a fresh opt-in is required
        if request.method == 'DELETE':
            trading_account.delete()
            info = {'configured':False}
        else:
            info = trading_account.save(data)
    except trading_account.AccountError as exc: return jsonify(error=str(exc)), 400
    except Exception:
        app.logger.error('Failed to save trading account credentials')  # never log request body or exceptions
        return jsonify(error='Не удалось безопасно сохранить настройки. Проверьте права папки на сервере.'), 503
    return jsonify(info)


@app.get('/api/trading/monitor')
def trading_monitor_status():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    from web import position_monitor, trading_account
    try: return jsonify(position_monitor.read_status(trading_account.load()))
    except Exception: return jsonify(error='Состояние фонового наблюдения недоступно.'),503


@app.route('/api/trading/monitor-interval', methods=['GET','POST'])
def trading_monitor_interval():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    from web import position_monitor
    if request.method=='GET': return jsonify(interval_seconds=position_monitor.get_interval())
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or data.get('confirm') is not True:
        return jsonify(error='Требуется подтверждение.'),400
    try:
        position_monitor.set_interval(data.get('interval_seconds'))
    except ValueError as exc: return jsonify(error=str(exc)),400
    except Exception: return jsonify(error='Не удалось сохранить интервал наблюдения.'),503
    return jsonify(interval_seconds=position_monitor.get_interval())


@app.route('/api/auto-scalp', methods=['GET','POST'])
def auto_scalp_api():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    if request.method=='GET': return jsonify(auto_scalp.status())
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or data.get('confirm') is not True: return jsonify(error='Требуется подтверждение.'),400
    try:
        if data.get('mode')!='demo': raise auto_scalp.ScalpError('Реальная автоматическая торговля ещё не реализована.')
        action=data.get('action')
        if action=='settings':auto_scalp.set_settings(data.get('settings'))
        elif action=='start':auto_scalp.set_enabled(True)
        elif action=='stop':auto_scalp.set_enabled(False)
        else:raise auto_scalp.ScalpError('Неизвестное действие.')
    except auto_scalp.ScalpError as exc:return jsonify(error=str(exc)),400
    except Exception:return jsonify(error='Не удалось изменить настройки демо.'),503
    return jsonify(ok=True)


@app.route('/api/copy-observer', methods=['GET','POST'])
def copy_observer_traders():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    if request.method=='GET': return jsonify(copy_observer.status())
    try: wallet=copy_observer.add_leader(request.get_json(silent=True))
    except copy_observer.ObserverError as exc: return jsonify(error=str(exc)),400
    except Exception: return jsonify(error='Не удалось добавить наблюдение.'),503
    return jsonify(wallet=wallet),201


@app.get('/api/copy-observer/notification')
def copy_observer_notification():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    return jsonify(copy_observer.notification())


@app.get('/api/copy-observer/metadata')
def copy_observer_metadata():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    try: return jsonify(copy_observer.metadata(request.args.get('tokens','').split(',')))
    except copy_observer.ObserverError as exc: return jsonify(error=str(exc)),400


@app.get('/api/copy-observer/<int:leader_id>/signal/<event_key>')
def copy_observer_signal(leader_id,event_key):
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    try: return jsonify(copy_observer.signal_detail(leader_id,event_key))
    except copy_observer.ObserverError as exc: return jsonify(error=str(exc)),400
    except Exception: return jsonify(error='Не удалось получить детали сделки.'),503


@app.route('/api/copy-observer/<int:leader_id>', methods=['PATCH','DELETE'])
def copy_observer_trader(leader_id):
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or data.get('confirm') is not True: return jsonify(error='Требуется подтверждение.'),400
    try:
        if request.method=='DELETE': copy_observer.delete_leader(leader_id)
        elif 'settings' in data: copy_observer.update_settings(leader_id,data['settings'])
        else: copy_observer.set_enabled(leader_id,data.get('enabled'))
    except copy_observer.ObserverError as exc: return jsonify(error=str(exc)),400
    except Exception: return jsonify(error='Не удалось изменить наблюдение.'),503
    return jsonify(ok=True)


@app.get('/api/trading/account')
def trading_account_snapshot():
    if not trusted_ssh_tunnel(): return jsonify(error='Аккаунт доступен только через SSH-туннель.'), 403
    from web import trading_account
    try: return jsonify(trading_account.account_snapshot())
    except trading_account.AccountError as exc: return jsonify(error=str(exc)), 400
    except Exception:
        app.logger.warning('Account snapshot failed')
        return jsonify(error='Аккаунт временно недоступен.'), 503


@app.get('/api/trading/portfolio')
def trading_portfolio():
    if not trusted_ssh_tunnel(): return jsonify(error='Портфель доступен только через SSH-туннель.'), 403
    from web import portfolio, trading_account
    try: return jsonify(portfolio.snapshot())
    except trading_account.AccountError as exc: return jsonify(error=str(exc)), 400
    except Exception: return jsonify(error='Портфель временно недоступен.'), 503


@app.post('/api/trading/control')
def trading_control():
    if not trusted_ssh_tunnel(): return jsonify(error='Торговля доступна только через SSH-туннель.'), 403
    from web import trading_account, trading_orders
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or not isinstance(data.get('enabled'),bool) or data.get('confirm') is not True:
        return jsonify(error='Требуется явное подтверждение.'), 400
    if not login_allowed('trade-control:'+(request.remote_addr or 'unknown')):
        return jsonify(error='Слишком много попыток. Повторите позже.'),429
    password=data.get('current_password')
    try: valid=isinstance(password,str) and len(password)<=256 and check_password_hash(current_password_hash(),password)
    except (ValueError,TypeError): valid=False
    if not valid: return jsonify(error='Неверный пароль терминала.'),400
    with _login_lock: _login_attempts.pop('trade-control:'+(request.remote_addr or 'unknown'),None)
    try:
        if data['enabled'] and not trading_account.load(): return jsonify(error='Сначала настройте аккаунт Polymarket.'),400
        trading_orders.set_enabled(data['enabled'])
    except trading_orders.TradeError as exc: return jsonify(error=str(exc)),400
    except Exception: return jsonify(error='Не удалось изменить настройку торговли.'),503
    return jsonify(enabled=data['enabled'])


@app.get('/api/trading/intents')
def trading_intents():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    from web import trading_account, trading_orders
    try:
        c=trading_account.load()
        if not c: return jsonify(intents=[])
        slug=request.args.get('slug','')
        if slug and not re.fullmatch(r'[a-z0-9-]{1,200}',slug): return jsonify(error='Неверное событие.'),400
        from web import order_reconciliation
        order_reconciliation.reconcile_wallet(c)
        rows=trading_orders.list_intents(c['funder'] or c['signer'])
        if slug:
            rows=[row for row in rows if row['slug']==slug]
            trading_orders.annotate_order_statuses(rows,c)
        return jsonify(intents=rows)
    except Exception: return jsonify(error='Не удалось получить журнал заявок.'),503


@app.post('/api/trading/orders')
def trading_submit_order():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    from web import trading_orders
    if not trading_orders.enabled(): return jsonify(error='Торговля выключена в настройках.'),403
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or not isinstance(data.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{1,200}',data['slug']):
        return jsonify(error='Неверное событие.'),400
    # A retry must return the previously persisted state before any network call.
    try:
        from web import trading_account
        c=trading_account.load()
        if c:
            prior=trading_orders.find_intent(c['funder'] or c['signer'],data.get('nonce'))
            if prior: return jsonify(prior)
    except Exception:
        return jsonify(error='Не удалось проверить журнал. Не повторяйте заявку до сверки.'),503
    event=event_for_station(data['slug'],fresh=True)
    if not event: return jsonify(error='Не удалось проверить событие. Ордер не отправлен.'),503
    try: return jsonify(trading_orders.submit(data,event))
    except trading_orders.UnknownOrders as exc: return jsonify(error=str(exc),code='unknown_orders',nonces=exc.nonces),409
    except trading_orders.TradeError as exc: return jsonify(error=str(exc)),400
    except Exception:
        app.logger.error('Order request failed; do not retry without reconciliation')
        return jsonify(error='Не удалось определить состояние заявки. Проверьте Polymarket перед повтором.'),503


@app.post('/api/trading/cancel')
def trading_cancel_order():
    if not trusted_ssh_tunnel(): return jsonify(error='Требуется SSH-туннель.'),403
    from web import trading_orders
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or data.get('confirm') is not True: return jsonify(error='Подтвердите отмену.'),400
    try: return jsonify(trading_orders.cancel(data.get('order_id')))
    except trading_orders.TradeError as exc: return jsonify(error=str(exc)),400
    except Exception: return jsonify(error='Результат отмены неизвестен. Проверьте ордер на Polymarket.'),503


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
        conn.execute('DELETE FROM web_ai_forecasts WHERE station_id=?', (sid,))
        conn.execute('DELETE FROM web_forecast_snapshots WHERE station_id=?', (sid,))
        prefix = f'st_{sid}_'
        conn.execute('DELETE FROM settings WHERE substr(key,1,?)=?', (len(prefix), prefix))
        old_code=conn.execute('SELECT UPPER(station_code) AS code FROM stations WHERE id=?',(sid,)).fetchone()['code']
        conn.execute('DELETE FROM stations WHERE id=?', (sid,))
        if old_code and not conn.execute("SELECT 1 FROM stations WHERE station_type='metar' AND UPPER(station_code)=? LIMIT 1",(old_code,)).fetchone():
            conn.execute('DELETE FROM web_metar_archive WHERE icao=?',(old_code,))
            conn.execute('DELETE FROM web_metar_archive_status WHERE icao=?',(old_code,))
            conn.execute('DELETE FROM web_metar_archive_station WHERE icao=?',(old_code,))
    return jsonify(ok=True)


def record_web_metar(sid, metar, view_active=False):
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
            SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM stations WHERE id=? AND (enabled=1 OR ?=1))
            ON CONFLICT(station_id,obs_time) DO UPDATE SET temp=excluded.temp''', (sid, obs_time, temp, sid, int(view_active)))
        conn.execute('DELETE FROM web_metar_history WHERE station_id=? AND obs_time<?', (sid, now - 8 * 24 * 3600))


def _metar_points(sid, archive, hours):
    # The locally recorded report wins when the same observation appears in both.
    cutoff=time.time()-hours*3600
    reports=archive.get('reports',[]) if isinstance(archive,dict) else []
    points={r['obs_time']:r['temp'] for r in reports if r.get('temp') is not None and r['obs_time']>=cutoff}
    with db.get_db() as conn:
        for row in conn.execute('SELECT obs_time,temp FROM web_metar_history WHERE station_id=? AND obs_time>=?',(sid,cutoff)):
            points[row['obs_time']]=row['temp']
    return [[t,points[t]] for t in sorted(points)]


def combined_history(sid, archive=None):
    points=db.get_station_history(sid,hours=8*24)
    points.extend(_metar_points(sid,archive,8*24))
    return sorted(points,key=lambda p:p[0])


def metar_chart_history(sid, archive=None):
    # Bot history stores poll times, not METAR observation times; do not mix them.
    return _metar_points(sid,archive,168)


def save_solar_days(sid, forecast_data, view_active=False):
    if not isinstance(forecast_data, dict): return
    tz = forecast_data.get('timezone')
    daily = forecast_data.get('daily') or {}
    if not forecast_ensemble.valid_timezone(tz) or not isinstance(daily, dict): return
    dates, rises, sets = daily.get('time'), daily.get('sunrise'), daily.get('sunset')
    if not all(isinstance(x, list) for x in (dates, rises, sets)): return
    rows = []
    for day, rise, sunset in zip(dates, rises, sets):
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day): continue
        # Preserve dates even when polar conditions have no sunrise or sunset.
        if rise is not None and (not isinstance(rise, str) or not rise.startswith(day + 'T')): continue
        if sunset is not None and (not isinstance(sunset, str) or not sunset.startswith(day + 'T')): continue
        rows.append((sid, day, rise, sunset, tz, sid, int(view_active)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
    with db.get_db() as conn:
        for row in rows:
            conn.execute("""INSERT OR REPLACE INTO web_station_solar (station_id,day,sunrise,sunset,timezone)
                SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM stations WHERE id=? AND (enabled=1 OR ?=1))""", row)
        conn.execute('DELETE FROM web_station_solar WHERE station_id=? AND day<?', (sid, cutoff))


def get_solar_days(sid):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute('SELECT day,sunrise,sunset,timezone FROM web_station_solar WHERE station_id=? AND day>=? ORDER BY day', (sid, cutoff))]


@app.get('/api/stations/<int:sid>/weather')
def weather(sid, active_view=False):
    station = db.get_station(sid)
    if not station: return jsonify(error='Станция не найдена'), 404
    # A visible web overview requests a temporary snapshot without changing the
    # bot's persistent enabled flag. No background web poller is started.
    active_view=active_view or request.args.get('view')=='1'
    live=bool(station['enabled']) or active_view
    metar_archive.prune()
    code=str(station.get('station_code') or '').upper()
    valid=station.get('station_type')=='metar' and re.fullmatch(r'[A-Z0-9]{4}',code)
    if valid: metar_archive.refresh(code)
    archive=metar_archive.get(code if valid else None)
    result = {'station': public_station(station), 'history': combined_history(sid,archive), 'metar_history': metar_chart_history(sid,archive), 'metar_archive':archive, 'solar_days': get_solar_days(sid), 'metar': None, 'forecast': None, 'forecast_ensemble': None, 'forecast_status': {'reason':'disabled' if not live else 'pending'}, 'info': None, 'view_active':bool(active_view)}
    if not live:
        result['message'] = 'Опрос станции выключен'
        return jsonify(result)
    if station.get('station_type') != 'metar':
        result['forecast_status']={'reason':'unsupported'}
        result['message'] = 'В веб-терминале поддерживаются станции METAR'
        return jsonify(result)
    if not valid:
        result['forecast_status']={'reason':'invalid_code'}
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
        record_web_metar(sid, metar, view_active=active_view)
        result['history'] = combined_history(sid,archive)
        result['metar_history'] = metar_chart_history(sid,archive)
    info = station_info(code)
    coord_source='stationinfo'
    if not isinstance(info,dict) or not forecast_ensemble.coordinates(info.get('lat'),info.get('lon')):
        info=archive.get('location');coord_source='metar_archive'
    result['info'] = info
    coords=forecast_ensemble.coordinates(info.get('lat'),info.get('lon')) if isinstance(info,dict) else None
    history_error=False
    if coords:
        opened=forecast(*coords)
        open_cache=_cache.get(('forecast',*coords))
        open_requested_at=open_cache[0] if open_cache and opened is not None else None
        result['forecast']=opened
        save_solar_days(sid, opened, view_active=active_view)
        result['solar_days'] = get_solar_days(sid)
        metno=forecast_ensemble.get_metno(*coords)
        tz_candidates=[opened.get('timezone') if isinstance(opened,dict) else None]+[s.get('timezone') for s in result['solar_days']]
        tz=next((name for name in tz_candidates if forecast_ensemble.valid_timezone(name)),None)
        result['forecast_ensemble']=forecast_ensemble.ensemble(opened,metno.get('data'),tz)
        if active_view:
            try:forecast_history.capture(sid,opened,metno.get('data'))
            except Exception:app.logger.warning('Could not save forecast snapshot');history_error=True
        has_open=any('Open-Meteo' in p['sources'] for p in result['forecast_ensemble']['points'])
        result['forecast_status']={'reason': 'ok' if tz else 'no_timezone', 'coordinate_source':coord_source,
            'open_meteo': 'ok' if has_open else 'unavailable', 'open_meteo_requested_at':open_requested_at,
            'met_norway':metno.get('status'), 'met_norway_updated_at':(metno.get('data') or {}).get('updated_at'),
            'met_norway_fetched_at':metno.get('fetched_at')}
    else:
        result['forecast_status']={'reason':'no_coordinates'}
    try:result['forecast_comparison']=forecast_history.compare(sid,metar)
    except Exception:app.logger.warning('Could not compare forecast snapshot');result['forecast_comparison']=None;history_error=True
    result['forecast_history_status']='error' if history_error else 'ok'
    if not metar: result['message'] = 'METAR временно недоступен'
    return jsonify(result)


@app.route('/api/ai/config', methods=['GET','POST'])
def ai_config():
    if not trusted_ssh_tunnel():return jsonify(error='Настройки Gemini доступны только через SSH-туннель.'),403
    if request.method=='GET':
        try:return jsonify(ai_forecast.public_config())
        except ai_forecast.AIError as exc:return jsonify(error=str(exc)),exc.status
        except Exception:return jsonify(error='Не удалось прочитать защищённые настройки Gemini.'),503
    name='ai-config:'+(request.remote_addr or 'unknown')
    if not login_allowed(name):return jsonify(error='Слишком много попыток. Повторите через 15 минут.'),429
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or not isinstance(data.get('current_password'),str):return jsonify(error='Введите текущий пароль терминала.'),400
    try:valid=len(data['current_password'])<=256 and check_password_hash(current_password_hash(),data['current_password'])
    except (ValueError,TypeError):valid=False
    if not valid:return jsonify(error='Неверный пароль терминала.'),400
    with _login_lock:_login_attempts.pop(name,None)
    try:return jsonify(ai_forecast.config_update(data))
    except ai_forecast.AIError as exc:return jsonify(error=str(exc)),exc.status
    except Exception:
        app.logger.error('Failed to save Gemini settings')
        return jsonify(error='Не удалось безопасно сохранить настройки Gemini.'),503


@app.route('/api/stations/<int:sid>/ai-forecast',methods=['GET','POST'])
def station_ai_forecast(sid):
    if not trusted_ssh_tunnel():return jsonify(error='ИИ-прогноз доступен только через SSH-туннель.'),403
    if not db.get_station(sid):return jsonify(error='Станция не найдена.'),404
    if request.method=='GET':
        return jsonify(forecast=ai_forecast.latest(sid))
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or data.get('confirm') is not True:return jsonify(error='Подтвердите отправку данных и возможную плату за запрос.'),400
    token=None
    try:
        token=ai_forecast._claim()
        if not ai_forecast.public_config()['keys']:raise ai_forecast.AIError('Добавьте ключ Gemini в настройках.',409)
        # Build a server-owned snapshot; never accept weather or private account
        # data from the browser. The manual request takes a fresh station snapshot.
        response=weather(sid, active_view=True)
        if isinstance(response,tuple):return jsonify(error='Станция недоступна.'),404
        snapshot=response.get_json()
        if not isinstance(snapshot,dict):return jsonify(error='Нет данных станции.'),503
        return jsonify(forecast=ai_forecast.generate(sid,snapshot,lease=token))
    except ai_forecast.AIError as exc:return jsonify(error=str(exc)),exc.status
    except Exception:
        app.logger.error('Gemini weather request failed without logging data')
        return jsonify(error='Ошибка ИИ-прогноза. Автоматического повтора нет; проверьте расходы перед новым запросом.'),503
    finally:
        if token is not None:ai_forecast._release(token)


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


def temperature_scan_data():
    global _scan_cache
    with _scan_lock:
        if _scan_cache and time.time() - _scan_cache[0] < 120:
            now=time.time()
            quotes=[e.get('quotes_at') for e in _scan_cache[1].get('events',[]) if isinstance(e.get('quotes_at'),(int,float))]
            if not quotes or all(0<=now-t<120 for t in quotes):return _scan_cache[1]
        data=build_temperature_scan()
        _scan_cache=(time.time(),data)
        return data


@app.get('/api/temperature-scan')
def temperature_scan():
    try:
        data=temperature_scan_data()
        # Internal candidate metadata can be large and is not needed by the catalog UI.
        return jsonify({**data,'events':[{k:v for k,v in event.items() if k not in ('opportunity_markets','opportunity_truncated')} for event in data['events']]})
    except Exception:
        app.logger.warning('Temperature scan failed', exc_info=True)
        return jsonify(error='Polymarket временно недоступен. Повторите сканирование позже.'),502


_opp_lock=threading.Lock()
_opp_busy=threading.BoundedSemaphore(1)
_opp_sessions=OrderedDict()
OPP_MAX_CANDIDATES=5000
OPP_SESSION_TTL=300


@app.get('/api/temperature-opportunities')
def temperature_opportunities():
    session_id=request.args.get('session')
    if session_id:
        with _opp_lock:
            item=_opp_sessions.get(session_id)
            if not item or time.time()-item['created']>OPP_SESSION_TTL:
                return jsonify(error='Время поиска истекло. Запустите поиск заново.'),409
        try:offset=int(request.args.get('offset',''))
        except ValueError:return jsonify(error='Некорректная страница поиска.'),400
        if offset<0 or offset>=len(item['candidates']) or offset%opportunity_scan.PAGE_TOKENS:
            return jsonify(error='Некорректная страница поиска.'),400
    else:
        def num(name,default,lo,hi):
            return opportunity_scan.number(request.args.get(name,default),lo,hi)
        settings={'price':num('price','97',0,100),'leader_min':num('leader_min','97',0,100),
                  'gap':num('gap','1',0,100),'gap_units':request.args.get('gap_units','C'),
                  'side':request.args.get('side','no'),'quote':request.args.get('quote','ask'),
                  'reference':request.args.get('reference','leader'),'query':request.args.get('query','').strip()}
        if any(settings[k] is None for k in ('price','leader_min','gap')) or settings['gap_units'] not in ('C','F') or settings['side'] not in ('yes','no','both') or settings['quote'] not in ('ask','bid','either','both') or settings['reference'] not in ('leader','observed') or len(settings['query'])>100:
            return jsonify(error='Проверьте цены, разрыв и настройки фильтра.'),400
        offset=0;item=None
    if not _opp_busy.acquire(blocking=False):return jsonify(error='Поиск уже выполняется. Попробуйте позже.'),429
    try:
        if item is None:
            try:data=temperature_scan_data()
            except Exception:return jsonify(error='Каталог Polymarket временно недоступен.'),502
            candidates,skipped=opportunity_scan.prepare(data['events'],settings)
            limited=len(candidates)>OPP_MAX_CANDIDATES
            candidates=candidates[:OPP_MAX_CANDIDATES]
            item={'candidates':candidates,'settings':settings,'created':time.time(),'scanned_at':data.get('scanned_at'),
                  'incomplete':data.get('incomplete',False),'limited':limited,'skipped':skipped}
            session_id=secrets.token_urlsafe(18)
            with _opp_lock:
                for key in list(_opp_sessions):
                    if time.time()-_opp_sessions[key]['created']>OPP_SESSION_TTL:del _opp_sessions[key]
                _opp_sessions[session_id]=item
                while len(_opp_sessions)>10:_opp_sessions.popitem(last=False)
        selected=item['candidates'][offset:offset+opportunity_scan.PAGE_TOKENS]
        now=time.time()
        fresh=[candidate for candidate in selected if isinstance(candidate['event'].get('quotes_at'),(int,float)) and 0<=now-candidate['event']['quotes_at']<120]
        stale_leader=len(selected)-len(fresh)
        books=opportunity_scan.books_for([candidate['token'] for candidate in fresh])
        after_books=time.time()
        still_fresh=[candidate for candidate in fresh if 0<=after_books-candidate['event']['quotes_at']<120]
        stale_leader+=len(fresh)-len(still_fresh)
        rows,unavailable=opportunity_scan.matched_rows(still_fresh,books,item['settings'])
        next_offset=offset+len(selected)
        return jsonify(rows=rows,session=session_id,next_offset=next_offset if next_offset<len(item['candidates']) else None,
            checked=len(selected),total_candidates=len(item['candidates']),unavailable=unavailable,stale_leader=stale_leader,skipped=item['skipped'],
            catalog_incomplete=item['incomplete'],candidate_limit=item['limited'],scanned_at=item['scanned_at'],settings=item['settings'])
    finally:_opp_busy.release()


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
