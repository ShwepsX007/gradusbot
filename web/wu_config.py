"""Server-only WU credentials; same private-directory protections as Gemini."""
import contextlib
import fcntl
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from web import ai_forecast

KEY = re.compile(r'[A-Za-z0-9_.-]{8,256}')
MAX_KEYS = 2


class ConfigError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@contextlib.contextmanager
def _lock(create=False):
    try:
        directory = ai_forecast._private_dir(create)
    except ai_forecast.AIError:
        raise ConfigError('Недоступна защищённая папка ключей.', 503) from None
    if directory is None:
        yield None
        return
    try:
        fd = os.open(os.path.join(directory, 'wu.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError:
        raise ConfigError('Недоступна блокировка настроек WU.', 503) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or st.st_nlink != 1:
            raise ConfigError('Небезопасные права блокировки WU.', 503)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield directory
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read(directory):
    if directory is None:
        return []
    try:
        fd = os.open(os.path.join(directory, 'wu.json'), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError:
        raise ConfigError('Недоступен файл ключей WU.', 503) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or st.st_nlink != 1 or st.st_size > 4096:
            raise ConfigError('Небезопасные права файла ключей WU.', 503)
        with os.fdopen(fd, 'r', encoding='utf-8') as f:
            fd = None
            data = json.load(f)
        if not isinstance(data, list) or len(data) > MAX_KEYS:
            raise ValueError()
        for item in data:
            if not isinstance(item, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(item.get('id', ''))) or not KEY.fullmatch(str(item.get('key', ''))):
                raise ValueError()
        return data
    except (ValueError, UnicodeError, TypeError):
        raise ConfigError('Файл ключей WU повреждён.', 503) from None
    finally:
        if fd is not None:
            os.close(fd)


def _write(directory, data):
    if os.path.lexists(os.path.join(directory, 'wu.json')):
        _read(directory)
    fd, path = tempfile.mkstemp(prefix='.wu-', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path, os.path.join(directory, 'wu.json'))
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def private_keys():
    with _lock() as directory:
        return [x['key'] for x in _read(directory)]


def public_config():
    with _lock() as directory:
        return {'keys': [{'id': x['id']} for x in _read(directory)]}


def update(payload):
    if not isinstance(payload, dict):
        raise ConfigError('Неверные настройки.')
    with _lock(create=True) as directory:
        data = _read(directory)
        if payload.get('action') == 'add_key':
            key = payload.get('key')
            if payload.get('authorized') is not True:
                raise ConfigError('Подтвердите разрешение на автоматические запросы и возможную плату.')
            if not isinstance(key, str) or not KEY.fullmatch(key.strip()):
                raise ConfigError('Неверный формат ключа WU.')
            key = key.strip()
            if len(data) >= MAX_KEYS:
                raise ConfigError('Можно сохранить не более двух ключей.')
            if any(hmac.compare_digest(item['key'], key) for item in data):
                raise ConfigError('Этот ключ уже сохранён.')
            data.append({'id': secrets.token_hex(16), 'key': key})
        elif payload.get('action') == 'delete_key':
            kid = payload.get('id')
            if not isinstance(kid, str) or not any(x['id'] == kid for x in data):
                raise ConfigError('Ключ не найден.', 404)
            data = [x for x in data if x['id'] != kid]
        else:
            raise ConfigError('Неизвестное действие.')
        _write(directory, data)
    return {'keys': [{'id': x['id']} for x in data]}
