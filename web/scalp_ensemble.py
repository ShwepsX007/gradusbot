"""Observational model distributions for the demo scalper; never submits trades.

The background observer only handles a small bounded set of open demo positions
and recent demo candidates. Its results cannot influence the demo ledger.
"""
import json
import math
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import database as db
from web import scalp_forecast, opportunity_scan, temperature_scan, orderbook, market_station

MODELS={'ECMWF IFS':('ecmwf_ifs025',51),'GEFS':('gfs025',31)}
URL='https://ensemble-api.open-meteo.com/v1/ensemble'
TTL=6*3600
RETRY=30*60
INTERVAL=120
MAX_BYTES=1024*1024
MAX_ITEMS=50
VIEW_LEASE=150
RETAIN=8*86400
_lock=threading.Lock()
_candidates=[]
_cursor=0
_last_cleanup=0


def init_db():
    with db.get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_ensemble_cache (
          station TEXT NOT NULL, model TEXT NOT NULL, payload TEXT, fetched REAL, lat TEXT, lon TEXT,
          next_attempt REAL NOT NULL DEFAULT 0, error TEXT, PRIMARY KEY(station,model))''')
        cols={x['name'] for x in c.execute('PRAGMA table_info(web_scalp_ensemble_cache)')}
        for col in ('lat','lon'):
            if col not in cols:c.execute(f'ALTER TABLE web_scalp_ensemble_cache ADD COLUMN {col} TEXT')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_ensemble_views (
          slug TEXT PRIMARY KEY, station TEXT NOT NULL, day TEXT NOT NULL, touched REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_ensemble_results (
          token TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_ensemble_snapshots (
          position_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, captured REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_ensemble_control (
          id INTEGER PRIMARY KEY CHECK(id=1), blocked_until REAL NOT NULL DEFAULT 0)''')
        cols={x['name'] for x in c.execute('PRAGMA table_info(web_scalp_ensemble_control)')}
        if 'last_run' not in cols:c.execute('ALTER TABLE web_scalp_ensemble_control ADD COLUMN last_run REAL')
        if 'last_error' not in cols:c.execute('ALTER TABLE web_scalp_ensemble_control ADD COLUMN last_error TEXT')
        c.execute('INSERT OR IGNORE INTO web_scalp_ensemble_control(id) VALUES(1)')


def offer_candidates(items, books):
    """Copy a bounded snapshot; no blocking I/O and no mutation of trade inputs."""
    global _candidates
    result=[]
    for item in items[:MAX_ITEMS]:
        token=item.get('token')
        result.append({k:item.get(k) for k in ('token','condition_id','event_slug','title','label','station','date','extreme','end')})
        result[-1]['book']=books.get(token);result[-1]['offered_at']=time.time()
    with _lock:_candidates=result


def _json_response(r):
    r.raise_for_status()
    parts=[];size=0;start=time.monotonic()
    for chunk in r.iter_content(65536):
        size+=len(chunk)
        if size>MAX_BYTES or time.monotonic()-start>12:raise ValueError('oversized or slow response')
        parts.append(chunk)
    data=json.loads(b''.join(parts))
    if not isinstance(data,dict):raise ValueError('invalid response')
    return data


def fetch_model(lat,lon,model):
    """One model at a time, UTC hourly instants (provider may interpolate)."""
    response=None
    try:
        response=requests.get(URL,params={'latitude':lat,'longitude':lon,'models':model,
            'hourly':'temperature_2m','temperature_unit':'celsius','timezone':'UTC',
            'timeformat':'unixtime','forecast_days':4},headers=scalp_forecast.AGENT,
            timeout=(3,8),stream=True)
        if response.status_code in (403,429):return None,'Сервис ограничил запросы',True
        return _json_response(response),None,False
    except Exception:return None,'Модель недоступна',False
    finally:
        if response is not None:response.close()


