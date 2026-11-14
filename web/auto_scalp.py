"""Demo-only weather scalper. No credentials, signing or real order submission.

Snapshots are not an exchange simulator: fills below are hypothetical and are
intentionally modelled against displayed opposing liquidity, without queue priority.
"""
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import threading
import time
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN

import requests
import database as db
from web import orderbook, temperature_scan, trading_account, opportunity_scan, market_station, scalp_forecast

D=Decimal
DEFAULT={'initial_balance':'1000','trade_cap':'50','total_cap':'500','min_shares':'5',
         'min_price':'0.85','max_price':'0.985','target_cents':'5','min_profit':'0.03',
         'max_positions':5,'daily_loss':'5','interval_seconds':30,'fee_rate':'0.05',
         'forecast_mode':'off','forecast_gap':'3','forecast_gap_units':'C'}
DISCOVERY_INTERVAL=120
MAX_CANDIDATES=500
BATCH_SIZE=50
BOOK_LIMIT=100
FORECAST_FETCHES_PER_PASS=2

class ScalpError(Exception): pass


def init_db():
    with db.get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_config (
            id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 1, settings TEXT NOT NULL, balance TEXT NOT NULL,
            heartbeat REAL, scan_at REAL, status TEXT NOT NULL DEFAULT 'not_running', message TEXT NOT NULL DEFAULT '')''')
        c.execute('INSERT OR IGNORE INTO web_scalp_config(id,settings,balance) VALUES(1,?,?)',(json.dumps(DEFAULT),DEFAULT['initial_balance']))
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_demo_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL, condition_id TEXT NOT NULL,
            event_slug TEXT NOT NULL, title TEXT NOT NULL, label TEXT NOT NULL, opened REAL NOT NULL,
            original TEXT NOT NULL, remaining TEXT NOT NULL, entry_price TEXT NOT NULL,
            entry_fee TEXT NOT NULL, target TEXT NOT NULL, realized TEXT NOT NULL DEFAULT '0', closed REAL)''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(web_scalp_demo_positions)')}
        if 'forecast_info' not in cols:c.execute('ALTER TABLE web_scalp_demo_positions ADD COLUMN forecast_info TEXT')
        c.execute('CREATE INDEX IF NOT EXISTS web_scalp_open_positions ON web_scalp_demo_positions(id) WHERE closed IS NULL')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_demo_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, position_id INTEGER,
            kind TEXT NOT NULL, size TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
            pnl TEXT, note TEXT NOT NULL)''')
        c.execute("CREATE INDEX IF NOT EXISTS web_scalp_sell_timestamp ON web_scalp_demo_log(timestamp) WHERE kind='SELL'")
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_demo_book_usage (
            token TEXT NOT NULL, side TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(token,side,digest))''')
    scalp_forecast.init_db()


def _number(value,low,high,precision=6):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)) or len(str(value))>30: raise ScalpError('Неверное числовое значение.')
    try: n=D(str(value))
    except (InvalidOperation,ValueError): raise ScalpError('Неверное числовое значение.') from None
    if not n.is_finite() or n<low or n>high or n.as_tuple().exponent < -precision: raise ScalpError('Число вне диапазона или содержит слишком много знаков.')
    return n


def validate_settings(data):
    if not isinstance(data,dict): raise ScalpError('Неверные настройки.')
    rules={'initial_balance':(D('1'),D('1000000')),'trade_cap':(D('1'),D('100000')),
           'total_cap':(D('1'),D('1000000')),'min_shares':(D('0.01'),D('100000')),
           'min_price':(D('0.01'),D('0.999')),'max_price':(D('0.01'),D('0.999')),
           'target_cents':(D('0.1'),D('99')),'min_profit':(D('0'),D('100000')),
           'daily_loss':(D('0.01'),D('100000')),'fee_rate':(D('0'),D('0.2'))}
    result={key:str(_number(data.get(key),*bounds)) for key,bounds in rules.items()}
    result['forecast_gap']=str(_number(data.get('forecast_gap',DEFAULT['forecast_gap']),D('0.1'),D('50'),precision=2))
    result['forecast_mode']=data.get('forecast_mode',DEFAULT['forecast_mode'])
    result['forecast_gap_units']=data.get('forecast_gap_units',DEFAULT['forecast_gap_units'])
    if result['forecast_mode'] not in ('off','forecast') or result['forecast_gap_units'] not in ('C','F'):
        raise ScalpError('Неверный режим прогноза или единицы отступа.')
    for key,low,high in [('max_positions',1,50),('interval_seconds',15,300)]:
        v=data.get(key)
        if type(v) is not int or not low<=v<=high: raise ScalpError('Неверный интервал или число позиций.')
        result[key]=v
    if D(result['min_price'])>D(result['max_price']): raise ScalpError('Минимальная цена выше максимальной.')
    if D(result['trade_cap'])>D(result['total_cap']): raise ScalpError('Лимит одной сделки превышает общий лимит.')
    return result


def set_settings(data):
    settings=validate_settings(data)
    with db.get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        cfg=c.execute('SELECT * FROM web_scalp_config WHERE id=1').fetchone()
        if cfg['enabled']: raise ScalpError('Сначала остановите новые входы.')
        old=json.loads(cfg['settings'])
        if D(settings['initial_balance'])!=D(old['initial_balance']):
            if c.execute('SELECT 1 FROM web_scalp_demo_positions LIMIT 1').fetchone(): raise ScalpError('Начальный баланс нельзя менять после первой демо-сделки.')
            c.execute('UPDATE web_scalp_config SET balance=? WHERE id=1',(settings['initial_balance'],))
        c.execute('UPDATE web_scalp_config SET settings=?,revision=revision+1 WHERE id=1',(json.dumps(settings),))
    return settings


def set_enabled(value):
    if type(value) is not bool: raise ScalpError('Неверное состояние.')
    with db.get_db() as c:
        c.execute('UPDATE web_scalp_config SET enabled=?,revision=revision+1 WHERE id=1',(int(value),))


def status():
    with db.get_db() as c:
        cfg=dict(c.execute('SELECT * FROM web_scalp_config WHERE id=1').fetchone());cfg['settings']={**DEFAULT,**json.loads(cfg['settings'])}
        positions=[dict(x) for x in c.execute('SELECT * FROM web_scalp_demo_positions WHERE closed IS NULL ORDER BY id DESC')]
        positions.extend(dict(x) for x in c.execute('SELECT * FROM web_scalp_demo_positions WHERE closed IS NOT NULL ORDER BY id DESC LIMIT 100'))
        logs=[dict(x) for x in c.execute('''SELECT l.*,p.token,p.condition_id,p.event_slug,p.title,p.label,p.forecast_info
            FROM web_scalp_demo_log l LEFT JOIN web_scalp_demo_positions p ON p.id=l.position_id
            ORDER BY l.id DESC LIMIT 200''')]
    for row in positions+logs:
        try:row['forecast_info']=json.loads(row['forecast_info']) if row.get('forecast_info') else None
        except (ValueError,TypeError):row['forecast_info']=None
    age=time.time()-cfg['heartbeat'] if cfg['heartbeat'] else None
    cfg['service']='running' if age is not None and -10<=age<=max(90,3*cfg['settings']['interval_seconds']) else 'stale' if age is not None else 'not_running'
    return {'config':cfg,'positions':positions,'journal':logs,'real_available':False}


def _gamma_events():
    events=[];seen=set();incomplete=False
    for tag in ('daily-temperature','temperature'):
        for page in range(5):
            r=requests.get('https://gamma-api.polymarket.com/events',params={'tag_slug':tag,'active':'true','closed':'false','limit':100,'offset':page*100,'order':'id','ascending':'false'},timeout=(3,8),headers={'User-Agent':'gradusbot-web/1.0'})
            r.raise_for_status();data=r.json()
            if not isinstance(data,list): raise ValueError('invalid Gamma')
            for event in data:
                if isinstance(event,dict) and event.get('slug') not in seen:
                    seen.add(event.get('slug'));events.append(event)
            if len(data)<100:break
        else:incomplete=True
    return events,incomplete


def candidates(events,now):
    today=datetime.fromtimestamp(now,timezone.utc).date();out=[];seen=set()
    for event in events:
        if not isinstance(event,dict) or event.get('active') is not True or event.get('closed') is not False: continue
        slug=event.get('slug')
        if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{1,200}',slug):continue
        day=temperature_scan.observation_date(slug)
        if not day or not today-timedelta(days=1)<=date.fromisoformat(day)<=today+timedelta(days=2):continue
        try:
            # Gamma endDate is a declared market date, NOT a guarantee of resolution time.
            end=datetime.fromisoformat(str(event.get('endDate','')).replace('Z','+00:00'))
            if end.tzinfo is None or not 0<=end.timestamp()-now<=48*3600:continue
        except (ValueError,OverflowError,TypeError):continue
        resolved=market_station.identify_station(event)
        evidence=resolved.get('candidate') or {}
        station=evidence.get('code') if resolved.get('status')=='found' and evidence.get('kind')!='site в правилах' and not temperature_scan.is_hong_kong_observatory(event) else None
        kind='max' if slug.startswith('highest-') else 'min' if slug.startswith('lowest-') else None
        markets=event.get('markets')
        if not isinstance(markets,list):continue
        for market in markets[:100]:
            if not isinstance(market,dict) or market.get('active') is not True or market.get('closed') is not False or market.get('acceptingOrders') is not True:continue
            if str(market.get('version','')).lower() in ('v2','2') or market.get('positionIds'):continue
            label=market.get('groupItemTitle')
            temp_range=opportunity_scan.temperature_range(label)
            if temp_range is None:continue
            unit_match=re.search(r'[°º]\s*([CF])',label,re.I)
            if not unit_match:continue
            unit=unit_match.group(1).upper()
            pair=orderbook.event_tokens({'markets':[market]})
            names=orderbook._list(market.get('outcomes'));prices=orderbook._list(market.get('outcomePrices'))
            try:
                idx=[str(x).strip().lower() for x in names].index('yes')
                if len(prices)!=2 or isinstance(prices[idx],bool):continue
                yes=D(str(prices[idx]))
                if not yes.is_finite() or not 0<=yes<=D('0.15'):continue
            except (ValueError,InvalidOperation,IndexError):continue
            condition=str(market.get('conditionId') or '')
            if not pair or not re.fullmatch(r'0x[0-9a-fA-F]{64}',condition):continue
            token=pair[0]['no']
            if token in seen:continue
            seen.add(token)
            out.append({'token':token,'condition_id':condition.lower(),'event_slug':slug,
                        'title':str(event.get('title') or '')[:200],'label':label.strip()[:100],
                        'end':end.timestamp(),'temperature_range':temp_range,'unit':unit,
                        'station':station,'station_reason':('Конфликт станций в правилах' if resolved.get('status')=='conflict' else 'Станция разрешения не определена однозначно') if not station else None,'date':day,'extreme':kind})
            if len(out)>=MAX_CANDIDATES:return out,True
    return out,False


def prepare_forecasts(items,settings,now=None):
    """Apply forecast gap, or clearly labelled price-only fallback on missing data.

    A two-station fetch budget bounds latency and public provider traffic. Existing
    cached forecasts don't consume that budget. Forecast distance is a heuristic,
    not a probability, and a half-degree margin guards bucket rounding.
    """
    now=time.time() if now is None else now
    settings={**DEFAULT,**settings}
    stats={'forecast':0,'fallback':0,'excluded':0}
    if settings['forecast_mode']=='off':
        return [{**item,'forecast_info':{'mode':'off'}} for item in items],stats
    result=[];station_cache={};day_cache={};budget=FORECAST_FETCHES_PER_PASS;reasons={}
    for item in items:
        code=item.get('station');key=(code,item.get('date'),item.get('extreme'))
        if not code:
            forecast=None;reason=item.get('station_reason') or 'Станция разрешения не определена однозначно'
        else:
            if code not in station_cache:
                data,reason=scalp_forecast.get(code,permit_fetch=False,now=now)
                if data is None and reason=='Лимит запросов прогноза за проход' and budget>0:
                    budget-=1;data,reason=scalp_forecast.get(code,permit_fetch=True,now=now)
                station_cache[code]=(data,reason)
            data,reason=station_cache[code]
            if key not in day_cache:
                day_cache[key]=scalp_forecast.extreme(data,item.get('date'),item.get('extreme')) if data else None
            forecast=day_cache[key]
            if data and not forecast:reason='Прогноз на местные сутки неполон или недоступен'
        if not forecast:
            stats['fallback']+=1
            reasons[reason or 'Прогноз недоступен']=reasons.get(reason or 'Прогноз недоступен',0)+1
            result.append({**item,'forecast_info':{'mode':'fallback','reason':reason or 'Прогноз недоступен',
                'station':code,'day':item.get('date')}})
            continue
        low,high=item['temperature_range']
        # Polymarket commonly resolves integer degree buckets by rounding;
        # widen all finite bounds by half a degree in the market's own units.
        margin=.5 if item['unit']=='C' else 5/18
        expanded=(None if low is None else low-margin,None if high is None else high+margin)
        gap=opportunity_scan.range_gap((forecast['value_c'],forecast['value_c']),expanded)
        gap_display=gap*(1.8 if settings['forecast_gap_units']=='F' else 1)
        if gap_display+1e-9<float(settings['forecast_gap']):
            stats['excluded']+=1;continue
        stats['forecast']+=1
        info={'mode':'forecast',**forecast,'distance':gap_display,'distance_units':settings['forecast_gap_units'],
              'required_gap':settings['forecast_gap'],'bucket_margin':f"0.5 °{item['unit']}"}
        result.append({**item,'forecast_info':info})
    if reasons:stats['reasons']=reasons
    # A large forecast gap is only a ranking heuristic; missing forecasts last.
    result.sort(key=lambda item:(item['forecast_info']['mode']!='forecast',
                               -item['forecast_info'].get('distance',0)))
    return result,stats


def _fee(size,price,rate):
    # Conservative cost estimate; actual venue fees, rounding and fee units may differ.
    return size*rate*price*(D(1)-price)


def _hash_book(book):
    return hashlib.sha256(json.dumps({'asks':book['asks'],'bids':book['bids']},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _record(c,kind,position_id,size,price,fee,pnl,note,now):
    c.execute('INSERT INTO web_scalp_demo_log(timestamp,position_id,kind,size,price,fee,pnl,note) VALUES(?,?,?,?,?,?,?,?)',
              (now,position_id,kind,str(size),str(price),str(fee),str(pnl) if pnl is not None else None,note))


def _seen(c,token,side,digest):
    return c.execute('SELECT 1 FROM web_scalp_demo_book_usage WHERE token=? AND side=? AND digest=?',(token,side,digest)).fetchone() is not None


def process_books(market_list,books,now=None,expected_revision=None):
    """Atomic demo ledger; repeats of the same snapshot cannot refill the same side."""
    now=time.time() if now is None else now
    with db.get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        cfg=c.execute('SELECT * FROM web_scalp_config WHERE id=1').fetchone();settings={**DEFAULT,**json.loads(cfg['settings'])}
        balance=D(cfg['balance']);rate=D(settings['fee_rate'])
        opened=[dict(x) for x in c.execute('SELECT * FROM web_scalp_demo_positions WHERE closed IS NULL ORDER BY id')]
        # Always monitor hypothetical exits, even after stopping new entries.
        for p in opened:
            book=books.get(p['token']);stamp=book.get('fetched_at') if isinstance(book,dict) else None
            if not book or not isinstance(stamp,(int,float)) or not 0<=now-stamp<=15 or not book.get('bids'):continue
            digest=_hash_book(book)
            if _seen(c,p['token'],'SELL',digest):continue
            eligible=[(D(x['price']),D(x['size'])) for x in book['bids'] if D(x['price'])>=D(p['target'])]
            remaining=D(p['remaining']);sold=cost=fees=D(0)
            for price,qty in eligible:
                take=min(remaining-sold,qty)
                if take<=0:break
                sold+=take;cost+=take*price;fees+=_fee(take,price,rate)
            if sold<=0:continue
            original=D(p['original']);entry_cost=sold*D(p['entry_price']);entry_fee=D(p['entry_fee'])*sold/original
            pnl=cost-fees-entry_cost-entry_fee;remaining-=sold;balance+=cost-fees
            c.execute('UPDATE web_scalp_demo_positions SET remaining=?,realized=?,closed=? WHERE id=?',
                      (str(remaining),str(D(p['realized'])+pnl),now if remaining==0 else None,p['id']))
            c.execute('INSERT INTO web_scalp_demo_book_usage VALUES(?,?,?)',(p['token'],'SELL',digest))
            _record(c,'SELL',p['id'],sold,cost/sold,fees,pnl,'Виртуальная продажа по отображённым BID; не реальное исполнение.',now)
        if cfg['enabled'] and (expected_revision is None or cfg['revision']==expected_revision):
            opened=[dict(x) for x in c.execute('SELECT * FROM web_scalp_demo_positions WHERE closed IS NULL')]
            risk=sum((D(p['remaining'])*D(p['entry_price'])+D(p['entry_fee'])*D(p['remaining'])/D(p['original']) for p in opened),D(0))
            day_start=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            day_pnl=sum((D(x['pnl']) for x in c.execute("SELECT pnl FROM web_scalp_demo_log WHERE timestamp>=? AND kind='SELL' AND pnl IS NOT NULL",(day_start,))),D(0))
            for item in market_list:
                if settings['forecast_mode']=='forecast' and (not isinstance(item.get('forecast_info'),dict) or item['forecast_info'].get('mode') not in ('forecast','fallback')):continue
                if len(opened)>=settings['max_positions'] or day_pnl<=-D(settings['daily_loss']):break
                if item['end']<=now or any(p['token']==item['token'] for p in opened):continue
                book=books.get(item['token']);stamp=book.get('fetched_at') if isinstance(book,dict) else None
                if not book or not isinstance(stamp,(int,float)) or not 0<=now-stamp<=15 or not book.get('asks'):continue
                digest=_hash_book(book)
                if _seen(c,item['token'],'BUY',digest):continue
                ask=D(book['asks'][0]['price']);qty=D(book['asks'][0]['size']);target=ask+D(settings['target_cents'])/100
                if not D(settings['min_price'])<=ask<=D(settings['max_price']) or not ask<target<1:continue
                max_budget=min(D(settings['trade_cap']),D(settings['total_cap'])-risk,balance)
                unit=ask+_fee(D(1),ask,rate)
                size=(min(qty,max_budget/unit)).quantize(D('0.01'),rounding=ROUND_DOWN)
                fee=_fee(size,ask,rate);profit=size*(target-ask)-fee-_fee(size,target,rate)
                if size<D(settings['min_shares']) or profit<D(settings['min_profit']):continue
                value=size*ask+fee
                result=c.execute('''INSERT INTO web_scalp_demo_positions(token,condition_id,event_slug,title,label,opened,original,remaining,entry_price,entry_fee,target,forecast_info)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(item['token'],item['condition_id'],item['event_slug'],item['title'],item['label'],now,str(size),str(size),str(ask),str(fee),str(target),json.dumps(item.get('forecast_info'),ensure_ascii=True,allow_nan=False) if item.get('forecast_info') else None))
                pid=result.lastrowid;balance-=value;risk+=value
                opened.append({'token':item['token']})
                c.execute('INSERT INTO web_scalp_demo_book_usage VALUES(?,?,?)',(item['token'],'BUY',digest))
                _record(c,'BUY',pid,size,ask,fee,None,'Виртуальная покупка по лучшему ASK; не реальное исполнение.',now)
        c.execute('UPDATE web_scalp_config SET balance=? WHERE id=1',(str(balance),))


