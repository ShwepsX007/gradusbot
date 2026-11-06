"""Bounded read-only gap search: explicit temperature ranges and CLOB books."""
import math
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests
import database as db
from web import orderbook

MAX_MARKETS = 100
PAGE_TOKENS = 100
BATCH_SIZE = 50
_NUM = r'(-?\d+(?:\.\d+)?)'
_UNIT = r'\s*°\s*([CF])'


def number(value, low, high):
    if value is None or isinstance(value,bool): return None
    try:x=float(value)
    except (TypeError,ValueError,OverflowError):return None
    return x if math.isfinite(x) and low<=x<=high else None


def temperature_range(label):
    """Only parse unambiguous bucket labels. Bounds are in Celsius; None=infinity."""
    if not isinstance(label,str):return None
    s=label.strip().upper().replace('−','-').replace('º','°').replace('–','-').replace('—','-')
    match=re.fullmatch(_NUM+r'(?:\s*°\s*([CF]))?\s*(?:-|TO)\s*'+_NUM+_UNIT,s)
    if match:
        a,u,b,unit=match.groups()
        if u and u!=unit:return None
        a,b=float(a),float(b)
        if a>b:return None
        low,high=a,b
    else:
        match=re.fullmatch(_NUM+_UNIT+r'(?:\s+(OR BELOW|OR LOWER|OR LESS|AND BELOW|OR ABOVE|OR HIGHER|OR MORE|AND ABOVE))?',s)
        if not match:return None
        a,unit,tail=match.groups();a=float(a)
        low,high=(None,a) if tail in ('OR BELOW','OR LOWER','OR LESS','AND BELOW') else (a,None) if tail else (a,a)
    def c(v):return None if v is None else (v-32)*5/9 if unit=='F' else v
    low,high=c(low),c(high)
    if any(v is not None and not -150<=v<=150 for v in (low,high)):return None
    return (low,high)


def range_gap(a,b):
    al,ah=a;bl,bh=b
    if ah is not None and bl is not None and ah<bl:return bl-ah
    if bh is not None and al is not None and bh<al:return al-bh
    return 0.0


def scan_markets(event):
    """Keep all active YES prices for the leader; require explicit tokens for entries."""
    raw=event.get('markets') if isinstance(event,dict) else None
    if not isinstance(raw,list):return [],False
    truncated=len(raw)>MAX_MARKETS
    out=[];uncertain=False
    for market in raw[:MAX_MARKETS]:
        if not isinstance(market,dict) or market.get('closed') is True or market.get('active') is False or market.get('acceptingOrders') is False:continue
        names=orderbook._list(market.get('outcomes')); prices=orderbook._list(market.get('outcomePrices'))
        if len(names)!=2 or len(prices)!=2 or not all(isinstance(n,str) for n in names):uncertain=True;continue
        names=[n.strip().lower() for n in names]
        if sorted(names)!=['no','yes']:uncertain=True;continue
        yes_price=number(prices[names.index('yes')],0,1)
        if yes_price is None:uncertain=True;continue
        label=market.get('groupItemTitle')
        label=label.strip()[:180] if isinstance(label,str) else ''
        pair=orderbook.event_tokens({'markets':[market]})
        out.append({'label':label,'yes_price':yes_price,'accepting':market.get('acceptingOrders') is True,
                    'tokens':pair[0] if pair else None})
    return out,truncated or uncertain


def observed_extreme(event,now=None):
    """Only previously saved observations: never launch bulk archive requests."""
    now=time.time() if now is None else now
    code=event.get('station_code');zone=event.get('timezone');day=event.get('date')
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{4}',code):return None
    if not isinstance(zone,str) or not isinstance(day,str):return None
    try:
        tz=ZoneInfo(zone);d=date.fromisoformat(day)
        start=datetime.combine(d,datetime.min.time(),tzinfo=tz).timestamp()
        end=datetime.combine(d+timedelta(days=1),datetime.min.time(),tzinfo=tz).timestamp()
    except (ValueError,TypeError,OverflowError,KeyError):return None
    if start>now or end<now-7*86400:return None
    with db.get_db() as c:
        points={r['obs_time']:r['temp'] for r in c.execute('SELECT obs_time,temp FROM web_metar_archive WHERE icao=? AND obs_time>=? AND obs_time<? AND obs_time<=?',(code,start,end,now))}
        for r in c.execute('''SELECT h.obs_time,h.temp FROM web_metar_history h JOIN stations s ON s.id=h.station_id
             WHERE s.station_type='metar' AND UPPER(TRIM(s.station_code))=? AND h.obs_time>=? AND h.obs_time<? AND h.obs_time<=?''',(code,start,end,now)):
            points.setdefault(r['obs_time'],r['temp'])
        st=c.execute('SELECT status,last_success FROM web_metar_archive_status WHERE icao=?',(code,)).fetchone()
    points={t:value for t,v in points.items() if (value:=number(v,-150,150)) is not None}
    if not points:return None
    last=max(points)
    if datetime.fromtimestamp(now,tz).date()==d and now-last>7200:return None
    maximum=event['slug'].startswith('highest-')
    if not maximum and not event['slug'].startswith('lowest-'):return None
    return {'temp':(max if maximum else min)(points.values()),'last_observation':last,'count':len(points),
            'archive_status':st['status'] if st else 'not_loaded','last_success':st['last_success'] if st else None,
            'extreme':'max' if maximum else 'min'}