def _valid_response(data,lat,lon,expected):
    """Reject wrong locations and structurally incomplete provider replies."""
    try:
        a=float(data['latitude']);b=float(data['longitude']);lat=float(lat);lon=float(lon)
        if not all(math.isfinite(x) for x in (a,b,lat,lon)):return False
        if abs(a-lat)>.5 or abs((b-lon+180)%360-180)>.5:return False
        if data.get('hourly_units',{}).get('time')!='unixtime':return False
        if data.get('hourly_units',{}).get('temperature_2m') not in ('°C','C','celsius'):return False
        hourly=data['hourly'];times=hourly['time']
        if not isinstance(times,list) or not 24<=len(times)<=120:return False
        # The control run may be unsuffixed or member00, never count both.
        keys=[key for key in hourly if re.fullmatch(r'temperature_2m_member\d{1,2}',key)]
        count=len(keys)+(1 if 'temperature_2m' in hourly else 0)
        return math.ceil(expected*.9)<=count<=expected
    except (KeyError,TypeError,ValueError,OverflowError):return False


def _day_bounds(day,zone):
    d=date.fromisoformat(day);tz=ZoneInfo(zone)
    start=datetime.combine(d,datetime.min.time(),tzinfo=tz).timestamp()
    end=datetime.combine(d+timedelta(days=1),datetime.min.time(),tzinfo=tz).timestamp()
    if end-start not in (23*3600,24*3600,25*3600):raise ValueError('unexpected day length')
    return start,end


