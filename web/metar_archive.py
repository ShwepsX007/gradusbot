"""Bounded, shared seven-day public METAR archive cache.

History requests use the same AWC limiter/backoff as the bot in this process.
A database lease prevents duplicate station fetches across web workers. This
cache is separate from locally recorded observations and never edits bot data.
"""
import json
import math
import re
import time
from datetime import datetime, timezone

import database as db
import utils

WINDOW = 7 * 24 * 3600
SUCCESS_TTL = 15 * 60
ERROR_TTL = 5 * 60
LEASE_TTL = 90
MAX_REPORTS = 2000
MAX_BYTES = 2 * 1024 * 1024
PAGE_LIMIT = 400
MAX_PAGES = 5
URL = 'https://aviationweather.gov/api/data/metar'


def init_db():
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_archive (
            icao TEXT NOT NULL, obs_time INTEGER NOT NULL, temp REAL, raw TEXT,
            dew_point REAL, wind_speed REAL, wind_dir TEXT, wind_gust REAL,
            PRIMARY KEY(icao,obs_time))''')
        conn.execute('CREATE INDEX IF NOT EXISTS web_metar_archive_expiry ON web_metar_archive(obs_time)')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_archive_status (
            icao TEXT PRIMARY KEY, last_attempt REAL NOT NULL, last_success REAL, status TEXT NOT NULL)''')
        columns={r['name'] for r in conn.execute('PRAGMA table_info(web_metar_archive_status)')}
        if 'range_hours' not in columns:
            conn.execute('ALTER TABLE web_metar_archive_status ADD COLUMN range_hours INTEGER NOT NULL DEFAULT 48')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_archive_station (
            icao TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL, obs_time INTEGER NOT NULL)''')
        conn.execute('DELETE FROM web_metar_archive WHERE obs_time<?',(time.time()-WINDOW,))
        conn.execute('DELETE FROM web_metar_archive_station WHERE obs_time<?',(time.time()-WINDOW,))


def prune():
    with db.get_db() as conn:
        conn.execute('DELETE FROM web_metar_archive WHERE obs_time<?',(time.time()-WINDOW,))
        conn.execute('DELETE FROM web_metar_archive_station WHERE obs_time<?',(time.time()-WINDOW,))
        # Remove metadata for codes no longer referenced by a METAR station.
        conn.execute("""DELETE FROM web_metar_archive_status WHERE NOT EXISTS
            (SELECT 1 FROM stations WHERE station_type='metar' AND UPPER(station_code)=web_metar_archive_status.icao)""")


def _number(value, low, high):
    if value is None or isinstance(value,bool): return None
    try: number=float(value)
    except (TypeError,ValueError,OverflowError): return None
    return number if math.isfinite(number) and low<=number<=high else None


def parse_reports(data, code, now):
    if not isinstance(data,list) or len(data)>MAX_REPORTS:
        raise ValueError('Invalid or oversized archive response')
    reports={}
    for item in data:
        if not isinstance(item,dict) or str(item.get('icaoId','')).upper()!=code: continue
        stamp=item.get('obsTime')
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp!=int(stamp): continue
        stamp=int(stamp)
        if stamp<now-WINDOW or stamp>now: continue
        temp=_number(item.get('temp'),-150,150)
        raw=item.get('rawOb')
        raw=raw[:1024] if isinstance(raw,str) else ''
        if temp is None and not raw: continue
        dew=_number(item.get('dewp'),-150,150)
        speed=_number(item.get('wspd'),0,500)
        gust=_number(item.get('wgst'),0,500)
        direction=item.get('wdir')
        if isinstance(direction,str) and direction.upper()=='VRB': direction='VRB'
        else:
            direction=_number(direction,0,360)
            direction=str(int(direction)) if direction is not None and direction.is_integer() else None
        # Keep first report at the same observation time; provider order may not
        # reliably distinguish a later correction from an earlier message.
        reports.setdefault(stamp,(code,stamp,temp,raw,dew,speed,direction,gust))
    return list(reports.values())


def _claim(code, now):
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM web_metar_archive_status WHERE icao=?',(code,)).fetchone()
        if row and row['range_hours']==168:
            age=now-row['last_attempt']
            ttl=LEASE_TTL if row['status']=='loading' else SUCCESS_TTL if row['status'] in ('ok','empty') else ERROR_TTL
            if 0<=age<ttl:return None
        conn.execute('''INSERT INTO web_metar_archive_status(icao,last_attempt,last_success,status,range_hours)
            VALUES(?,?,NULL,'loading',168) ON CONFLICT(icao) DO UPDATE SET last_attempt=excluded.last_attempt,status='loading',range_hours=168 ''',(code,now))
        return now


def _finish(code, attempt, status, rows=None, location=None):
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        claim=conn.execute('SELECT last_attempt,status FROM web_metar_archive_status WHERE icao=?',(code,)).fetchone()
        if not claim or claim['last_attempt']!=attempt or claim['status']!='loading':return
        if not conn.execute("SELECT 1 FROM stations WHERE station_type='metar' AND UPPER(station_code)=? LIMIT 1",(code,)).fetchone():
            conn.execute('DELETE FROM web_metar_archive_status WHERE icao=?',(code,));return
        if status in ('ok','empty','partial'):
            now=time.time()
            rows=[r for r in (rows or []) if r[1]>=now-WINDOW and r[1]<=now]
            conn.executemany('''INSERT INTO web_metar_archive(icao,obs_time,temp,raw,dew_point,wind_speed,wind_dir,wind_gust)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(icao,obs_time) DO UPDATE SET
                temp=excluded.temp,raw=excluded.raw,dew_point=excluded.dew_point,
                wind_speed=excluded.wind_speed,wind_dir=excluded.wind_dir,wind_gust=excluded.wind_gust''',rows)
            if location and location[3]>=now-WINDOW:
                conn.execute('''INSERT INTO web_metar_archive_station(icao,lat,lon,obs_time) VALUES(?,?,?,?)
                    ON CONFLICT(icao) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,obs_time=excluded.obs_time
                    WHERE excluded.obs_time>=web_metar_archive_station.obs_time''',location)
            if status!='partial':
                conn.execute('UPDATE web_metar_archive_status SET status=?,last_success=? WHERE icao=?',(status,now,code))
            else:
                conn.execute('UPDATE web_metar_archive_status SET status=? WHERE icao=?',(status,code))
        else:
            conn.execute('UPDATE web_metar_archive_status SET status=? WHERE icao=?',(status,code))
        conn.execute('DELETE FROM web_metar_archive WHERE obs_time<?',(time.time()-WINDOW,))


def refresh(code):
    """Fetch seven days; page backwards only when the provider's 400 limit is hit."""
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{4}',code): return
    now=time.time();attempt=_claim(code,now)
    if attempt is None:return
    rows={};location=None;anchor=None;status='error';started=time.monotonic()
    try:
        for page in range(MAX_PAGES):
            if time.monotonic()-started>20:status='partial';break
            if time.time()<utils._AWC_STATE['blocked_until'] or not utils.AWC_LIMITER.allow():
                status='partial' if rows else 'throttled';break
            params={'ids':code,'format':'json','hours':168}
            if anchor is not None:params['date']=datetime.fromtimestamp(anchor,timezone.utc).strftime('%Y%m%d%H%M')
            response=None
            try:
                response=utils.SESSION.get(URL,params=params,timeout=(3,8),stream=True)
                if response.status_code in (403,429):
                    utils._awc_penalty(response.status_code);status='partial' if rows else 'throttled';break
                if response.status_code==204:data=[]
                else:
                    response.raise_for_status();chunks=[];size=0
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic()-started>25:raise ValueError('archive response took too long')
                        size+=len(chunk)
                        if size>MAX_BYTES:raise ValueError('oversized archive response')
                        chunks.append(chunk)
                    data=json.loads(b''.join(chunks))
                page_rows=parse_reports(data,code,time.time())
                if data and not page_rows:
                    stamps=[r.get('obsTime') for r in data if isinstance(r,dict) and str(r.get('icaoId','')).upper()==code]
                    if rows and stamps and all(isinstance(t,(int,float)) and not isinstance(t,bool) and math.isfinite(t) and t<now-WINDOW for t in stamps):
                        status='ok';break
                    raise ValueError('no valid reports')
                for row in page_rows:rows.setdefault(row[1],row)
                for item in data:
                    if not isinstance(item,dict) or str(item.get('icaoId','')).upper()!=code:continue
                    stamp=item.get('obsTime');lat=_number(item.get('lat'),-90,90);lon=_number(item.get('lon'),-180,180)
                    if isinstance(stamp,(int,float)) and not isinstance(stamp,bool) and math.isfinite(stamp) and now-WINDOW<=stamp<=time.time() and lat is not None and lon is not None:
                        if location is None or stamp>location[3]:location=(code,lat,lon,int(stamp))
                if len(data)<PAGE_LIMIT or (page_rows and min(r[1] for r in page_rows)<=now-WINDOW):
                    status='ok' if rows else 'empty';break
                oldest=min(r[1] for r in page_rows)
                next_anchor=oldest//60*60
                if anchor is not None and next_anchor>=anchor:
                    status='partial';break
                anchor=next_anchor
            finally:
                if response is not None:response.close()
        else:status='partial'
        if status in ('ok','empty') and time.time()>=utils._AWC_STATE['blocked_until']:utils._awc_ok()
    except Exception:
        status='partial' if rows else 'error'
    _finish(code,attempt,status,list(rows.values()),location)


def get(code):
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{4}',code):
        return {'status':'unsupported','last_success':None,'reports':[]}
    cutoff=time.time()-WINDOW
    with db.get_db() as conn:
        state=conn.execute('SELECT status,last_success FROM web_metar_archive_status WHERE icao=?',(code,)).fetchone()
        location=conn.execute('SELECT lat,lon FROM web_metar_archive_station WHERE icao=? AND obs_time>=?',(code,cutoff)).fetchone()
        rows=conn.execute('''SELECT obs_time,temp,raw,dew_point,wind_speed,wind_dir,wind_gust
            FROM web_metar_archive WHERE icao=? AND obs_time>=? ORDER BY obs_time DESC LIMIT ?''',(code,cutoff,MAX_REPORTS)).fetchall()
    return {'status':state['status'] if state else 'not_loaded',
            'last_success':state['last_success'] if state else None,
            'location':dict(location) if location else None,
            'reports':[dict(row) for row in rows]}
