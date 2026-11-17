"""Optional, read-only WU outcome marker for verified overview events only."""
import math
import re
import time
from web import opportunity_scan, scalp_ensemble, wu_forecast


def _forecast_extreme(snapshot, day, zone, kind, now, obs):
    """Require exact local-day coverage and validated intraday METAR extrema."""
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'ok' or kind not in ('min','max'):
        return None
    fetched = snapshot.get('fetched_at')
    if type(fetched) not in (int,float) or not 0 <= now-fetched < 1800:
        return None
    try:
        start, end = scalp_ensemble._day_bounds(day, zone)
        if now >= end:
            return None
        points = snapshot.get('points')
        if not isinstance(points, list):
            return None
        selected=[]
        for p in points:
            if not isinstance(p, dict):return None
            t,v=p.get('time'),p.get('temp')
            if type(t) not in (int,float) or type(v) not in (int,float) or not math.isfinite(t) or not math.isfinite(v) or not -100<=v<=70:return None
            if start<=t<end and (now<start or t>=now):selected.append((t,v))
        selected.sort()
        if not selected or any(b[0]-a[0]!=3600 for a,b in zip(selected,selected[1:])):
            return None
        if now<start:
            if len(selected)!=int((end-start)/3600) or selected[0][0]-start>=3600 or end-selected[-1][0]>3600:
                return None
            used_metar=False
        else:
            if selected[0][0]-now>3600 or end-selected[-1][0]>3600:
                return None
            if not isinstance(obs, dict) or obs.get('extreme')!=kind:
                return None
            temp,updated,last=obs.get('temp'),obs.get('fetched_at'),obs.get('last_observation')
            if (type(temp) not in (int,float) or not math.isfinite(temp) or
                type(updated) not in (int,float) or not 0<=now-updated<=scalp_ensemble.METAR_MAX_AGE or
                type(last) not in (int,float) or not 0<=now-last<=scalp_ensemble.METAR_GAP):
                return None
            selected.append((last,temp));used_metar=True
        extreme=(max if kind=='max' else min)(v for _,v in selected)
        return {'temp':extreme, 'kind':kind, 'with_metar':used_metar,
                'fetched_at':fetched, 'local_start':start, 'local_end':end}
    except (ValueError,TypeError,OverflowError,OSError):
        return None


def _matches(temp, label):
    bounds=opportunity_scan.temperature_range(label)
    unit=re.search(r'[°º]\s*([CF])',label or '',re.I)
    if bounds is None or not unit:return None
    margin=.5 if unit.group(1).upper()=='C' else 5/18
    lo,hi=bounds
    lo=None if lo is None else lo-margin
    hi=None if hi is None else hi+margin
    # The exchange's exact rounding rules vary; do not claim a tie belongs to either side.
    if (lo is not None and abs(temp-lo)<1e-8) or (hi is not None and abs(temp-hi)<1e-8):return None
    return (lo is None or temp>lo) and (hi is None or temp<hi)


def attach(overview, now=None):
    """Augment response from verified ensemble overview; never trigger network or trading."""
    now=time.time() if now is None else now
    if not isinstance(overview,dict):return overview
    station, day, zone = overview.get('station'), overview.get('date'), overview.get('timezone')
    rows=overview.get('outcomes')
    if not station or not day or not zone or not isinstance(rows,list):return overview
    snapshot=wu_forecast.peek(station,zone,now)
    if not snapshot:return overview
    for row in rows:
        if not isinstance(row,dict) or row.get('station')!=station or row.get('date')!=day or row.get('timezone')!=zone:continue
        result=_forecast_extreme(snapshot,day,zone,row.get('extreme'),now,row.get('observation'))
        if result is not None:
            row['wu']={**result,'match':_matches(result['temp'],row.get('label'))}
    return overview