def model_distribution(data,day,zone,kind,label,expected,now):
    """Empirical member frequency; never calls it a calibrated probability."""
    try:
        start,end=_day_bounds(day,zone)
        if start<=now:return None,'Местные сутки уже начались; наблюдения дня не учтены'
        h=data['hourly'];times=h['time'];unit=data.get('hourly_units',{}).get('temperature_2m')
        if unit not in ('°C','C','celsius'):return None,'Единицы модели не подтверждены'
        if kind not in ('max','min') or not isinstance(times,list) or not 1<=len(times)<=120:return None,'Некорректные часы модели'
        if any(type(t) not in (int,float) or not math.isfinite(t) for t in times):return None,'Некорректные часы модели'
        indices=[i for i,t in enumerate(times) if start<=t<end]
        ts=[times[i] for i in indices]
        if len(ts)!=int((end-start)//3600) or any(b-a!=3600 for a,b in zip(ts,ts[1:])):return None,'Модель не покрывает все местные сутки'
        bounds=opportunity_scan.temperature_range(label)
        match=re.search(r'[°º]\s*([CF])',label or '',re.I)
        if bounds is None or not match:return None,'Границы исхода не распознаны'
        margin=.5 if match.group(1).upper()=='C' else 5/18
        lo,hi=bounds;lo=None if lo is None else lo-margin;hi=None if hi is None else hi+margin
        members={}
        if isinstance(h.get('temperature_2m'),list):members[0]=h['temperature_2m']
        for key,values in h.items():
            m=re.fullmatch(r'temperature_2m_member(\d{1,2})',key)
            if m:
                number=int(m.group(1))
                if number in members or number>=expected:return None,'Неоднозначные участники модели'
                members[number]=values
        complete=[]
        for values in members.values():
            if not isinstance(values,list) or len(values)!=len(times):continue
            selected=[values[i] for i in indices]
            if any(type(x) not in (int,float) or not math.isfinite(x) or not -150<=x<=150 for x in selected):continue
            complete.append((max if kind=='max' else min)(selected))
        # Missing members can bias tails; report failure rather than inventing certainty.
        if len(complete)<math.ceil(expected*.9):return None,f'Неполные участники модели: {len(complete)} из {expected}'
        hits=sum((lo is None or v>=lo) and (hi is None or v<=hi) for v in complete)
        n=len(complete)
        return {'yes_count':hits,'no_count':n-hits,'members':n,'expected':expected,
                'yes':hits/n,'no':(n-hits)/n,'min_c':min(complete),'max_c':max(complete),
                'margin':f"0.5 °{match.group(1).upper()}", 'local_start':start},None
    except (KeyError,TypeError,ValueError,OverflowError,OSError):return None,'Некорректные данные модели или часовой пояс'


def _load_cache(station,model):
    with db.get_db() as c:row=c.execute('SELECT * FROM web_scalp_ensemble_cache WHERE station=? AND model=?',(station,model)).fetchone()
    return dict(row) if row else {}


def _position_items():
    with db.get_db() as c:
        rows=c.execute('SELECT id,token,condition_id,event_slug,title,label,opened,entry_price,original,entry_fee,forecast_info FROM web_scalp_demo_positions WHERE closed IS NULL ORDER BY id DESC LIMIT ?', (MAX_ITEMS,)).fetchall()
    out=[]
    for row in rows:
        row=dict(row)
        try:info=json.loads(row.pop('forecast_info') or '{}') or {}
        except (ValueError,TypeError):info={}
        row['station']=info.get('station');row['date']=info.get('day') or temperature_scan.observation_date(row['event_slug'])
        row['extreme']='max' if row['event_slug'].startswith('highest-') else 'min' if row['event_slug'].startswith('lowest-') else None
        row['position_id']=row.pop('id');out.append(row)
    return out


def _coordinates(station,now):
    with db.get_db() as c:row=c.execute('SELECT lat,lon,payload,station_expires FROM web_scalp_forecast_cache WHERE code=?',(station,)).fetchone()
    if row and row['lat'] and row['lon'] and row['station_expires']>now:
        try:
            payload=json.loads(row['payload'] or '{}');zone=payload['open']['timezone']
            if scalp_forecast.forecast_ensemble.valid_timezone(zone):return (row['lat'],row['lon'],zone),None
        except (KeyError,ValueError,TypeError):pass
    payload,reason=scalp_forecast.get(station,permit_fetch=True,now=now)
    if not payload:return None,reason
    with db.get_db() as c:row=c.execute('SELECT lat,lon FROM web_scalp_forecast_cache WHERE code=?',(station,)).fetchone()
    if not row or not row['lat'] or not row['lon']:return None,'Координаты станции недоступны'
    return (row['lat'],row['lon'],payload['open']['timezone']),None


def _refresh_station(station,now,days=None):
    coords,reason=_coordinates(station,now)
    if not coords:return None,reason
    lat,lon,zone=coords
    if days is not None:
        future=False
        for day in days:
            try:future=future or _day_bounds(day,zone)[0]>now
            except (ValueError,TypeError,OverflowError):pass
        if not future:return coords,None
    for name,(model,expected) in MODELS.items():
        row=_load_cache(station,name)
        if now<row.get('next_attempt',0) and (not row.get('payload') or (row.get('lat'),row.get('lon'))==(lat,lon)):continue
        with db.get_db() as c:blocked=c.execute('SELECT blocked_until FROM web_scalp_ensemble_control WHERE id=1').fetchone()[0]
        if now<blocked:break
        data,error,throttled=fetch_model(lat,lon,model)
        current=time.time()
        # Validate basic structure and limit persisted data. Full checks occur per day.
        if data is not None and not _valid_response(data,lat,lon,expected):data=None;error='Некорректный или неполный ответ модели'
        try:payload=json.dumps(data,separators=(',',':'),allow_nan=False) if data is not None else None
        except (ValueError,TypeError):payload=None;error='Некорректный ответ модели'
        with db.get_db() as c:
            c.execute('''INSERT INTO web_scalp_ensemble_cache(station,model,payload,fetched,lat,lon,next_attempt,error)
             VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(station,model) DO UPDATE SET payload=excluded.payload,
             fetched=excluded.fetched,lat=excluded.lat,lon=excluded.lon,next_attempt=excluded.next_attempt,error=excluded.error''',
             (station,name,payload,current if payload else None,lat,lon,current+(TTL if payload else RETRY),error))
            if throttled:c.execute('UPDATE web_scalp_ensemble_control SET blocked_until=MAX(blocked_until,?) WHERE id=1',(current+RETRY,))
        if throttled:break
    return (lat,lon,zone),None


def _quote(book,now,fee_rate):
    if not isinstance(book,dict) or not isinstance(book.get('fetched_at'),(int,float)) or not 0<=now-book['fetched_at']<=120 or not book.get('asks'):return None
    try:
        ask=float(book['asks'][0]['price']);size=float(book['asks'][0]['size']);rate=float(fee_rate)
        if not all(math.isfinite(x) for x in (ask,size,rate)) or not 0<ask<1 or size<=0 or not 0<=rate<=.2:return None
        return {'ask':ask,'size':size,'fetched_at':book['fetched_at'],'fee_per_share':rate*ask*(1-ask),'fee_rate':rate}
    except (ValueError,TypeError,KeyError,IndexError,OverflowError):return None


def _evaluate(item,coords,reason,now,fee_rate,model_cache=None):
    station=item.get('station');day=item.get('date');kind=item.get('extreme')
    result={k:item.get(k) for k in ('token','position_id','opened','condition_id','event_slug','title','label','station','date','extreme')}
    result['observed_at']=now;result['models']=[];result['quote']=_quote(item.get('book'),now,fee_rate)
    if not coords:
        result['reason']=reason or 'Станция разрешения неизвестна';return result
    result['timezone']=coords[2]
    for name,(_,expected) in MODELS.items():
        row=model_cache.get((station,name),{}) if model_cache is not None else _load_cache(station,name)
        entry={'name':name,'fetched_at':row.get('fetched')}
        try:
            if not row.get('payload') or not row.get('fetched') or (row.get('lat'),row.get('lon'))!=tuple(coords[:2]) or not 0<=now-row['fetched']<=TTL:
                entry['reason']=row.get('error') or 'Ожидается обновление модели'
            else:
                distribution,error=model_distribution(row['payload'] if isinstance(row['payload'],dict) else json.loads(row['payload']),day,coords[2],kind,item.get('label'),expected,now)
                if error:entry['reason']=error
                else:
                    entry.update(distribution)
                    if result['quote']:
                        entry['gross_edge']=distribution['no']-result['quote']['ask']
                        entry['net_hold_edge']=entry['gross_edge']-result['quote']['fee_per_share']
        except (ValueError,TypeError):entry['reason']='Некорректные данные модели'
        result['models'].append(entry)
    return result


def capture_entry(connection,position_id,item):
    """Save an already-computed recent observation at entry, without I/O.

    Optional audit only: caller must ignore failures. Never changes decisions.
    """
    now=item['opened']
    row=connection.execute('SELECT payload,updated FROM web_scalp_ensemble_results WHERE token=?',(item['token'],)).fetchone()
    if not row or not 0<=now-row['updated']<=INTERVAL*3:return
    value=json.loads(row['payload'])
    if any(value.get(k)!=item.get(k) for k in ('token','condition_id','event_slug','label','station','date','extreme')):return
    ask=float(item['entry_price']);size=float(item['original']);fee=float(item['entry_fee'])
    if not all(math.isfinite(x) for x in (ask,size,fee)) or not 0<ask<1 or size<=0 or fee<0:return
    quote={'ask':ask,'size':size,'size_kind':'demo_filled','fetched_at':now,'fee_per_share':fee/size}
    models=[]
    for model in value.get('models',[]):
        model=dict(model);no=model.get('no');fetched=model.get('fetched_at');start=model.get('local_start')
        valid=(type(no) in (int,float) and math.isfinite(no) and 0<=no<=1 and
               type(fetched) in (int,float) and 0<=now-fetched<=TTL and
               type(start) in (int,float) and start>now)
        if not valid:
            model.pop('yes',None);model.pop('no',None);model.pop('gross_edge',None);model.pop('net_hold_edge',None)
            model['reason']='Модель недоступна или устарела на момент входа'
        else:
            model['gross_edge']=no-quote['ask'];model['net_hold_edge']=model['gross_edge']-quote['fee_per_share']
        models.append(model)
    if not any('no' in model for model in models):return
    value.update(position_id=position_id,models=models,quote=quote,snapshot_kind='at_entry',captured_at=now)
    connection.execute('INSERT OR IGNORE INTO web_scalp_ensemble_snapshots VALUES(?,?,?)',
        (position_id,json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':')),now))


