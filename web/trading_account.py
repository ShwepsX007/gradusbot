"""Local trading credentials and read-only exchange account snapshots.

Real order placement is deliberately not implemented here. Credentials are
stored in a private file, never returned to the browser or logged.
"""
import json
import logging
import math
import os
import re
import stat
import tempfile

import requests
from eth_account import Account
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds
from py_clob_client_v2.constants import POLYGON

import config

HOST = 'https://clob.polymarket.com'
DATA_API = 'https://data-api.polymarket.com/positions'
ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')
PRIVATE_KEY = re.compile(r'^(?:0x)?[0-9a-fA-F]{64}$')


class AccountError(Exception):
    pass


def _directory():
    return os.path.join(config.BASE_DIR, '.gradus-private')


def _private_directory(create=False):
    directory = _directory()
    if create:
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
    try:
        st = os.lstat(directory)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise AccountError('Небезопасные права на папку торговых ключей; обратитесь к администратору.')
    except FileNotFoundError:
        if not create: return None
        raise AccountError('Не удалось создать папку торговых ключей.')
    return directory


def validate(data):
    if not isinstance(data, dict): raise AccountError('Неверные данные настроек.')
    key = data.get('private_key')
    if not isinstance(key, str) or not PRIVATE_KEY.fullmatch(key.strip()):
        raise AccountError('Приватный ключ должен содержать 64 шестнадцатеричных символа.')
    key = key.strip()
    if not key.startswith('0x'): key = '0x' + key
    try: signer = Account.from_key(key).address
    except Exception: raise AccountError('Недопустимый приватный ключ.') from None
    signature_type = data.get('signature_type')
    if isinstance(signature_type, bool) or str(signature_type) not in ('0','1','2','3'):
        raise AccountError('Выберите поддерживаемый тип подписи 0, 1, 2 или 3.')
    signature_type = int(signature_type)
    funder = data.get('funder', '')
    if not isinstance(funder, str): raise AccountError('Неверный адрес funder.')
    funder = funder.strip()
    if funder and not ADDRESS.fullmatch(funder): raise AccountError('Неверный адрес funder.')
    if signature_type != 0 and not funder: raise AccountError('Для этого типа подписи требуется адрес funder.')
    if signature_type == 0 and funder: raise AccountError('Для типа 0 оставьте поле funder пустым: используется адрес подписанта.')
    api = {}
    for field in ('api_key', 'api_secret', 'api_passphrase'):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c)<32 for c in value):
            raise AccountError('Заполните все три поля CLOB API без управляющих символов.')
        api[field] = value.strip()
    return {'private_key':key, 'signer':signer, 'signature_type':signature_type,
            'funder':funder, **api}


def derive_api_credentials(data):
    # Validate the wallet before contacting CLOB. The placeholders never leave
    # this process; credentials returned by the SDK are not logged or sent to UI.
    checked = validate({**data, 'api_key':'placeholder', 'api_secret':'placeholder', 'api_passphrase':'placeholder'})
    logging.getLogger('py_clob_client_v2.http_helpers.helpers').setLevel(logging.CRITICAL)
    try:
        client = ClobClient(host=HOST, chain_id=POLYGON, key=checked['private_key'], retry_on_error=False)
        keys = client.create_or_derive_api_key(nonce=0)
        derived = {**data, 'api_key':keys.api_key, 'api_secret':keys.api_secret, 'api_passphrase':keys.api_passphrase}
        return validate(derived)
    except Exception:
        raise AccountError('Не удалось получить ключи CLOB автоматически. Проверьте связь с Polymarket или введите ключи вручную.') from None


