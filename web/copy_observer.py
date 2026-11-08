"""Public-trade observer. It does not load trading credentials, place or cancel orders.

It records signals and an indicative five-level book comparison, not simulated
fills or positions. The public feed has no unique fill id and may be delayed.
"""
import collections
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse, unquote

import requests
import database as db
from web import orderbook, trading_account

ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')
HASH = re.compile(r'^0x[0-9a-fA-F]{64}$')
TOKEN = re.compile(r'^[0-9]{1,80}$')
PROFILE = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
MAX_LEADERS = 5
MAX_PAGES = 10
PAGE_SIZE = 100
OVERLAP = 300
MAX_NEW = 200
DATA_URL = 'https://data-api.polymarket.com/v2/trades'
GAMMA_URL = 'https://gamma-api.polymarket.com'


class ObserverError(Exception):
    pass


def init_db():
    with db.get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS web_copy_leaders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL, enabled INTEGER NOT NULL, started_at INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1, last_attempt REAL, last_success REAL,
            status TEXT NOT NULL DEFAULT 'waiting', error_reason TEXT, mode TEXT NOT NULL,
            amount TEXT NOT NULL, cap TEXT NOT NULL, interval_seconds INTEGER NOT NULL)''')
        columns={r['name'] for r in conn.execute('PRAGMA table_info(web_copy_leaders)')}
        if 'error_reason' not in columns: conn.execute('ALTER TABLE web_copy_leaders ADD COLUMN error_reason TEXT')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_copy_signals (
            leader_id INTEGER NOT NULL, event_key TEXT NOT NULL, source_time INTEGER NOT NULL,
            observed_at REAL NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(leader_id,event_key))''')
        conn.execute('CREATE INDEX IF NOT EXISTS web_copy_signals_recent ON web_copy_signals(observed_at DESC)')
        # Persistent, monotonic counter: survives signal pruning and leader deletion.
        # The epoch distinguishes a new database from a previously seen one.
        conn.execute('''CREATE TABLE IF NOT EXISTS web_copy_notifications (
            id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, seq INTEGER NOT NULL)''')
        conn.execute('INSERT OR IGNORE INTO web_copy_notifications VALUES(1,?,0)',(secrets.token_hex(16),))
        conn.execute('''CREATE TRIGGER IF NOT EXISTS web_copy_signal_notification
            AFTER INSERT ON web_copy_signals BEGIN
            UPDATE web_copy_notifications SET seq=seq+1 WHERE id=1; END''')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_copy_service (
            id INTEGER PRIMARY KEY CHECK(id=1), checked_at REAL NOT NULL)''')


def _get(url, params):
    r = requests.get(url, params=params, timeout=(3, 8), headers={'User-Agent':'gradusbot-web/1.0'})
    r.raise_for_status()
    return r.json()


def resolve_profile(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ObserverError('Укажите адрес кошелька или ссылку на профиль Polymarket.')
    value=value.strip()
    if value.startswith('https://'):
        u=urlparse(value)
        try: port=u.port
        except ValueError: raise ObserverError('Неверная ссылка на профиль.') from None
        if u.hostname not in ('polymarket.com','www.polymarket.com') or u.username or u.password or port or u.query or u.fragment:
            raise ObserverError('Разрешены только ссылки на профиль Polymarket без параметров.')
        path=unquote(u.path).strip('/')
        if path.startswith('profile/'): value=path[len('profile/'):]
        elif path.startswith('@'): value=path
        else: raise ObserverError('Не удалось распознать ссылку на профиль.')
    if ADDRESS.fullmatch(value):
        try: profile=_get(GAMMA_URL+'/public-profile',{'address':value})
        except Exception: raise ObserverError('Не удалось проверить профиль Polymarket. Попробуйте позже.') from None
        wallet=profile.get('proxyWallet') if isinstance(profile,dict) else None
        if not isinstance(wallet,str) or not ADDRESS.fullmatch(wallet):
            raise ObserverError('Polymarket не вернул торговый кошелёк профиля.')
        label=(profile.get('name') or profile.get('pseudonym') or wallet)
        return wallet.lower(), str(label)[:100]
    name=value.lstrip('@')
    if not PROFILE.fullmatch(name): raise ObserverError('Неверное имя профиля или адрес кошелька.')
    try: data=_get(GAMMA_URL+'/public-search',{'q':name,'search_profiles':'true','limit_per_type':20})
    except Exception: raise ObserverError('Поиск профиля недоступен. Введите торговый адрес кошелька.') from None
    profiles=data.get('profiles') if isinstance(data,dict) else None
    if not isinstance(profiles,list): raise ObserverError('Поиск профиля не вернул список. Введите адрес кошелька.')
    matches={}
    for p in profiles:
        if not isinstance(p,dict): continue
        if name.lower() not in [str(p.get(k) or '').lower() for k in ('name','pseudonym','username')]: continue
        wallet=p.get('proxyWallet') or p.get('wallet') or p.get('address')
        if isinstance(wallet,str) and ADDRESS.fullmatch(wallet): matches[wallet.lower()]=str(p.get('name') or p.get('pseudonym') or name)[:100]
    if len(matches)!=1: raise ObserverError('Профиль не найден однозначно. Укажите торговый адрес кошелька.')
    wallet,label=next(iter(matches.items()))
    # Search results can expose a user address instead of the trading wallet.
    try: profile=_get(GAMMA_URL+'/public-profile',{'address':wallet})
    except Exception: raise ObserverError('Не удалось проверить торговый кошелёк профиля.') from None
    resolved=profile.get('proxyWallet') if isinstance(profile,dict) else None
    if not isinstance(resolved,str) or not ADDRESS.fullmatch(resolved): raise ObserverError('Не удалось определить торговый кошелёк профиля.')
    return resolved.lower(),label


def _decimal(value, low, high, precision=6):
    if isinstance(value,bool): raise ObserverError('Укажите допустимый числовой объём.')
    try: n=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError): raise ObserverError('Укажите допустимый числовой объём.') from None
    if not n.is_finite() or n < low or n > high or n.as_tuple().exponent < -precision:
        raise ObserverError('Объём вне допустимого диапазона или содержит больше 6 знаков после запятой.')
    return n


def _settings(data):
    if not isinstance(data,dict): raise ObserverError('Неверные настройки наблюдения.')
    mode=data.get('mode')
    if mode not in ('fixed','same','percent'): raise ObserverError('Выберите режим объёма.')
    amount=_decimal(data.get('amount',1),Decimal('0.000001'),Decimal('100000'))
    if mode=='percent' and amount>10000: raise ObserverError('Процент не может превышать 10000%.')
    cap=_decimal(data.get('cap'),Decimal('0.000001'),Decimal('100000'))
    interval=data.get('interval_seconds')
    if type(interval) is not int or not 15<=interval<=300: raise ObserverError('Интервал наблюдения: целое число от 15 до 300 секунд.')
    return mode,str(amount),str(cap),interval


def add_leader(data):
    if not isinstance(data,dict) or data.get('confirm') is not True: raise ObserverError('Подтвердите добавление наблюдения.')
    mode,amount,cap,interval=_settings(data)
    wallet,label=resolve_profile(data.get('profile'))
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT COUNT(*) FROM web_copy_leaders').fetchone()[0]>=MAX_LEADERS: raise ObserverError('Можно наблюдать не более пяти трейдеров.')
        if conn.execute('SELECT 1 FROM web_copy_leaders WHERE wallet=?',(wallet,)).fetchone(): raise ObserverError('Этот торговый кошелёк уже добавлен.')
        conn.execute('''INSERT INTO web_copy_leaders(wallet,label,enabled,started_at,mode,amount,cap,interval_seconds)
            VALUES(?,?,1,?,?,?,?,?)''',(wallet,label,int(time.time()),mode,str(amount),str(cap),interval))
    return wallet


def update_settings(leader_id, settings):
    mode,amount,cap,interval=_settings(settings)
    with db.get_db() as conn:
        changed=conn.execute('''UPDATE web_copy_leaders SET mode=?,amount=?,cap=?,interval_seconds=?,
            revision=revision+1,last_attempt=NULL,error_reason=NULL,
            status=CASE WHEN enabled=1 THEN 'waiting' ELSE 'paused' END WHERE id=?''',
            (mode,amount,cap,interval,leader_id))
        if changed.rowcount!=1: raise ObserverError('Трейдер не найден.')


def set_enabled(leader_id, enabled):
    if type(enabled) is not bool: raise ObserverError('Неверное состояние наблюдения.')
    with db.get_db() as conn:
        row=conn.execute('SELECT enabled FROM web_copy_leaders WHERE id=?',(leader_id,)).fetchone()
        if row is None: raise ObserverError('Трейдер не найден.')
        if bool(row['enabled'])!=enabled:
            conn.execute('''UPDATE web_copy_leaders SET enabled=?,revision=revision+1,
                started_at=?,last_attempt=NULL,last_success=NULL,status=?,error_reason=NULL WHERE id=?''',
                (int(enabled),int(time.time()),'waiting' if enabled else 'paused',leader_id))


def delete_leader(leader_id):
    with db.get_db() as conn:
        conn.execute('DELETE FROM web_copy_signals WHERE leader_id=?',(leader_id,))
        if conn.execute('DELETE FROM web_copy_leaders WHERE id=?',(leader_id,)).rowcount!=1: raise ObserverError('Трейдер не найден.')


def status():
    with db.get_db() as conn:
        conn.execute('BEGIN')  # the notification watermark and displayed signals share a snapshot
        leaders=[dict(r) for r in conn.execute('SELECT * FROM web_copy_leaders ORDER BY id')]
        rows=conn.execute('''SELECT s.*,l.wallet,l.label FROM web_copy_signals s
            JOIN web_copy_leaders l ON l.id=s.leader_id ORDER BY s.observed_at DESC,s.rowid DESC LIMIT 100''').fetchall()
        heartbeat=conn.execute('SELECT checked_at FROM web_copy_service WHERE id=1').fetchone()
        notification=dict(conn.execute('SELECT epoch,seq FROM web_copy_notifications WHERE id=1').fetchone())
    signals=[]
    for row in rows:
        try: payload=json.loads(row['payload'])
        except (ValueError,TypeError): continue
        if isinstance(payload,dict): signals.append({**payload,'leader_id':row['leader_id'],'wallet':row['wallet'],'label':row['label'],'observed_at':row['observed_at'],'event_key':row['event_key']})
    stamp=heartbeat['checked_at'] if heartbeat else None
    service='running' if stamp and -10<=time.time()-stamp<=90 else 'stale' if stamp else 'not_running'
    return {'service':service,'service_checked_at':stamp,'leaders':leaders,'signals':signals,'notification':notification}



# Only fixed Polymarket endpoints are called, never URLs supplied by the client.
_detail_slots = threading.BoundedSemaphore(2)
_METADATA_FIELDS = ('token_id','condition_id','event_slug','market_slug','title','outcome')
_POSITION_NUMBERS = ('current_size','avg_price','entry_cost_usdc','entry_fees_usdc',
    'current_price','current_value','total_size','realized_pnl','unrealized_pnl',
    'total_pnl','percent_pnl','percent_realized_pnl','first_entry_at','last_event_at')


def notification():
    with db.get_db() as conn:
        return dict(conn.execute('SELECT epoch,seq FROM web_copy_notifications WHERE id=1').fetchone())


def _metadata(tokens):
    data=_get('https://data-api.polymarket.com/v2/tokens',{'token_id':','.join(tokens)})
    if not isinstance(data,dict) or not isinstance(data.get('data'),list):
        raise ObserverError('Ответ справочника исходов недоступен.')
    result={}
    for row in data['data'][:50]:
        if not isinstance(row,dict): continue
        token=str(row.get('token_id') or '')
        condition=str(row.get('condition_id') or '').lower()
        if token not in tokens or not HASH.fullmatch(condition): continue
        result[token]={k:str(row.get(k) or '')[:200] for k in _METADATA_FIELDS}
        # Never truncate a URL slug into a different, apparently valid event.
        for field in ('event_slug','market_slug'):
            value=row.get(field)
            result[token][field]=value if isinstance(value,str) and len(value)<=200 else ''
        result[token]['condition_id']=condition
    return result


def metadata(tokens):
    if not isinstance(tokens,list) or not tokens or len(tokens)>50 or any(not isinstance(t,str) or not TOKEN.fullmatch(t) for t in tokens):
        raise ObserverError('Неверный список исходов.')
    if not _detail_slots.acquire(blocking=False): raise ObserverError('Слишком много запросов, повторите позже.')
    try: return _metadata(list(dict.fromkeys(tokens)))
    except ObserverError: raise
    except Exception: raise ObserverError('Справочник исходов Polymarket временно недоступен.') from None
    finally: _detail_slots.release()


def _history(wallet, condition, token):
    # Bounded market-wide walk; a busy opposite outcome can exhaust the limit.
    params={'user':wallet,'condition':condition,'limit':100,'taker_only':'false'}
    items=[]; cursors=set(); skipped=0; partial=False; last=None
    for page in range(3):
        data=_get(DATA_URL,dict(params))
        if not isinstance(data,dict) or not isinstance(data.get('data'),list) or not isinstance(data.get('pagination'),dict):
            raise ObserverError('История сделок недоступна.')
        pagination=data['pagination'];more=pagination.get('has_more')
        if type(more) is not bool: raise ObserverError('Неизвестна полнота истории.')
        if len(data['data'])>100: partial=True
        for row in data['data'][:100]:
            try: _,event=_trade(row,wallet)
            except ObserverError: skipped+=1;continue
            if last is not None and event['timestamp']>last: partial=True
            last=event['timestamp']
            if event['condition_id']==condition and event['token_id']==token: items.append(event)
        if not more: break
        cursor=pagination.get('next_cursor')
        if not isinstance(cursor,str) or not cursor or cursor in cursors:
            partial=True;break
        cursors.add(cursor);params['cursor']=cursor
        if page==2: partial=True
    return {'trades':items,'partial':partial or skipped>0,'skipped':skipped,'fetched_at':time.time()}


def _positions(wallet,condition,token):
    snapshots={}
    for status in ('OPEN','CLOSED'):
        try:
            data=_get('https://data-api.polymarket.com/v2/positions',{'user':wallet,'condition':condition,'status':status,'limit':100})
            if not isinstance(data,dict) or not isinstance(data.get('data'),list) or not isinstance(data.get('pagination'),dict) or type(data['pagination'].get('has_more')) is not bool:
                raise ValueError('invalid response')
            rows=[]
            for row in data['data'][:100]:
                if not isinstance(row,dict) or str(row.get('proxy_wallet','')).lower()!=wallet or str(row.get('condition_id','')).lower()!=condition or str(row.get('token_id',''))!=token: continue
                result={}
                for field in _POSITION_NUMBERS:
                    value=row.get(field)
                    try:
                        number=Decimal(str(value))
                        result[field]=str(number) if not isinstance(value,bool) and number.is_finite() and abs(number)<=Decimal('1e18') else None
                    except (InvalidOperation,TypeError,ValueError): result[field]=None
                result['status']=str(row.get('status') or '')[:30]
                rows.append(result)
            snapshots[status]={'rows':rows,'partial':data['pagination']['has_more'] or len(data['data'])>100,'fetched_at':time.time()}
        except Exception: snapshots[status]={'error':'Снимок позиции Polymarket недоступен.'}
    return snapshots


def signal_detail(leader_id, event_key):
    if not re.fullmatch(r'[0-9a-f]{64}:[1-9][0-9]{0,5}',event_key):
        raise ObserverError('Сделка не найдена.')
    with db.get_db() as conn:
        row=conn.execute('''SELECT s.payload,l.wallet FROM web_copy_signals s
            JOIN web_copy_leaders l ON l.id=s.leader_id WHERE s.leader_id=? AND s.event_key=?''',
            (leader_id,event_key)).fetchone()
    if not row: raise ObserverError('Сделка не найдена или была удалена.')
    try:
        event=json.loads(row['payload']);wallet=row['wallet'];token=event['token_id'];condition=event['condition_id']
        if not ADDRESS.fullmatch(wallet) or not TOKEN.fullmatch(token) or not HASH.fullmatch(condition): raise ValueError()
    except (ValueError,TypeError,KeyError): raise ObserverError('Данные сохранённой сделки повреждены.') from None
    if not _detail_slots.acquire(blocking=False): raise ObserverError('Слишком много запросов, повторите позже.')
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            history=pool.submit(_history,wallet,condition,token)
            positions=pool.submit(_positions,wallet,condition,token)
            reference=pool.submit(_metadata,[token])
            try: h=history.result()
            except Exception: h={'error':'История сделок Polymarket временно недоступна.'}
            try: m=reference.result().get(token)
            except Exception: m=None
            if m and m['condition_id']!=condition: m=None
            return {'history':h,'positions':positions.result(),'metadata':m}
    finally: _detail_slots.release()


def _trade(row, wallet):
    if not isinstance(row,dict) or str(row.get('proxy_wallet','')).lower()!=wallet: raise ObserverError('Неверный кошелёк в ответе ленты.')
    token=str(row.get('token_id') or row.get('position_id') or '')
    condition=str(row.get('condition_id') or '')
    tx=str(row.get('transaction_hash') or '')
    side=row.get('side')
    stamp=row.get('timestamp')
    if not TOKEN.fullmatch(token) or not HASH.fullmatch(condition) or not HASH.fullmatch(tx) or side not in ('BUY','SELL') or type(stamp) is not int or stamp<0 or stamp>time.time()+60:
        raise ObserverError('Некорректные данные сделки в публичной ленте.')
    try:
        size=_decimal(row.get('size'),Decimal('0.000000000001'),Decimal('1000000000'),18)
        price=_decimal(row.get('price'),Decimal('0.000000000001'),Decimal('0.999999999999'),18)
    except ObserverError: raise ObserverError('Некорректный размер или цена сделки в публичной ленте.') from None
    canonical={'timestamp':stamp,'condition_id':condition.lower(),'token_id':token,'transaction_hash':tx.lower(),'side':side,'size':str(size.normalize()),'price':str(price.normalize())}
    key=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    index=row.get('outcome_index')
    return key,{**canonical,'title':str(row.get('title') or '')[:200],
        'outcome':str(row.get('outcome') or '')[:100], 'event_slug':str(row.get('event_slug') or '')[:300],
        'market_slug':str(row.get('slug') or '')[:300],
        'outcome_index':index if type(index) is int and 0<=index<999 else None,
        'trader_name':str(row.get('name') or '')[:100], 'pseudonym':str(row.get('pseudonym') or '')[:100]}


def fetch_trades(wallet, since):
    params={'user':wallet,'limit':PAGE_SIZE,'taker_only':'false'}
    cursor=None;items=[];last=None;seen=set()
    for _ in range(MAX_PAGES):
        if cursor is not None: params['cursor']=cursor
        data=_get(DATA_URL,dict(params))
        if not isinstance(data,dict) or not isinstance(data.get('data'),list) or not isinstance(data.get('pagination'),dict): raise ObserverError('Неверный ответ публичной ленты.')
        pagination=data['pagination'];more=pagination.get('has_more')
        if type(more) is not bool: raise ObserverError('Неизвестна полнота публичной ленты.')
        finished=False
        for row in data['data']:
            key,event=_trade(row,wallet)
            if last is not None and event['timestamp']>last: raise ObserverError('Нарушен порядок публичной ленты.')
            last=event['timestamp']
            if last<since: finished=True;break
            items.append((key,event))
        if finished or not more: return items
        cursor=pagination.get('next_cursor')
        if not isinstance(cursor,str) or not cursor or cursor in seen: raise ObserverError('Не удалось прочитать всю ленту сделок.')
        seen.add(cursor)
    raise ObserverError('Слишком много сделок: лента за период не прочитана полностью.')


def evaluate(event, leader, book):
    price=Decimal(event['price']);size=Decimal(event['size']);cap=Decimal(leader['cap'])
    if event['side']=='BUY':
        if leader['mode']=='fixed': desired=Decimal(leader['amount'])/price
        elif leader['mode']=='same': desired=size
        else: desired=size*Decimal(leader['amount'])/100
        capped=desired*price>cap
        desired=min(desired,cap/price)
    else:
        # There is no simulated position ledger; don't infer a sale quantity.
        desired=None;capped=False
    result={'desired_shares':str(desired.quantize(Decimal('0.000001'))) if desired is not None else None,
            'desired_value':str((desired*price).quantize(Decimal('0.000001'))) if desired is not None else None,
            'capped':capped,'book_available':book is not None,'available_shares':None,'best_price':None,
            'book_at':book.get('fetched_at') if book else None,
            'book_levels':{'bids':book['bids'],'asks':book['asks']} if book is not None else None}
    if book is not None:
        levels=book['asks'] if event['side']=='BUY' else book['bids']
        result['best_price']=levels[0]['price'] if levels else None
        total=sum((Decimal(x['size']) for x in levels if (Decimal(x['price'])<=price if event['side']=='BUY' else Decimal(x['price'])>=price)),Decimal(0))
        result['available_shares']=str(total)
    return result


def poll_leader(leader, now=None):
    now=time.time() if now is None else now
    since=max(leader['started_at'],int(leader['last_success'] or leader['started_at'])-OVERLAP)
    try:
        rows=fetch_trades(leader['wallet'],since)
        counts=collections.Counter();candidates=[]
        for key,event in rows:
            counts[key]+=1
            event_key=f'{key}:{counts[key]}'
            if event['timestamp']<=leader['started_at']: continue
            candidates.append((event_key,event))
        with db.get_db() as conn:
            existing={r['event_key'] for r in conn.execute('SELECT event_key FROM web_copy_signals WHERE leader_id=?',(leader['id'],))}
        new=[(key,event) for key,event in candidates if key not in existing]
        if len(new)>MAX_NEW: raise ObserverError('Слишком много новых сделок за один проход; оценка не проведена.')
        # A bounded number of requests; the remainder are explicitly unavailable.
        tokens=list(dict.fromkeys(event['token_id'] for _,event in new))[:10]
        books=orderbook.fetch_batch(tokens) if tokens else {}
        evaluated=[(key,{**event,'estimate':evaluate(event,leader,books.get(event['token_id']))}) for key,event in new]
        with db.get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=conn.execute('SELECT enabled,revision FROM web_copy_leaders WHERE id=?',(leader['id'],)).fetchone()
            if not current or not current['enabled'] or current['revision']!=leader['revision']: return
            for key,payload in evaluated:
                conn.execute('INSERT OR IGNORE INTO web_copy_signals VALUES(?,?,?,?,?)',
                    (leader['id'],key,payload['timestamp'],time.time(),json.dumps(payload,ensure_ascii=True,allow_nan=False)))
            conn.execute("UPDATE web_copy_leaders SET last_attempt=?,last_success=?,status='ok',error_reason=NULL WHERE id=?",(now,now,leader['id']))
            # Bounded retained data. An older duplicate may reappear after pruning;
            # this observer never trades and should not be reused as an execution ledger.
            conn.execute('''DELETE FROM web_copy_signals WHERE leader_id=? AND rowid NOT IN
                (SELECT rowid FROM web_copy_signals WHERE leader_id=? ORDER BY observed_at DESC,rowid DESC LIMIT 5000)''',(leader['id'],leader['id']))
    except Exception as exc:
        # Only our own static validation messages are exposed; upstream errors may contain URLs or secrets.
        reason=str(exc) if isinstance(exc,ObserverError) else 'Не удалось получить или обработать данные Polymarket.'
        with db.get_db() as conn:
            conn.execute("UPDATE web_copy_leaders SET last_attempt=?,status='error',error_reason=? WHERE id=? AND enabled=1 AND revision=?",(now,reason[:300],leader['id'],leader['revision']))


def poll_once():
    now=time.time()
    with db.get_db() as conn:
        conn.execute('INSERT INTO web_copy_service(id,checked_at) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at',(now,))
        leaders=[dict(r) for r in conn.execute('SELECT * FROM web_copy_leaders WHERE enabled=1 ORDER BY COALESCE(last_attempt,0),id')]
    for leader in leaders:
        if leader['last_attempt'] is None or now-leader['last_attempt']>=leader['interval_seconds'] or now<leader['last_attempt']-10:
            poll_leader(leader)


def acquire_lock():
    directory=trading_account._private_directory(create=True)
    fd=os.open(os.path.join(directory,'copy-observer.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1: raise RuntimeError('Небезопасный файл блокировки.')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd);raise


def main():
    init_db()
    try: fd=acquire_lock()
    except (OSError,RuntimeError): print('Монитор копирования уже запущен или файл блокировки небезопасен.',flush=True);return 1
    stopped=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopped.set());signal.signal(signal.SIGINT,lambda *_:stopped.set())
    try:
        while not stopped.is_set():
            try: poll_once()
            except Exception: print('Ошибка наблюдения за сделками; повторим позже.',flush=True)
            stopped.wait(5)
    finally: os.close(fd)
    return 0


if __name__=='__main__': raise SystemExit(main())
