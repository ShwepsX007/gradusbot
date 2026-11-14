"""Bounded public forecasts for demo scalper. Never reads keys or trades."""
import json
import math
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import database as db
import utils
from web import forecast_ensemble

FORECAST_TTL=900
STATION_TTL=7*86400
ERROR_RETRY=300
MET_MAX_AGE=2*3600
MAX_BYTES=1024*1024
AGENT={'User-Agent':'gradusbot-web/1.0 (https://github.com/ShwepsX007/gradusbot)','Accept':'application/json'}


def init_db():
    with db.get_db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS web_scalp_forecast_cache (
            code TEXT PRIMARY KEY, lat TEXT, lon TEXT, station_expires REAL NOT NULL DEFAULT 0,
            payload TEXT, fetched_at REAL, expires REAL NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, reason TEXT)''')
        c.execute('DELETE FROM web_scalp_forecast_cache WHERE station_expires<? AND expires<? AND next_attempt<?',(time.time()-7*86400,)*3)
    forecast_ensemble.init_db()


def _json_limited(response):
    response.raise_for_status()
    chunks=[];size=0;started=time.monotonic()
    for chunk in response.iter_content(65536):
        size+=len(chunk)
        if size>MAX_BYTES or time.monotonic()-started>12:raise ValueError('oversized or slow response')
        chunks.append(chunk)
    return json.loads(b''.join(chunks))


def _station(code):
    if time.time()<utils._AWC_STATE['blocked_until'] or not utils.AWC_LIMITER.allow():
        raise ValueError('AWC ограничен')
    r=None
    try:
        r=utils.SESSION.get('https://aviationweather.gov/api/data/stationinfo',
            params={'ids':code,'format':'json'},timeout=(3,8),headers=AGENT,stream=True)
        if r.status_code in (403,429):utils._awc_penalty(r.status_code)
        data=_json_limited(r)
        if not isinstance(data,list):raise ValueError('invalid station')
        hits=[x for x in data if isinstance(x,dict) and str(x.get('icaoId') or x.get('id') or '').upper()==code]
        if len(hits)!=1:raise ValueError('station identity')
        coords=forecast_ensemble.coordinates(hits[0].get('lat'),hits[0].get('lon'))
        if not coords:raise ValueError('station coordinates')
        return coords
    finally:
        if r is not None:r.close()


def _open_meteo(coords):
    r=None
    try:
        r=requests.get('https://api.open-meteo.com/v1/forecast',params={
            'latitude':coords[0],'longitude':coords[1],'timezone':'auto','past_days':1,'forecast_days':3,
            'hourly':'temperature_2m','timeformat':'unixtime','temperature_unit':'celsius'},
            headers=AGENT,timeout=(3,8),stream=True)
        raw=_json_limited(r)
        if not isinstance(raw,dict) or not forecast_ensemble.valid_timezone(raw.get('timezone')):raise ValueError('timezone')
        hourly=raw.get('hourly')
        if not isinstance(hourly,dict) or not isinstance(hourly.get('time'),list) or not isinstance(hourly.get('temperature_2m'),list):raise ValueError('hourly')
        if len(hourly['time'])!=len(hourly['temperature_2m']) or not 1<=len(hourly['time'])<=120:raise ValueError('hourly length')
        data=forecast_ensemble.normalize_open_meteo(raw)
        if not isinstance(data,dict):raise ValueError('invalid forecast')
        return {'timezone':data['timezone'],'hourly':data['hourly']}
    finally:
        if r is not None:r.close()


def get(code, *, permit_fetch=True, now=None):
    """Fresh cached result, or at most one station's bounded provider fetch.

    Returns payload and an explicit fallback reason. Failed/stale data are never
    silently treated as a fresh forecast. Caller controls cross-station budget.
    """
    now=time.time() if now is None else now
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z][A-Z0-9]{3}',code):return None,'Станция разрешения не определена'
    with db.get_db() as c:
        row=c.execute('SELECT * FROM web_scalp_forecast_cache WHERE code=?',(code,)).fetchone()
    row=dict(row) if row else {}
    if row.get('payload') and 0<=now-(row.get('fetched_at') or 0)<=FORECAST_TTL and now<row.get('expires',0):
        try:
            data=json.loads(row['payload'])
            if isinstance(data,dict):return data,None
        except (ValueError,TypeError):pass
    if now<row.get('next_attempt',0):return None,row.get('reason') or 'Прогноз временно недоступен'
    if not permit_fetch:return None,'Лимит запросов прогноза за проход'
    coords=(row.get('lat'),row.get('lon')) if now<row.get('station_expires',0) else None
    if not coords or not all(coords):
        try:coords=_station(code)
        except Exception:
            _error(code,'Координаты станции недоступны',now)
            return None,'Координаты станции недоступны'
    # Either source can be used alone. MET Norway does not provide a zone;
    # a previously verified Open-Meteo timezone for this ICAO can be reused.
    try:opened=_open_meteo(coords)
    except Exception:opened=None
    try:met=forecast_ensemble.get_metno(*coords)
    except Exception:met={'status':'error','data':None}
    fetched=met.get('fetched_at') if isinstance(met,dict) else None
    fresh=isinstance(fetched,(int,float)) and not isinstance(fetched,bool) and math.isfinite(fetched) and -120<=now-fetched<=MET_MAX_AGE
    met_data=met.get('data') if isinstance(met,dict) and met.get('status')=='ok' and fresh else None
    if opened is None and met_data:
        try:
            previous=json.loads(row.get('payload') or '{}')
            zone=(previous.get('open') or {}).get('timezone')
        except (ValueError,TypeError,AttributeError):zone=None
        if tuple(coords)==(row.get('lat'),row.get('lon')) and forecast_ensemble.valid_timezone(zone):
            opened={'timezone':zone,'hourly':{'epoch':[],'temperature_2m':[]}}
    if opened is None:
        _error(code,'Нет прогноза с подтверждённым часовым поясом станции',now,coords)
        return None,'Нет прогноза с подтверждённым часовым поясом станции'
    payload={'open':opened,'met':met_data,
             'met_status':met.get('status') if isinstance(met,dict) else 'error',
             'met_fetched_at':met.get('fetched_at') if isinstance(met,dict) else None,
             'fetched_at':now,'code':code}
    try:encoded=json.dumps(payload,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError):
        _error(code,'Некорректный прогноз',now,coords);return None,'Некорректный прогноз'
    expires=min(now+FORECAST_TTL,fetched+MET_MAX_AGE) if met_data else now+FORECAST_TTL
    with db.get_db() as c:
        c.execute('''INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload,fetched_at,expires,next_attempt,reason)
            VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(code) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,
            station_expires=excluded.station_expires,payload=excluded.payload,fetched_at=excluded.fetched_at,
            expires=excluded.expires,next_attempt=excluded.next_attempt,reason=NULL''',
            (code,*coords,now+STATION_TTL,encoded,now,expires,expires))
    return payload,None


def _error(code,reason,now,coords=None):
    with db.get_db() as c:
        c.execute('''INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,next_attempt,reason)
            VALUES(?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET next_attempt=excluded.next_attempt,reason=excluded.reason''',
            (code,*(coords or (None,None)),now+STATION_TTL if coords else 0,now+ERROR_RETRY,reason))


def extreme(payload, day, kind):
    """Ensemble extreme over a complete station-local day; no inference on gaps."""
    try:
        if kind not in ('max','min'):return None
        local_day=date.fromisoformat(day)
        opened=payload['open'];tzname=opened['timezone'];zone=ZoneInfo(tzname)
        start=datetime.combine(local_day,datetime.min.time(),tzinfo=zone).timestamp()
        end=datetime.combine(local_day+timedelta(days=1),datetime.min.time(),tzinfo=zone).timestamp()
        length=end-start
        if length not in (23*3600,24*3600,25*3600):return None
        ensemble=forecast_ensemble.ensemble(opened,payload.get('met'),tzname)
        points=sorted((p for p in ensemble['points'] if start<=p['time']<end),key=lambda p:p['time'])
        # A provider can use UTC-aligned hours in a half-hour zone. Require a
        # complete uniform hourly grid within the civil day, not local :00.
        if len(points)!=int(length//3600):return None
        if any(b['time']-a['time']!=3600 for a,b in zip(points,points[1:])):return None
        daily=[]
        for p in points:
            if not isinstance(p.get('sources'),dict) or not p['sources'] or not isinstance(p.get('temp'),(int,float)) or not math.isfinite(p['temp']):return None
            daily.append(p)
        if not daily:return None
        point=(max if kind=='max' else min)(daily,key=lambda p:p['temp'])
        return {'value_c':point['temp'],'time':point['time'],'timezone':tzname,'day':day,'kind':kind,
                'sources':sorted(point['sources']), 'hours':len(daily),'station':payload['code'],
                'single_hours':sum(len(p['sources'])==1 for p in daily),
                'fetched_at':payload['fetched_at'],'met_status':payload.get('met_status'),
                'met_fetched_at':payload.get('met_fetched_at')}
    except (KeyError,ValueError,TypeError,OverflowError,OSError):return None