def prepare(events,settings,now=None):
    now=time.time() if now is None else now
    results=[];skipped={'no_leader':0,'no_observation':0,'unparsed':0,'no_tokens':0,'truncated_events':0}
    memo={};query=settings['query'].casefold()
    query={'лондон':'london','париж':'paris','нью-йорк':'new york','ньюйорк':'new york','токио':'tokyo','сеул':'seoul','гонконг':'hong kong','чикаго':'chicago','майами':'miami','москва':'moscow','стамбул':'istanbul','сиэтл':'seattle'}.get(query,query)
    for event in events:
        if not isinstance(event,dict) or not event.get('date'):continue
        if query and not any(query in str(event.get(k) or '').casefold() for k in ('title','slug','station_code')):continue
        markets=event.get('opportunity_markets') or []
        if event.get('opportunity_truncated'):skipped['truncated_events']+=1
        if event.get('opportunity_truncated'):continue  # leader cannot be known reliably
        if not markets:continue
        price=max(x['yes_price'] for x in markets)
        leaders=[x for x in markets if x['yes_price']==price]
        if price<settings['leader_min']/100 or len(leaders)!=1:
            skipped['no_leader']+=1;continue
        leader=leaders[0];leader_range=temperature_range(leader['label'])
        if not leader['accepting']:skipped['no_leader']+=1;continue
        if not leader_range:skipped['no_leader']+=1;continue
        reference={'kind':'leader','label':leader['label'],'yes_price':price}
        target=leader_range
        if settings['reference']=='observed':
            k=(event.get('station_code'),event.get('timezone'),event.get('date'),event['slug'].startswith('highest-'))
            if k not in memo:memo[k]=observed_extreme(event,now)
            obs=memo[k]
            if not obs:skipped['no_observation']+=1;continue
            target=(obs['temp'],obs['temp']);reference={'kind':'observed',**obs}
        for market in markets:
            if (settings['reference']=='leader' and market is leader) or not market['accepting']:continue
            if not market['tokens']:skipped['no_tokens']+=1;continue
            rng=temperature_range(market['label'])
            if not rng:skipped['unparsed']+=1;continue
            gap=range_gap(target,rng)*(1.8 if settings['gap_units']=='F' else 1)
            if gap+1e-9<settings['gap']:continue
            sides=('yes','no') if settings['side']=='both' else (settings['side'],)
            for side in sides:
                results.append({'event':{k:event.get(k) for k in ('slug','title','date','station_code','station','timezone','quotes_at')},
                    'leader':{'label':leader['label'],'yes_price':price},'reference':reference,
                    'label':market['label'],'side':side,'token':market['tokens'][side],
                    'gap':gap,'gap_units':settings['gap_units']})
    results.sort(key=lambda r:(-r['gap'],r['event']['title'] or '',r['label'],r['side']))
    return results,skipped


def books_for(tokens):
    """At most two batches, no request amplification on rejected batches."""
    tokens=list(dict.fromkeys(tokens));batches=[tokens[i:i+BATCH_SIZE] for i in range(0,len(tokens),BATCH_SIZE)]
    def fetch(batch):
        result=dict.fromkeys(batch)
        r=None
        try:
            r=requests.post('https://clob.polymarket.com/books',json=[{'token_id':t} for t in batch],
                timeout=(3,5),headers={'User-Agent':'gradusbot-web/1.0'},allow_redirects=False,stream=True)
            r.raise_for_status();chunks=[];size=0;started=time.monotonic()
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>4*1024*1024 or time.monotonic()-started>12:raise ValueError('oversized or slow response')
                chunks.append(chunk)
            payload=json.loads(b''.join(chunks))
            if not isinstance(payload,list):return result
            for book in payload:
                if isinstance(book,dict):
                    token=str(book.get('asset_id',''))
                    if token in result:result[token]=orderbook._parse_book(book,token)
        except (requests.RequestException,ValueError,TypeError):pass
        finally:
            if r is not None:
                try:r.close()
                except Exception:pass
        return result
    output={}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for part in pool.map(fetch,batches):output.update(part)
    return output


def matched_rows(candidates,books,settings):
    out=[];unavailable=0
    cap=Decimal(str(settings['price']))/100
    for item in candidates:
        book=books.get(item['token'])
        if book is None:unavailable+=1;continue
        bid=(book.get('bids') or [None])[0];ask=(book.get('asks') or [None])[0]
        def fits(level):
            try:return bool(level and Decimal(0)<Decimal(level['price'])<Decimal(1) and Decimal(level['price'])<=cap)
            except (InvalidOperation,ValueError,TypeError,KeyError):return False
        match={'ask':fits(ask),'bid':fits(bid),'either':fits(ask) or fits(bid),'both':fits(ask) and fits(bid)}[settings['quote']]
        if not match:continue
        row={k:v for k,v in item.items() if k!='token'}
        row.update(bid=bid,ask=ask,book_at=book['fetched_at'],ask_entry=fits(ask))
        out.append(row)
    return out,unavailable