_discovery=None
_cursor=0

def poll_once():
    global _discovery,_cursor
    now=time.time()
    with db.get_db() as c:
        cfg=dict(c.execute('SELECT * FROM web_scalp_config WHERE id=1').fetchone())
        active=[r['token'] for r in c.execute('SELECT token FROM web_scalp_demo_positions WHERE closed IS NULL ORDER BY id')]
        c.execute("UPDATE web_scalp_config SET heartbeat=?,status='checking',message='' WHERE id=1",(now,))
    try:
        settings={**DEFAULT,**json.loads(cfg['settings'])}
        # Do not delay monitoring existing virtual exits behind forecast requests.
        if active:
            exit_books={}
            for i in range(0,min(len(active),BOOK_LIMIT),BATCH_SIZE):
                exit_books.update(orderbook.fetch_batch(active[i:i+BATCH_SIZE]))
            process_books([],exit_books,time.time(),expected_revision=cfg['revision'])
            with db.get_db() as c:
                active=[r['token'] for r in c.execute('SELECT token FROM web_scalp_demo_positions WHERE closed IS NULL ORDER BY id')]
        positions_full=len(active)>=settings['max_positions']
        discovery_error=False
        if cfg['enabled'] and not positions_full and (not _discovery or now-_discovery[0]>=DISCOVERY_INTERVAL):
            try:
                events,incomplete=_gamma_events();items,truncated=candidates(events,now)
                _discovery=(now,items,incomplete or truncated)
            except Exception:
                # Stop entries when discovery cannot be refreshed, but still monitor open exits.
                discovery_error=True
        all_items=_discovery[1] if cfg['enabled'] and not positions_full and not discovery_error and _discovery and 0<=now-_discovery[0]<=DISCOVERY_INTERVAL*2 else []
        capacity=max(0,BOOK_LIMIT-len(set(active)))
        if all_items and capacity:
            offset=_cursor%len(all_items);window=(all_items+all_items)[offset:offset+min(capacity,len(all_items))];_cursor=(offset+len(window))%len(all_items)
        else:window=[]
        filtered,forecast_stats=prepare_forecasts(window,settings,now)
        tokens=list(dict.fromkeys(m['token'] for m in filtered))[:capacity];books={}
        for i in range(0,len(tokens),BATCH_SIZE):books.update(orderbook.fetch_batch(tokens[i:i+BATCH_SIZE]))
        now=time.time()
        # A long network pass must not use a stale snapshot as a fresh forecast.
        for item in filtered:
            info=item.get('forecast_info') or {}
            if info.get('mode')=='forecast' and not 0<=now-info.get('fetched_at',0)<=scalp_forecast.FORECAST_TTL:
                item['forecast_info']={'mode':'fallback','reason':'Прогноз устарел во время проверки','station':item.get('station'),'day':item.get('date')}
                forecast_stats['forecast']-=1;forecast_stats['fallback']+=1
                reasons=forecast_stats.setdefault('reasons',{});reason='Прогноз устарел во время проверки';reasons[reason]=reasons.get(reason,0)+1
        if filtered:process_books(filtered,books,now,expected_revision=cfg['revision'])
        unavailable=sum(1 for t in tokens if books.get(t) is None)
        with db.get_db() as c:c.execute("UPDATE web_scalp_config SET heartbeat=?,scan_at=?,status=?,message=? WHERE id=1",
            (now,now,'partial' if unavailable or discovery_error or forecast_stats['fallback'] or (_discovery and _discovery[2]) else 'ok',f'Кандидатов: {len(all_items)}; проверено: {len(window)}; недоступных стаканов: {unavailable}.'+(f" Прогноз: прошли {forecast_stats['forecast']}, исключены {forecast_stats['excluded']}, без прогноза / только цена {forecast_stats['fallback']}."+(' Причины: '+', '.join(f'{k}: {v}' for k,v in forecast_stats.get('reasons',{}).items())+'.' if forecast_stats.get('reasons') else '') if settings['forecast_mode']=='forecast' else ' Погодный фильтр выключен.')+(' Достигнут лимит открытых позиций; проверяются только виртуальные выходы.' if cfg['enabled'] and positions_full else '')+(' Каталог недоступен; новые входы пропущены.' if discovery_error else ' Каталог ограничен.' if _discovery and _discovery[2] else '')))
    except Exception:
        with db.get_db() as c:c.execute("UPDATE web_scalp_config SET heartbeat=?,status='error',message='Не удалось получить или обработать публичные данные.' WHERE id=1",(time.time(),))


def acquire_lock():
    directory=trading_account._private_directory(create=True)
    fd=os.open(os.path.join(directory,'auto-scalp-demo.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1:raise RuntimeError('unsafe lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return fd
    except Exception:os.close(fd);raise


def main():
    init_db()
    try:fd=acquire_lock()
    except (OSError,RuntimeError):print('Демо-скальпер уже запущен или файл блокировки небезопасен.',flush=True);return 1
    stopped=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopped.set());signal.signal(signal.SIGINT,lambda *_:stopped.set())
    try:
        while not stopped.is_set():
            start=time.monotonic();poll_once()
            with db.get_db() as c:settings=json.loads(c.execute('SELECT settings FROM web_scalp_config WHERE id=1').fetchone()[0])
            stopped.wait(max(1,settings['interval_seconds']-(time.monotonic()-start)))
    finally:os.close(fd)
    return 0

if __name__=='__main__':raise SystemExit(main())
