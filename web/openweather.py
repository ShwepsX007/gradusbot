"""OpenWeather One Call 3.0: protected key, two-hour cache and bounded usage.

This module only reads forecasts. It never changes station/bot settings.
"""
import contextlib
import fcntl
import json
import math
import logging
import os
import re
import stat
import tempfile
from email.utils import parsedate_to_datetime
import time

import requests
import database as db
from web import trading_account, forecast_ensemble

URL='https://api.openweathermap.org/data/3.0/onecall'
INTERVAL=7200
DAILY_LIMIT=900  # This terminal only; other users of the account are not visible.
MAX_BYTES=512*1024
KEY=re.compile(r'[A-Za-z0-9_-]{16,256}')


class OpenWeatherError(Exception):
    pass


@contextlib.contextmanager
def _lock(create=False):
    directory=trading_account._private_directory(create=create)
    if directory is None:
        yield None
        return
    fd=os.open(os.path.join(directory,'openweather.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_nlink!=1:
            raise OpenWeatherError('Небезопасный файл блокировки OpenWeather.')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield directory
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def _read(directory):
    if directory is None:return None
    try:fd=os.open(os.path.join(directory,'openweather.json'),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_nlink!=1 or st.st_size>2048:
            raise OpenWeatherError('Небезопасный файл ключа OpenWeather.')
        with os.fdopen(fd,'r',encoding='utf-8') as f:
            fd=None;data=json.load(f)
        key=data.get('key') if isinstance(data,dict) else None
        if not isinstance(key,str) or not KEY.fullmatch(key):raise OpenWeatherError('Повреждён файл ключа OpenWeather.')
        return key
    finally:
        if fd is not None:os.close(fd)


def load_key():
    with _lock() as directory:return _read(directory)


def public_config():
    return {'configured':bool(load_key()),'daily_limit':DAILY_LIMIT,'interval_seconds':INTERVAL}


def save_key(key):
    if not isinstance(key,str) or not KEY.fullmatch(key.strip()):raise OpenWeatherError('Неверный формат API-ключа OpenWeather.')
    with _lock(create=True) as directory:
        path=os.path.join(directory,'openweather.json')
        if os.path.lexists(path):_read(directory)
        fd,tmp=tempfile.mkstemp(prefix='.openweather-',dir=directory)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump({'key':key.strip()},f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
            dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return public_config()


def delete_key():
    with _lock() as directory:
        if directory is None:return
        if _read(directory) is None:return
        os.unlink(os.path.join(directory,'openweather.json'))
        dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)


def init_db():
    with db.get_db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS web_openweather_cache (
          lat TEXT NOT NULL,lon TEXT NOT NULL,payload TEXT,fetched_at REAL,
          next_attempt REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'new',PRIMARY KEY(lat,lon))''')
        c.execute('CREATE TABLE IF NOT EXISTS web_openweather_calls(ts REAL NOT NULL)')
        c.execute('CREATE INDEX IF NOT EXISTS web_openweather_calls_ts ON web_openweather_calls(ts)')
        c.execute('CREATE TABLE IF NOT EXISTS web_openweather_control(id INTEGER PRIMARY KEY CHECK(id=1),blocked_until REAL NOT NULL DEFAULT 0)')
        c.execute('INSERT OR IGNORE INTO web_openweather_control(id,blocked_until) VALUES(1,0)')
        c.execute('DELETE FROM web_openweather_calls WHERE ts<?',(time.time()-86400,))
        c.execute('DELETE FROM web_openweather_cache WHERE next_attempt<?',(time.time()-7*86400,))


def _num(v,lo,hi):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:return None
    return v


def _parse(body,coords,now):
    if not isinstance(body,dict) or not isinstance(body.get('hourly'),list) or not 1<=len(body['hourly'])<=60:raise ValueError('invalid hourly')
    lat=_num(body.get('lat'),-90,90);lon=_num(body.get('lon'),-180,180)
    if lat is None or lon is None or abs(lat-float(coords[0]))>.02 or abs(lon-float(coords[1]))>.02:raise ValueError('wrong location')
    by_time={}
    for row in body['hourly']:
        if not isinstance(row,dict):continue
        t=_num(row.get('dt'),now-3600,now+49*3600);temp=_num(row.get('temp'),-150,150)
        if t is None or temp is None:continue
        point={'time':int(t),'temp':temp}
        for name,lo,hi in [('humidity',0,100),('clouds',0,100),('wind_speed',0,200),('wind_deg',0,360),('pop',0,1)]:
            n=_num(row.get(name),lo,hi)
            if n is not None:point[name]=n
        by_time[int(t)]=point
    if not by_time:raise ValueError('no temperatures')
    tz=body.get('timezone')
    return {'timezone':tz if forecast_ensemble.valid_timezone(tz) else None,'points':[by_time[t] for t in sorted(by_time)]}


def get_forecast(lat,lon):
    coords=forecast_ensemble.coordinates(lat,lon)
    if not coords:return {'status':'invalid_coordinates','data':None}
    try:key=load_key()
    except Exception:return {'status':'config_error','data':None}
    if not key:return {'status':'not_configured','data':None}
    now=time.time()
    with db.get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM web_openweather_calls WHERE ts<?',(now-86400,))
        c.execute('DELETE FROM web_openweather_cache WHERE next_attempt<?',(now-7*86400,))
        row=c.execute('SELECT * FROM web_openweather_cache WHERE lat=? AND lon=?',coords).fetchone()
        if row and now<row['next_attempt']:
            if row['status']=='ok' and row['payload']:
                return {'status':'ok','data':json.loads(row['payload']),'fetched_at':row['fetched_at'],'next_attempt':row['next_attempt']}
            return {'status':row['status'],'data':None,'next_attempt':row['next_attempt']}
        blocked=c.execute('SELECT blocked_until FROM web_openweather_control WHERE id=1').fetchone()[0]
        if now<blocked:return {'status':'throttled','data':None,'next_attempt':blocked}
        count,oldest=c.execute('SELECT COUNT(*),MIN(ts) FROM web_openweather_calls').fetchone()
        if count>=DAILY_LIMIT:
            return {'status':'local_limit','data':None,'next_attempt':oldest+86400}
        next_attempt=now+INTERVAL
        c.execute('INSERT INTO web_openweather_calls(ts) VALUES(?)',(now,))
        c.execute('''INSERT INTO web_openweather_cache(lat,lon,next_attempt,status) VALUES(?,?,?,'loading')
          ON CONFLICT(lat,lon) DO UPDATE SET next_attempt=excluded.next_attempt,status='loading' ''',(*coords,next_attempt))
    # No retries. Do not log exceptions or URLs: appid is a query parameter.
    response=None;data=None;status='error';retry_at=next_attempt;was_throttled=False
    # urllib3's DEBUG lines include the complete request path and appid.
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
    try:
        response=requests.get(URL,params={'lat':coords[0],'lon':coords[1],'units':'metric','exclude':'current,minutely,daily,alerts','appid':key},
                              timeout=(3,8),headers={'User-Agent':'gradusbot-web/1.0','Accept':'application/json'},stream=True,allow_redirects=False)
        if response.status_code==429:
            status='throttled';was_throttled=True
            value=response.headers.get('Retry-After','')
            try:
                delay=int(value);retry_at=max(retry_at,time.time()+max(delay,0))
            except (ValueError,TypeError,OverflowError):
                try:retry_at=max(retry_at,parsedate_to_datetime(value).timestamp())
                except (ValueError,TypeError,OverflowError,AttributeError):pass
        elif response.status_code in (401,403):status='unauthorized'
        elif response.status_code==200:
            chunks=[];length=0
            for chunk in response.iter_content(65536):
                length+=len(chunk)
                if length>MAX_BYTES:raise ValueError('large response')
                chunks.append(chunk)
            data=_parse(json.loads(b''.join(chunks)),coords,time.time());status='ok'
    except Exception:
        status='error'
    finally:
        if response is not None:
            try:response.close()
            except Exception:pass
    try:
        if load_key()!=key:status='config_changed';data=None
    except Exception:
        status='config_error';data=None
    fetched=time.time() if status=='ok' else None
    with db.get_db() as c:
        c.execute('''UPDATE web_openweather_cache SET payload=?,fetched_at=?,status=?,next_attempt=?
          WHERE lat=? AND lon=? AND status='loading' AND next_attempt=?''',
          (json.dumps(data,separators=(',',':')) if data else None,fetched,status,retry_at,*coords,next_attempt))
        if was_throttled:c.execute('UPDATE web_openweather_control SET blocked_until=MAX(blocked_until,?) WHERE id=1',(retry_at,))
    return {'status':status,'data':data,'fetched_at':fetched,'next_attempt':retry_at}
