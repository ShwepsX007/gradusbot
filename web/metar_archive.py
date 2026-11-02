"""Bounded, shared 48-hour public METAR archive cache.

History requests use the same AWC limiter/backoff as the bot in this process.
A database lease prevents duplicate station fetches across web workers. This
cache is separate from locally recorded observations and never edits bot data.
"""
import json
import math
import re
import time

import database as db
import utils

WINDOW = 48 * 3600
SUCCESS_TTL = 15 * 60
ERROR_TTL = 5 * 60
LEASE_TTL = 90
MAX_REPORTS = 2000
MAX_BYTES = 2 * 1024 * 1024
URL = 'https://aviationweather.gov/api/data/metar'


def init_db():
    with db.get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_archive (
            icao TEXT NOT NULL, obs_time INTEGER NOT NULL, temp REAL, raw TEXT,
            dew_point REAL, wind_speed REAL, wind_dir TEXT, wind_gust REAL,
            PRIMARY KEY(icao,obs_time))''')
        conn.execute('CREATE INDEX IF NOT EXISTS web_metar_archive_expiry ON web_metar_archive(obs_time)')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_metar_archive_status (
            icao TEXT PRIMARY KEY, last_attempt REAL NOT NULL, last_success REAL, status TEXT NOT NULL)''')
        conn.execute('DELETE FROM web_metar_archive WHERE obs_time<?',(time.time()-WINDOW,))


def prune():
    with db.get_db() as conn:
        conn.execute('DELETE FROM web_metar_archive WHERE obs_time<?',(time.time()-WINDOW,))
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
        if row:
            age=now-row['last_attempt']
            ttl=LEASE_TTL if row['status']=='loading' else SUCCESS_TTL if row['status'] in ('ok','empty') else ERROR_TTL
            if 0<=age<ttl:return None
        conn.execute('''INSERT INTO web_metar_archive_status(icao,last_attempt,last_success,status)
            VALUES(?,?,NULL,'loading') ON CONFLICT(icao) DO UPDATE SET last_attempt=excluded.last_attempt,status='loading' ''',(code,now))
        return now


def _finish(code, attempt, status, rows=None):
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        claim=conn.execute('SELECT last_attempt,status FROM web_metar_archive_status WHERE icao=?',(code,)).fetchone()
        if not claim or claim['last_attempt']!=attempt or claim['status']!='loading':return
        if not conn.execute("SELECT 1 FROM stations WHERE station_type='metar' AND UPPER(station_code)=? LIMIT 1",(code,)).fetchone():
            conn.execute('DELETE FROM web_metar_archive_status WHERE icao=?',(code,));return
        if status in ('ok','empty'):
            now=time.time()
            rows=[r for r in (rows or []) if r[1]>=now-WINDOW and r[1]<=now]
            conn.executemany('''INSERT INTO web_metar_archive(icao,obs_time,temp,raw,dew_point,wind_speed,wind_dir,wind_gust)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(icao,obs_time) DO UPDATE SET
                temp=excluded.temp,raw=excluded.raw,dew_point=excluded.dew_point,
                wind_speed=excluded.wind_speed,wind_dir=excluded.wind_dir,wind_gust=excluded.wind_gust''',rows)
            conn.execute('UPDATE web_metar_archive_status SET status=?,last_success=? WHERE icao=?',(status,now,code))
        else:
            conn.execute('UPDATE web_metar_archive_status SET status=? WHERE icao=?',(status,code))
        conn.execute('DELETE FROM web_metar_archive WHERE obs_time<?',(time.time()-WINDOW,))


def refresh(code):
    """Fetch at most once per TTL; on error keep previously cached reports."""
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{4}',code): return
    now=time.time();attempt=_claim(code,now)
    if attempt is None:return
    if now<utils._AWC_STATE['blocked_until'] or not utils.AWC_LIMITER.allow():
        _finish(code,attempt,'throttled');return
    response=None
    started=time.monotonic()
    try:
        response=utils.SESSION.get(URL,params={'ids':code,'format':'json','hours':48},timeout=(3,12),stream=True)
        if response.status_code in (403,429):
            utils._awc_penalty(response.status_code);_finish(code,attempt,'throttled');return
        if response.status_code==204:
            data=[]
        else:
            response.raise_for_status()
            chunks=[];size=0
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic()-started>30:raise ValueError('archive response took too long')
                size+=len(chunk)
                if size>MAX_BYTES:raise ValueError('oversized archive response')
                chunks.append(chunk)
            data=json.loads(b''.join(chunks))
        rows=parse_reports(data,code,time.time())
        # A non-empty response with no valid station observations is not evidence
        # of an empty archive and must not be silently treated as success.
        if data and not rows: raise ValueError('no valid reports')
        # Another request may have applied a penalty while this one was in flight.
        if time.time()>=utils._AWC_STATE['blocked_until']: utils._awc_ok()
        _finish(code,attempt,'ok' if rows else 'empty',rows)
    except Exception:
        _finish(code,attempt,'error')
    finally:
        if response is not None: response.close()


def get(code):
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{4}',code):
        return {'status':'unsupported','last_success':None,'reports':[]}
    cutoff=time.time()-WINDOW
    with db.get_db() as conn:
        state=conn.execute('SELECT status,last_success FROM web_metar_archive_status WHERE icao=?',(code,)).fetchone()
        rows=conn.execute('''SELECT obs_time,temp,raw,dew_point,wind_speed,wind_dir,wind_gust
            FROM web_metar_archive WHERE icao=? AND obs_time>=? ORDER BY obs_time DESC LIMIT ?''',(code,cutoff,MAX_REPORTS)).fetchall()
    return {'status':state['status'] if state else 'not_loaded',
            'last_success':state['last_success'] if state else None,
            'reports':[dict(row) for row in rows]}