def overview(event,now=None):
    """Queue only a verified event; compute all ranges from local cache, no I/O.

    Views are separate from demo results/snapshots and cannot change a trade.
    """
    now=time.time() if now is None else now
    slug=event.get('slug','') if isinstance(event,dict) else ''
    day=temperature_scan.observation_date(slug) if isinstance(slug,str) else None
    kind='max' if slug.startswith('highest-temperature-in-') else 'min' if slug.startswith('lowest-temperature-in-') else None
    resolved=market_station.identify_station(event)
    candidate=resolved.get('candidate') or {}
    station=candidate.get('code') if resolved.get('status')=='found' and candidate.get('kind')!='site в правилах' and not temperature_scan.is_hong_kong_observatory(event) else None
    reason=None
    if not kind or not day:reason='Дата или тип температурного рынка не распознаны'
    elif not station:reason='Станция разрешения неизвестна или неоднозначна'
    else:
        today=datetime.fromtimestamp(now,ZoneInfo('UTC')).date()
        if not today-timedelta(days=1)<=date.fromisoformat(day)<=today+timedelta(days=3):
            reason='Дата вне горизонта доступного прогноза'
    if reason:return {'slug':slug,'station':station,'date':day,'reason':reason,'outcomes':[]}
    with db.get_db() as c:
        c.execute('DELETE FROM web_scalp_ensemble_views WHERE touched<? OR touched>?',(now-VIEW_LEASE,now+30))
        c.execute('''INSERT INTO web_scalp_ensemble_views VALUES(?,?,?,?) ON CONFLICT(slug)
           DO UPDATE SET station=excluded.station,day=excluded.day,touched=excluded.touched''',(slug,station,day,now))
        c.execute('DELETE FROM web_scalp_ensemble_views WHERE slug NOT IN (SELECT slug FROM web_scalp_ensemble_views ORDER BY touched DESC LIMIT 20)')
        location=c.execute('SELECT lat,lon,payload,station_expires FROM web_scalp_forecast_cache WHERE code=?',(station,)).fetchone()
        rows=c.execute('SELECT * FROM web_scalp_ensemble_cache WHERE station=?',(station,)).fetchall()
        control=c.execute('SELECT last_run,blocked_until FROM web_scalp_ensemble_control WHERE id=1').fetchone()
    coords=None
    try:
        zone=json.loads(location['payload'] or '{}')['open']['timezone'] if location and location['station_expires']>now else None
        if location and location['lat'] and location['lon'] and scalp_forecast.forecast_ensemble.valid_timezone(zone):coords=(location['lat'],location['lon'],zone)
    except (KeyError,ValueError,TypeError):pass
    cache={}
    for r in rows:
        entry=dict(r)
        try:
            if entry.get('payload'):entry['payload']=json.loads(entry['payload'])
        except (ValueError,TypeError):entry['payload']=None;entry['error']='Некорректные данные модели'
        cache[(entry['station'],entry['model'])]=entry
    started=False
    if coords:
        try:started=_day_bounds(day,coords[2])[0]<=now
        except (ValueError,TypeError,OverflowError):pass
    outcomes=[];seen=set();markets=event.get('markets',[])
    if not isinstance(markets,list):markets=[]
    for market in markets[:100]:
        if not isinstance(market,dict):continue
        label=market.get('groupItemTitle');pair=orderbook.event_tokens({'markets':[market]})
        if not isinstance(label,str) or len(label)>200 or not pair or pair[0]['no'] in seen:continue
        seen.add(pair[0]['no'])
        item={'token':pair[0]['no'],'station':station,'date':day,'extreme':kind,'label':label}
        result=_evaluate(item,coords,'Ожидание координат и часового пояса станции',now,0,cache)
        result['token_yes']=pair[0]['yes']
        if started:
            result['models']=[{'name':name,'reason':'Местные сутки уже начались; наблюдения дня не учтены'} for name in MODELS]
        outcomes.append(result)
    # A token appearing twice is ambiguous; never attach its estimate to a row.
    counts={}
    for m in markets[:100]:
        for pair in orderbook.event_tokens({'markets':[m]}):
            for token in (pair['no'],pair['yes']):counts[token]=counts.get(token,0)+1
    outcomes=[x for x in outcomes if counts.get(x['token'])==1 and counts.get(x['token_yes'])==1]
    return {'slug':slug,'station':station,'date':day,'timezone':coords[2] if coords else None,
            'outcomes':outcomes,'control':dict(control) if control else {}}


