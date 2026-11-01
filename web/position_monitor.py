"""Read-only, singleton account monitor; never submits or cancels orders.

Run as a separate service with the same working directory and OS user as the
web terminal. Snapshots are advisory, not a stop-loss or settlement record.
"""
import fcntl
import hashlib
import json
import os
import signal
import stat
import threading
import time

import database as db
from web import trading_account

INTERVAL_SECONDS = 30
MIN_INTERVAL = 15
MAX_INTERVAL = 300


def init_db():
    with db.get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS web_account_monitor (
            id INTEGER PRIMARY KEY CHECK(id=1), checked_at REAL NOT NULL,
            status TEXT NOT NULL, wallet TEXT, fingerprint TEXT, payload TEXT, interval_seconds INTEGER)''')
        columns={r['name'] for r in conn.execute('PRAGMA table_info(web_account_monitor)')}
        if 'interval_seconds' not in columns: conn.execute('ALTER TABLE web_account_monitor ADD COLUMN interval_seconds INTEGER')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_monitor_control (
            id INTEGER PRIMARY KEY CHECK(id=1), interval_seconds INTEGER NOT NULL DEFAULT 30)''')
        conn.execute('INSERT OR IGNORE INTO web_monitor_control(id,interval_seconds) VALUES(1,30)')


def get_interval():
    try:
        with db.get_db() as conn:
            row=conn.execute('SELECT interval_seconds FROM web_monitor_control WHERE id=1').fetchone()
        value=row['interval_seconds'] if row else None
        return value if isinstance(value,int) and MIN_INTERVAL<=value<=MAX_INTERVAL else INTERVAL_SECONDS
    except Exception:
        return INTERVAL_SECONDS


def set_interval(value):
    if type(value) is not int or not MIN_INTERVAL<=value<=MAX_INTERVAL:
        raise ValueError('Укажите целое число секунд от 15 до 300.')
    with db.get_db() as conn:
        conn.execute('UPDATE web_monitor_control SET interval_seconds=? WHERE id=1',(value,))


def wait_until_next(stopped, started):
    """Reread settings while waiting, and avoid a tight loop after slow requests."""
    if time.monotonic()-started>=get_interval():
        stopped.wait(1)
        return
    while not stopped.is_set():
        remaining=get_interval()-(time.monotonic()-started)
        if remaining<=0: return
        stopped.wait(min(remaining,5))


def _fingerprint(credentials):
    # Never store or return credentials; a digest detects replacement even if the
    # same account wallet is configured with a different signer or API key.
    material=json.dumps(credentials,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def _save(status, wallet=None, payload=None, fingerprint=None):
    serialized=json.dumps(payload,ensure_ascii=True,allow_nan=False) if payload is not None else None
    with db.get_db() as conn:
        conn.execute('''INSERT INTO web_account_monitor(id,checked_at,status,wallet,payload,fingerprint,interval_seconds)
            VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            checked_at=excluded.checked_at,status=excluded.status,
            wallet=excluded.wallet,payload=excluded.payload,fingerprint=excluded.fingerprint,
            interval_seconds=excluded.interval_seconds''',
            (time.time(),status,wallet,serialized,fingerprint,get_interval()))


def poll_once():
    """Independent account and order reads. Neither submits or cancels orders."""
    credentials=None
    try:
        credentials=trading_account.load()
        if not credentials:
            _save('not_configured');return
        wallet=credentials['funder'] or credentials['signer']
        snapshot=trading_account.account_snapshot()
        if credentials != trading_account.load() or snapshot.get('wallet') != wallet:
            _save('account_changed');return
        if not isinstance(snapshot.get('errors'),list):
            raise ValueError('invalid account snapshot')
        _save('partial' if snapshot['errors'] else 'ok',wallet,snapshot,_fingerprint(credentials))
    except Exception:
        _save('error')
    finally:
        # A failing account snapshot must not prevent independent order reconciliation.
        if credentials:
            try:
                from web import order_reconciliation
                if credentials==trading_account.load(): order_reconciliation.reconcile_wallet(credentials)
            except Exception: pass


def read_status(credentials):
    """Return only the matching account's latest advisory snapshot."""
    try:
        with db.get_db() as conn:
            row=conn.execute('SELECT checked_at,status,wallet,fingerprint,payload,interval_seconds FROM web_account_monitor WHERE id=1').fetchone()
    except Exception:
        return {'status':'not_running','checked_at':None,'snapshot':None}
    if row is None: return {'status':'not_running','checked_at':None,'snapshot':None}
    wallet=(credentials['funder'] or credentials['signer']) if credentials else None
    if row['wallet'] and (not wallet or row['wallet'].lower()!=wallet.lower() or row['fingerprint']!=_fingerprint(credentials)):
        return {'status':'account_changed','checked_at':row['checked_at'],'snapshot':None}
    status=row['status']
    age=time.time()-row['checked_at']
    observed_interval=row['interval_seconds'] if type(row['interval_seconds']) is int and MIN_INTERVAL<=row['interval_seconds']<=MAX_INTERVAL else INTERVAL_SECONDS
    if age>max(90,3*min(observed_interval,get_interval())) or age < -10: status='stale'
    try: snapshot=json.loads(row['payload']) if row['payload'] and status in ('ok','partial') else None
    except (ValueError,TypeError): return {'status':'error','checked_at':row['checked_at'],'snapshot':None}
    if snapshot is not None and (not isinstance(snapshot,dict) or not isinstance(snapshot.get('wallet'),str) or not wallet or snapshot['wallet'].lower()!=wallet.lower()):
        return {'status':'error','checked_at':row['checked_at'],'snapshot':None}
    return {'status':status,'checked_at':row['checked_at'],'snapshot':snapshot,'interval_seconds':get_interval()}


def acquire_lock():
    """Use a local OS lock, which is released automatically on process exit."""
    directory=trading_account._private_directory(create=True)
    path=os.path.join(directory,'position-monitor.lock')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1:
            raise RuntimeError('Небезопасный файл блокировки монитора.')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise


def main():
    init_db()
    try: lock_fd=acquire_lock()
    except (OSError,RuntimeError):
        print('Не удалось запустить монитор: занят или небезопасен файл блокировки.',flush=True)
        return 1
    stopped=threading.Event()
    def stop(*_args): stopped.set()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while not stopped.is_set():
            started=time.monotonic()
            try: poll_once()
            except Exception: print('Ошибка обновления монитора; повторим позже.',flush=True)
            wait_until_next(stopped,started)
    finally:
        os.close(lock_fd)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
