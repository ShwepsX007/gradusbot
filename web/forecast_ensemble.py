"""MET Norway forecast cache and per-instant temperature ensemble for the web UI."""
import bisect
import json
import math
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import database as db

URL='https://api.met.no/weatherapi/locationforecast/2.0/compact'
USER_AGENT='gradusbot-web/1.0 (https://github.com/ShwepsX007/gradusbot)'
MAX_BYTES=1024*1024
_lock=threading.Lock()


def init_db():
    with db.get_db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS web_metno_cache (
            lat TEXT NOT NULL, lon TEXT NOT NULL, payload TEXT, expires REAL NOT NULL DEFAULT 0,
            last_modified TEXT, fetched_at REAL, next_attempt REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new', PRIMARY KEY(lat,lon))''')
        c.execute('CREATE TABLE IF NOT EXISTS web_metno_control(id INTEGER PRIMARY KEY CHECK(id=1),blocked_until REAL NOT NULL DEFAULT 0)')
        c.execute('INSERT OR IGNORE INTO web_metno_control(id,blocked_until) VALUES(1,0)')
        c.execute("DELETE FROM web_metno_cache WHERE next_attempt<? AND expires<?",(time.time()-2*86400,time.time()))


def _num(value,lo,hi):
    if isinstance(value,bool) or value is None:return None
    try:n=float(value)
    except (ValueError,TypeError,OverflowError):return None
    return n if math.isfinite(n) and lo<=n<=hi else None


def coordinates(lat,lon):
    lat,lon=_num(lat,-90,90),_num(lon,-180,180)
    if lat is None or lon is None:return None
    return (f'{lat:.4f}',f'{lon:.4f}')


def _date(value):
    try:
        result=parsedate_to_datetime(value)
        return result.timestamp() if result.tzinfo else None
    except (ValueError,TypeError,OverflowError):return None


def _expiry(headers,now):
    # Never re-request before the provider's Expires. A 15-min minimum avoids
    # excessive polling even when the header is missing or already in the past.
    return max(now+900, _date(headers.get('Expires')) or 0)


def _retry(headers,now):
    value=headers.get('Retry-After','')
    try:retry=now+max(0,int(value))
    except (ValueError,TypeError):retry=_date(value) or 0
    return max(now+300,retry)


def _parse(data,now):
    if not isinstance(data,dict) or not isinstance(data.get('properties'),dict):raise ValueError('invalid forecast')
    props=data['properties'];series=props.get('timeseries')
    if not isinstance(series,list) or not series or len(series)>300:raise ValueError('invalid series')
    points={}
    for entry in series:
        if not isinstance(entry,dict):continue
        try:
            stamp=entry.get('time')
            if not isinstance(stamp,str) or not stamp.endswith('Z'):continue
            instant=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
            temp=_num(entry['data']['instant']['details'].get('air_temperature'),-150,150)
        except (KeyError,TypeError,ValueError,OverflowError):continue
        if temp is not None and now-86400<=instant<=now+12*86400:points[int(instant)]=temp
    if not points:raise ValueError('no temperatures')
    updated=props.get('meta',{}).get('updated_at') if isinstance(props.get('meta'),dict) else None
    return {'points':[[k,points[k]] for k in sorted(points)],'updated_at':updated[:40] if isinstance(updated,str) else None}


def get_metno(lat,lon):
    key=coordinates(lat,lon)
    if not key:return {'status':'invalid_coordinates','data':None}
    # Short transaction and shared lease protect requests across threads/workers.
    # The process lock also prevents bursts for distinct stations in one worker.
    with _lock:
        now=time.time()
        with db.get_db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("DELETE FROM web_metno_cache WHERE next_attempt<? AND expires<?",(now-2*86400,now))
            row=c.execute('SELECT * FROM web_metno_cache WHERE lat=? AND lon=?',key).fetchone()
            if row and row['payload'] and now<row['expires']:
                return {'status':'ok','data':json.loads(row['payload']),'fetched_at':row['fetched_at']}
            if row and now<row['next_attempt']:
                return {'status':row['status'],'data':None}
            if now<c.execute('SELECT blocked_until FROM web_metno_control WHERE id=1').fetchone()[0]:
                return {'status':'throttled','data':None}
            old_payload=row['payload'] if row else None;modified=row['last_modified'] if row else None
            lease=now+90
            c.execute('''INSERT INTO web_metno_cache(lat,lon,next_attempt,status) VALUES(?,?,?,'loading')
                ON CONFLICT(lat,lon) DO UPDATE SET next_attempt=excluded.next_attempt,status='loading' ''',(*key,lease))
        headers={'User-Agent':USER_AGENT,'Accept':'application/json','Accept-Encoding':'gzip, deflate'}
        if modified:headers['If-Modified-Since']=modified
        response=None;data=None;status='error';expires=0;retry=time.time()+300;fetched=None;new_modified=modified
        try:
            response=requests.get(URL,params={'lat':key[0],'lon':key[1]},headers=headers,timeout=(3,8),stream=True)
            now=time.time()
            if response.status_code==304 and old_payload:
                data=json.loads(old_payload);status='ok';expires=_expiry(response.headers,now);fetched=now
            elif response.status_code==429:
                status='throttled';retry=_retry(response.headers,now)
            elif response.status_code==403:
                status='throttled'
            else:
                response.raise_for_status();chunks=[];size=0
                for chunk in response.iter_content(chunk_size=65536):
                    size+=len(chunk)
                    if size>MAX_BYTES:raise ValueError('response too large')
                    chunks.append(chunk)
                data=_parse(json.loads(b''.join(chunks)),now);status='ok';expires=_expiry(response.headers,now);fetched=now
                new_modified=response.headers.get('Last-Modified')
        except Exception:
            status='error'
        finally:
            if response is not None:response.close()
        with db.get_db() as c:
            if status=='ok':
                c.execute('''UPDATE web_metno_cache SET payload=?,expires=?,last_modified=?,fetched_at=?,next_attempt=?,status=? WHERE lat=? AND lon=? AND status='loading' AND next_attempt=? ''',
                          (json.dumps(data,separators=(',',':')),expires,new_modified,fetched,expires,status,*key,lease))
            else:
                c.execute("UPDATE web_metno_cache SET next_attempt=?,status=? WHERE lat=? AND lon=? AND status='loading' AND next_attempt=?",(retry,status,*key,lease))
                if status=='throttled':
                    c.execute('UPDATE web_metno_control SET blocked_until=MAX(blocked_until,?) WHERE id=1',(retry,))
        return {'status':status,'data':data if status=='ok' else None,'fetched_at':fetched}