def _prune(connection,now):
    connection.execute('DELETE FROM web_scalp_ensemble_views WHERE touched<?',(now-VIEW_LEASE,))
    connection.execute('DELETE FROM web_scalp_ensemble_results WHERE updated<?',(now-RETAIN,))
    connection.execute('DELETE FROM web_scalp_ensemble_snapshots WHERE captured<?',(now-RETAIN,))
    connection.execute('DELETE FROM web_scalp_ensemble_cache WHERE COALESCE(fetched,next_attempt,0)<?',(now-RETAIN,))
    connection.execute('DELETE FROM web_scalp_ensemble_cache WHERE (station,model) NOT IN (SELECT station,model FROM web_scalp_ensemble_cache ORDER BY COALESCE(fetched,next_attempt,0) DESC LIMIT 200)')
    connection.execute('DELETE FROM web_scalp_ensemble_results WHERE token NOT IN (SELECT token FROM web_scalp_ensemble_results ORDER BY updated DESC LIMIT 500)')
    connection.execute('DELETE FROM web_scalp_ensemble_snapshots WHERE position_id NOT IN (SELECT position_id FROM web_scalp_ensemble_snapshots ORDER BY captured DESC LIMIT 1000)')


def run_once(now=None):
    """At most one station refresh and two ensemble calls per cycle."""
    global _cursor,_last_cleanup
    live=now is None;now=time.time() if live else now
    with db.get_db() as c:cfg=c.execute('SELECT enabled,settings FROM web_scalp_config WHERE id=1').fetchone()
    positions=_position_items()
    # Save any prior observation as-of entry outside the demo ledger transaction.
    # This audit write happens after the simulated fill and cannot affect it.
    if positions:
        try:
            with db.get_db() as c:
                for p in positions:
                    if not c.execute('SELECT 1 FROM web_scalp_ensemble_snapshots WHERE position_id=?',(p['position_id'],)).fetchone():
                        try:capture_entry(c,p['position_id'],p)
                        except Exception:pass
        except Exception:pass
    with _lock:recent=[x for x in _candidates if 0<=now-x.get('offered_at',0)<=INTERVAL*3 and isinstance(x.get('end'),(int,float)) and x['end']>now]
    # Keep monitoring open demo positions after stop, not new candidates.
    items=positions+(recent if cfg['enabled'] else [])
    dedup={}
    for item in items:
        if item.get('token') and item['token'] not in dedup:dedup[item['token']]=item
    items=list(dedup.values())[:MAX_ITEMS]
    with db.get_db() as c:
        views=[dict(r) for r in c.execute('SELECT station,day FROM web_scalp_ensemble_views WHERE touched BETWEEN ? AND ? ORDER BY touched DESC LIMIT 20',(now-VIEW_LEASE,now+30))]
    if not items and not views:
        if now-_last_cleanup>=3600 or now<_last_cleanup:
            with db.get_db() as c:_prune(c,now)
            _last_cleanup=now
        return
    tokens=[x['token'] for x in positions]
    books={}
    for i in range(0,len(tokens),50):
        try:books.update(orderbook.fetch_batch(tokens[i:i+50]))
        except Exception:pass
    for item in positions:item['book']=books.get(item['token'])
    try:rate=json.loads(cfg['settings']).get('fee_rate','0.05')
    except (TypeError,ValueError):rate='0.05'
    station_items=items+[{'station':v['station'],'date':v['day']} for v in views]
    stations=list(dict.fromkeys(x.get('station') for x in station_items if isinstance(x.get('station'),str) and re.fullmatch(r'[A-Z][A-Z0-9]{3}',x['station'])))
    coords_by_station={};reasons={};model_cache={}
    if stations:
        # One read transaction instead of hundreds of connections when many
        # candidates share stations. No network or computation inside it.
        marks=','.join('?' for _ in stations)
        with db.get_db() as c:
            locations=c.execute(f'SELECT code,lat,lon,payload,station_expires FROM web_scalp_forecast_cache WHERE code IN ({marks})',stations).fetchall()
            cached=c.execute(f'SELECT * FROM web_scalp_ensemble_cache WHERE station IN ({marks})',stations).fetchall()
        for row in locations:
            try:
                zone=json.loads(row['payload'] or '{}')['open']['timezone'] if row['station_expires']>now else None
                if row['lat'] and row['lon'] and scalp_forecast.forecast_ensemble.valid_timezone(zone):coords_by_station[row['code']]=(row['lat'],row['lon'],zone)
            except (KeyError,ValueError,TypeError):pass
        model_cache={(row['station'],row['model']):dict(row) for row in cached}
    chosen=None
    if stations:
        # Round-robin prevents one busy market starving other stations.
        for offset in range(len(stations)):
            i=(_cursor+offset)%len(stations);code=stations[i];coords=coords_by_station.get(code)
            if coords:
                future=False
                for item in station_items:
                    if item.get('station')!=code:continue
                    try:future=future or _day_bounds(item.get('date'),coords[2])[0]>now
                    except (ValueError,TypeError,OverflowError):pass
                if not future:continue
            if any(now>=model_cache.get((code,name),{}).get('next_attempt',0) for name in MODELS):
                chosen=code;_cursor=(i+1)%len(stations);break
    if chosen:
        with db.get_db() as c:blocked=c.execute('SELECT blocked_until FROM web_scalp_ensemble_control WHERE id=1').fetchone()[0]
        if now>=blocked:
            try:coords_by_station[chosen],reasons[chosen]=_refresh_station(chosen,now,[x.get('date') for x in station_items if x.get('station')==chosen])
            except Exception:coords_by_station[chosen]=None;reasons[chosen]='Не удалось обновить ансамбль'
            with db.get_db() as c:updated=c.execute('SELECT * FROM web_scalp_ensemble_cache WHERE station=?',(chosen,)).fetchall()
            for row in updated:model_cache[(chosen,row['model'])]=dict(row)
    observed=time.time() if live else now
    records=[];snapshots=[]
    for item in items:
        code=item.get('station');result=_evaluate(item,coords_by_station.get(code),reasons.get(code),observed,rate,model_cache)
        encoded=json.dumps(result,ensure_ascii=True,allow_nan=False,separators=(',',':'))
        records.append((item['token'],encoded,observed))
        if item.get('position_id') and any('no' in m for m in result['models']) and observed>=item['opened']:
            result['snapshot_kind']='after_entry';result['captured_at']=observed
            encoded=json.dumps(result,ensure_ascii=True,allow_nan=False,separators=(',',':'))
            snapshots.append((item['position_id'],encoded,observed))
    with db.get_db() as c:
        c.executemany('''INSERT INTO web_scalp_ensemble_results VALUES(?,?,?) ON CONFLICT(token)
         DO UPDATE SET payload=excluded.payload,updated=excluded.updated''',records)
        c.executemany('INSERT OR IGNORE INTO web_scalp_ensemble_snapshots VALUES(?,?,?)',snapshots)
        if observed-_last_cleanup>=3600 or observed<_last_cleanup:
            _prune(c,observed);_last_cleanup=observed


