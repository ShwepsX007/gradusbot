"""Bounded local best-bid/ask snapshots from actual public book reads."""
import math
import re
import time
import database as db

RETENTION = 7 * 86400
MAX_ROWS = 250000
BUCKET_SECONDS = 30
PERIOD_SECONDS = {'1h': 3600, '6h': 21600, '1d': 86400, '1w': RETENTION, 'max': RETENTION}


def init_db():
    with db.get_db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS web_book_history (
            token TEXT NOT NULL, bucket INTEGER NOT NULL, observed REAL NOT NULL,
            bid REAL, ask REAL, PRIMARY KEY(token,bucket))''')
        c.execute('CREATE INDEX IF NOT EXISTS web_book_history_time ON web_book_history(observed)')
        c.execute('''CREATE TABLE IF NOT EXISTS web_book_history_maintenance (
            id INTEGER PRIMARY KEY CHECK(id=1), cleaned REAL NOT NULL)''')


def _price(levels):
    if not isinstance(levels, list) or not levels:
        return None
    try:
        x=float(levels[0]['price'])
        return x if math.isfinite(x) and 0 <= x <= 1 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def record(data, now=None):
    now=time.time() if now is None else now
    if not isinstance(data,dict) or not isinstance(data.get('books'),dict): return
    pairs=data.get('pairs',[])
    tokens={v for pair in pairs if isinstance(pair,dict) for v in (pair.get('yes'),pair.get('no'))
            if isinstance(v,str) and re.fullmatch(r'[0-9]{1,80}',v)}
    rows=[]
    for token in list(data['books'])[:100]:
        if token not in tokens: continue
        book=data['books'].get(token)
        if not isinstance(book,dict) or not isinstance(book.get('bids'),list) or not isinstance(book.get('asks'),list): continue
        try: observed=float(book['fetched_at'])
        except (KeyError,TypeError,ValueError,OverflowError): continue
        if not math.isfinite(observed) or not now-120<=observed<=now+60: continue
        rows.append((token,int(observed//BUCKET_SECONDS),observed,_price(book['bids']),_price(book['asks'])))
    if not rows: return
    with db.get_db() as c:
        c.executemany('''INSERT INTO web_book_history(token,bucket,observed,bid,ask) VALUES(?,?,?,?,?)
          ON CONFLICT(token,bucket) DO UPDATE SET observed=excluded.observed,bid=excluded.bid,ask=excluded.ask
          WHERE excluded.observed > web_book_history.observed''',rows)
        last=c.execute('SELECT cleaned FROM web_book_history_maintenance WHERE id=1').fetchone()
        if not last or now-last['cleaned'] >= 900:
            c.execute('DELETE FROM web_book_history WHERE observed < ?', (now-RETENTION,))
            c.execute('''DELETE FROM web_book_history WHERE rowid IN
                (SELECT rowid FROM web_book_history ORDER BY observed DESC LIMIT -1 OFFSET ?)''',(MAX_ROWS,))
            c.execute('INSERT OR REPLACE INTO web_book_history_maintenance(id,cleaned) VALUES(1,?)',(now,))


def read(token,period,now=None):
    now=time.time() if now is None else now
    with db.get_db() as c:
        rows=c.execute('''SELECT observed AS t,bid,ask FROM web_book_history
                          WHERE token=? AND observed>=? AND observed<=? ORDER BY observed''',
                       (token,now-PERIOD_SECONDS[period],now+60)).fetchall()
        return [dict(row) for row in rows]
