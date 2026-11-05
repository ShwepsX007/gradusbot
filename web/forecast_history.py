"""Bounded, forward-only snapshots of forecasts known to the terminal.

The timestamps are server capture times, not claimed model issuance times.
Never use model values valid before capture as a retrospective forecast.
"""
import bisect
import contextlib
import json
import math
import sqlite3
import time
import zlib

import database as db
from web import forecast_ensemble

RETENTION = 8 * 86400  # bounded snapshot retention; old comparisons may be unavailable
MAX_LEAD = 48 * 3600
MIN_SNAPSHOT_INTERVAL = 300
SOURCES = ('Open-Meteo', 'MET Norway')


@contextlib.contextmanager
def _history_db():
    # Snapshotting is optional; never add the bot database's 60-second busy
    # timeout to a weather or Gemini request if another writer holds the DB.
    conn=sqlite3.connect(db.DB_FILE,timeout=1)
    conn.row_factory=sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db.get_db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS web_forecast_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, station_id INTEGER NOT NULL,
            captured_at REAL NOT NULL, payload BLOB NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS web_forecast_snapshots_station_time ON web_forecast_snapshots(station_id,captured_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS web_forecast_snapshots_retention ON web_forecast_snapshots(captured_at)')
        c.execute('DELETE FROM web_forecast_snapshots WHERE captured_at<?', (time.time()-RETENTION,))


def _number(x, low, high):
    return x if isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and low<=x<=high else None


def capture(sid, open_data, met_data, now=None):
    now=time.time() if now is None else now
    # Filter each original provider before constructing the ensemble: otherwise
    # the ensemble could interpolate a future instant from a past model value.
    hourly=(open_data or {}).get('hourly',{}) if isinstance(open_data,dict) else {}
    epochs=hourly.get('epoch',[]) if isinstance(hourly,dict) else []
    temps=hourly.get('temperature_2m',[]) if isinstance(hourly,dict) else []
    future_open={'hourly':{'epoch':[],'temperature_2m':[]}}
    if isinstance(epochs,list) and isinstance(temps,list):
        for t,v in zip(epochs,temps):
            if _number(t,now,now+MAX_LEAD) is not None and _number(v,-150,150) is not None:
                future_open['hourly']['epoch'].append(t);future_open['hourly']['temperature_2m'].append(v)
    pairs=(met_data or {}).get('points',[]) if isinstance(met_data,dict) else []
    if not isinstance(pairs,list):pairs=[]
    future_met={'points':[p for p in pairs if isinstance(p,list) and len(p)==2 and _number(p[0],now,now+MAX_LEAD) is not None and _number(p[1],-150,150) is not None]}
    ensemble=forecast_ensemble.ensemble(future_open,future_met,None)
    points=[]
    for p in ensemble['points']:
        if not isinstance(p,dict):continue
        t=_number(p.get('time'),now,now+MAX_LEAD)
        src=p.get('sources')
        if t is None or not isinstance(src,dict):continue
        src={name:v for name in SOURCES if (v:=_number(src.get(name),-150,150)) is not None}
        if src:points.append({'time':int(t),'sources':src})
    if not points:return False
    points.sort(key=lambda p:p['time'])
    payload=zlib.compress(json.dumps(points,separators=(',',':'),allow_nan=False).encode(),6)
    with _history_db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM web_forecast_snapshots WHERE captured_at<?',(now-RETENTION,))
        last=c.execute('SELECT captured_at FROM web_forecast_snapshots WHERE station_id=? ORDER BY captured_at DESC LIMIT 1',(sid,)).fetchone()
        if last and now-last['captured_at']<MIN_SNAPSHOT_INTERVAL:return False
        inserted=c.execute('''INSERT INTO web_forecast_snapshots(station_id,captured_at,payload)
            SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM stations WHERE id=?)''',(sid,now,payload,sid))
        return bool(inserted.rowcount)


def _estimate(points, captured, obs_time):
    """Interpolate only between points that were both in the future at capture."""
    points=[p for p in points if p['time']>=captured]
    times=[p['time'] for p in points]
    index=bisect.bisect_left(times,obs_time)
    if index<len(points) and times[index]==obs_time:
        return points[index]['sources'],False
    if index==0 or index==len(points):return None
    a,b=points[index-1],points[index]
    if b['time']-a['time']>3600:return None
    ratio=(obs_time-a['time'])/(b['time']-a['time'])
    names=set(a['sources'])&set(b['sources'])
    values={k:a['sources'][k]+(b['sources'][k]-a['sources'][k])*ratio for k in SOURCES if k in names}
    return (values,True) if values else None


def compare(sid, metar, now=None):
    now=time.time() if now is None else now
    if not isinstance(metar,dict):return None
    obs=_number(metar.get('obs_time'),now-7*86400,now)
    actual=_number(metar.get('temp'),-150,150)
    if obs is None or actual is None:return None
    results={}
    with _history_db() as c:
        rows=c.execute('''SELECT captured_at,payload FROM web_forecast_snapshots
            WHERE station_id=? AND captured_at<? AND captured_at>=? ORDER BY captured_at DESC,id DESC''',
            (sid,obs,obs-MAX_LEAD)).fetchall()
    for row in rows:
        captured=row['captured_at']
        if 'latest' in results and ('three_hours' in results or captured>obs-3*3600):continue
        try:
            points=json.loads(zlib.decompress(row['payload']))
            estimate=_estimate(points,captured,obs)
        except (ValueError,TypeError,KeyError,zlib.error):continue
        if not estimate:continue
        sources,interpolated=estimate
        temp=sum(sources.values())/len(sources)
        item={'captured_at':captured,'forecast_temp':temp,'actual_temp':actual,'difference':actual-temp,
              'sources':sources,'interpolated':interpolated,'lead_seconds':obs-captured}
        if 'latest' not in results:results['latest']=item
        if captured<=obs-3*3600 and 'three_hours' not in results:results['three_hours']=item
        if len(results)==2:break
    return {'obs_time':obs,'actual_temp':actual,'latest':results.get('latest'),'three_hours':results.get('three_hours')}