def status(now=None):
    now=time.time() if now is None else now
    with db.get_db() as c:
        rows=c.execute('SELECT payload,updated FROM web_scalp_ensemble_results ORDER BY updated DESC LIMIT 50').fetchall()
        snaps=c.execute('SELECT position_id,payload FROM web_scalp_ensemble_snapshots ORDER BY captured DESC LIMIT 100').fetchall()
        control=dict(c.execute('SELECT last_run,last_error,blocked_until FROM web_scalp_ensemble_control WHERE id=1').fetchone())
    results=[];snapshots={}
    for row in rows:
        try:
            data=json.loads(row['payload']);data['stale']=not 0<=now-row['updated']<=INTERVAL*3;results.append(data)
        except (ValueError,TypeError):continue
    for row in snaps:
        try:snapshots[str(row['position_id'])]=json.loads(row['payload'])
        except (ValueError,TypeError):continue
    return {'results':results,'snapshots':snapshots,'interval_seconds':INTERVAL,'control':control}


def worker(stop):
    while not stop.is_set():
        error=None
        try:run_once()
        except Exception:error='Не удалось обновить наблюдение'
        try:
            with db.get_db() as c:c.execute('UPDATE web_scalp_ensemble_control SET last_run=?,last_error=? WHERE id=1',(time.time(),error))
        except Exception:pass  # Never stop trading on observer/SQLite errors.
        stop.wait(INTERVAL)