def valid_timezone(name):
    if not isinstance(name,str) or not name:return False
    try:ZoneInfo(name);return True
    except (ZoneInfoNotFoundError,ValueError,TypeError):return False


def normalize_open_meteo(data):
    """Keep exact UTC hourly instants while providing local wall times to the UI."""
    if not isinstance(data,dict):return None
    try:zone=ZoneInfo(data.get('timezone',''))
    except (ZoneInfoNotFoundError,ValueError,TypeError):return data
    result=dict(data)
    for key in ('hourly','daily'):
        original=data.get(key)
        if not isinstance(original,dict):continue
        section=dict(original);result[key]=section
        for field in (('time',) if key=='hourly' else ('time','sunrise','sunset')):
            arr=original.get(field)
            if not isinstance(arr,list) or not any(isinstance(x,(int,float)) and not isinstance(x,bool) for x in arr):continue
            if key=='hourly' and field=='time':section['epoch']=arr[:]
            converted=[]
            for stamp in arr:
                try:
                    if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):converted.append(None);continue
                    if key=='daily' and field=='time' and _num(data.get('utc_offset_seconds'),-86400,86400) is not None:
                        # Open-Meteo daily epoch dates use its response-wide UTC offset,
                        # which can differ from the zone's offset on a past DST day.
                        local=datetime.fromtimestamp(stamp+_num(data.get('utc_offset_seconds'),-86400,86400),timezone.utc)
                    else:
                        local=datetime.fromtimestamp(stamp,zone)
                    converted.append(local.strftime('%Y-%m-%d' if key=='daily' and field=='time' else '%Y-%m-%dT%H:%M:%S'))
                except (ValueError,OverflowError,OSError):converted.append(None)
            section[field]=converted
    return result


def ensemble(open_data, met_data, tz, openweather_data=None):
    """Average forecasts at the same instant; never interpolate over gaps > 1 hour."""
    open_values={};met_values={};ow_values={}
    h=(open_data or {}).get('hourly',{}) if isinstance(open_data,dict) else {}
    epochs=h.get('epoch',[]) if isinstance(h,dict) else []
    temps=h.get('temperature_2m',[]) if isinstance(h,dict) else []
    if isinstance(epochs,list) and isinstance(temps,list):
        for stamp,temp in zip(epochs,temps):
            t=_num(stamp,0,4102444800);v=_num(temp,-150,150)
            if t is not None and v is not None:open_values[int(t)]=v
    for pair in (met_data or {}).get('points',[]):
        if not isinstance(pair,list) or len(pair)!=2:continue
        t=_num(pair[0],0,4102444800);v=_num(pair[1],-150,150)
        if t is not None and v is not None:met_values[int(t)]=v
    for point in (openweather_data or {}).get('points',[]):
        if not isinstance(point,dict):continue
        t=_num(point.get('time'),0,4102444800);v=_num(point.get('temp'),-150,150)
        if t is not None and v is not None:ow_values[int(t)]=v
    ow_times=sorted(ow_values)
    met_times=sorted(met_values)
    if open_values:
        lo,hi=min(open_values),max(open_values)
        instants=sorted(set(open_values) | {t for t in met_times+ow_times if t<lo or t>hi})
    else:instants=sorted(set(met_times)|set(ow_times))
    points=[]
    for t in instants:
        src={};interpolated=False
        if t in open_values:src['Open-Meteo']=open_values[t]
        if t in met_values:src['MET Norway']=met_values[t]
        elif t in open_values:
            idx=bisect.bisect_left(met_times,t)
            if 0<idx<len(met_times):
                a,b=met_times[idx-1],met_times[idx]
                if b-a<=3600:
                    src['MET Norway']=met_values[a]+(met_values[b]-met_values[a])*(t-a)/(b-a)
                    interpolated=True
        if t in ow_values:src['OpenWeather']=ow_values[t]
        else:
            idx=bisect.bisect_left(ow_times,t)
            if 0<idx<len(ow_times):
                a,b=ow_times[idx-1],ow_times[idx]
                if b-a<=3600:
                    src['OpenWeather']=ow_values[a]+(ow_values[b]-ow_values[a])*(t-a)/(b-a)
                    interpolated=True
        if src:points.append({'time':t,'temp':sum(src.values())/len(src),'sources':src,'interpolated':interpolated})
    return {'timezone':tz,'points':points}