def save(data):
    credentials = derive_api_credentials(data) if isinstance(data, dict) and data.get('auto_api') is True else validate(data)
    directory = _private_directory(create=True)
    # Validate existing destination first; never follow symlinks or trust weak permissions.
    if os.path.lexists(os.path.join(directory, 'account.json')):
        load()
    fd, temporary = tempfile.mkstemp(prefix='.account-', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(credentials, f, ensure_ascii=True)
            f.flush();os.fsync(f.fileno())
        os.replace(temporary, os.path.join(directory, 'account.json'))
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return {'configured':True, 'signer':credentials['signer'],
            'wallet':credentials['funder'] or credentials['signer'],
            'signature_type':credentials['signature_type']}


def load():
    directory = _private_directory()
    if directory is None: return None
    path = os.path.join(directory, 'account.json')
    try: fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError: return None
    except OSError: raise AccountError('Недоступен файл торговых ключей.') from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or
            st.st_mode & 0o077 or st.st_nlink != 1 or st.st_size > 8192):
            raise AccountError('Небезопасные права на файл торговых ключей.')
        with os.fdopen(fd, 'r', encoding='utf-8') as f:
            fd = None
            return validate(json.load(f))
    except (ValueError, UnicodeError):
        raise AccountError('Файл торговых ключей повреждён.') from None
    finally:
        if fd is not None: os.close(fd)


def delete():
    directory = _private_directory()
    if directory is None: return
    if load() is None: return
    os.unlink(os.path.join(directory, 'account.json'))
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(dfd)
    finally: os.close(dfd)


def public_config():
    c = load()
    if c is None: return {'configured':False}
    return {'configured':True, 'signer':c['signer'], 'wallet':c['funder'] or c['signer'],
            'signature_type':c['signature_type']}


def build_client(c):
    # SDK logs error bodies verbatim; suppress them to avoid accidental secret disclosure.
    logging.getLogger('py_clob_client_v2.http_helpers.helpers').setLevel(logging.CRITICAL)
    creds = ApiCreds(api_key=c['api_key'], api_secret=c['api_secret'], api_passphrase=c['api_passphrase'])
    return ClobClient(host=HOST, chain_id=POLYGON, key=c['private_key'], creds=creds,
                      signature_type=c['signature_type'], funder=c['funder'] or None,
                      retry_on_error=False)


def _string(value, limit=200):
    return str(value)[:limit] if value is not None else ''


def _number(value):
    if isinstance(value, bool): return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError): return None


def _orders(c):
    result = build_client(c).get_open_orders(only_first_page=True)
    if not isinstance(result, list): raise AccountError('Неверный ответ CLOB.')
    return [{'id':_string(o.get('id'),120), 'asset_id':_string(o.get('asset_id'),100),
             'side':_string(o.get('side'),10), 'price':_number(o.get('price')),
             'original_size':_number(o.get('original_size')), 'size_matched':_number(o.get('size_matched')),
             'status':_string(o.get('status'),40)} for o in result[:1000] if isinstance(o,dict)]


def _positions(c):
    r = requests.get(DATA_API, params={'user':c['funder'] or c['signer'], 'sizeThreshold':0, 'limit':500, 'offset':0},
                     timeout=(3, 8), headers={'User-Agent':'gradusbot-web/1.0'})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list): raise AccountError('Неверный ответ Data API.')
    return [{'asset':_string(p.get('asset'),100), 'event_slug':_string(p.get('eventSlug')),
             'title':_string(p.get('title')), 'outcome':_string(p.get('outcome'),80),
             'size':_number(p.get('size')), 'avg_price':_number(p.get('avgPrice')),
             'current_price':_number(p.get('curPrice')), 'cash_pnl':_number(p.get('cashPnl')),
             'redeemable':p.get('redeemable') is True} for p in data[:500] if isinstance(p,dict)]


def account_snapshot():
    c = load()
    if not c: raise AccountError('Сначала настройте аккаунт Polymarket.')
    result = {'wallet':c['funder'] or c['signer'], 'orders':None, 'positions':None,
              'errors':[], 'orders_first_page':True, 'positions_maybe_partial':False}
    try: result['orders'] = _orders(c)
    except Exception: result['errors'].append('Не удалось получить открытые ордера CLOB; проверьте ключи и соединение.')
    try:
        result['positions'] = _positions(c)
        result['positions_maybe_partial'] = len(result['positions']) >= 500
    except Exception: result['errors'].append('Не удалось получить позиции из Data API.')
    return result
