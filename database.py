import json
import sqlite3
import time
import logging
from contextlib import contextmanager
from config import DB_FILE

log = logging.getLogger("bot")

def init_db():
    c = sqlite3.connect(DB_FILE, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            api_url TEXT,
            wu_url TEXT,
            station_code TEXT,
            station_type TEXT DEFAULT 'wunderground',
            last_temp REAL,
            enabled INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS station_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER,
            timestamp REAL,
            temp REAL
        );

        CREATE TABLE IF NOT EXISTS station_market_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER,
            market_slug TEXT,
            market_name TEXT
        );

        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            slug TEXT,
            enabled INTEGER DEFAULT 1,
            last_probs TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS market_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER,
            timestamp REAL,
            probs TEXT
        );

        CREATE TABLE IF NOT EXISTS active_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_demo INTEGER DEFAULT 0,
            slug TEXT,
            token_id TEXT,
            side TEXT,
            size REAL,
            sl INTEGER,
            tp INTEGER,
            entry_price REAL DEFAULT 0,
            question TEXT,
            outcome TEXT
        );

        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_demo INTEGER DEFAULT 0,
            slug TEXT,
            question TEXT,
            outcome TEXT,
            side TEXT,
            size REAL,
            entry_price REAL,
            close_price REAL,
            pnl REAL,
            timestamp REAL
        );
    """)

    for col in ["is_demo INTEGER DEFAULT 0", "question TEXT", "outcome TEXT"]:
        try: c.execute(f"ALTER TABLE active_positions ADD COLUMN {col}")
        except: pass

    defaults = [
        ("units", "C"),
        ("threshold", "0.5"),
        ("m_threshold", "2.0"),
        ("interval", "60"),           # Интервал WU
        ("metar_always_notify", "0"),
        ("m_interval", "30"),         # Интервал рынков
        ("notifications", "1"),
        ("checkwx_api_keys", ""),
        ("checkwx_default_minute", "0"),
        ("checkwx_default_window", "10"),
        ("checkwx_default_lead", "5"),
        ("market_notifications", "1"),
        ("demo_mode", "0"),
        ("order_timeout", "20"),
        ("order_retries", "3"),
        ("metar_interval", "60"),     # Интервал AWC
        ("metar_pred_window", "10"),
        ("checkwx_interval", "30"),   # НОВОЕ: Интервал CheckWX
        ("checkwx_api_key", ""),      # НОВОЕ: Ключ API CheckWX
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))

    c.commit()
    c.close()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()

# ============== SETTINGS ==============
def get_setting(k, d=""):
    with get_db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else d

def set_setting(k, v):
    with get_db() as c:
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))

# ============== STATIONS ==============
def get_stations():
    with get_db() as c: return [dict(r) for r in c.execute("SELECT * FROM stations ORDER BY id").fetchall()]

def get_station(sid):
    with get_db() as c:
        r = c.execute("SELECT * FROM stations WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

def add_station(name, api_url, wu_url, code, stype="wunderground"):
    with get_db() as c:
        return c.execute(
            "INSERT INTO stations (name,api_url,wu_url,station_code,station_type,enabled) VALUES (?,?,?,?,?,0)",
            (name, api_url, wu_url, code, stype)
        ).lastrowid

def update_station(sid, **kw):
    if not kw: return
    with get_db() as c:
        c.execute(f"UPDATE stations SET {','.join(f'{k}=?' for k in kw)} WHERE id=?", list(kw.values()) + [sid])

def delete_station(sid):
    with get_db() as c:
        c.execute("DELETE FROM station_history WHERE station_id=?", (sid,))
        c.execute("DELETE FROM station_market_bindings WHERE station_id=?", (sid,))
        c.execute("DELETE FROM stations WHERE id=?", (sid,))

def get_station_history(sid, hours=24):
    with get_db() as c:
        return [[r["timestamp"], r["temp"]] for r in c.execute(
            "SELECT timestamp,temp FROM station_history WHERE station_id=? AND timestamp>? ORDER BY timestamp",
            (sid, time.time() - hours * 3600)).fetchall()]

def add_station_history(sid, temp):
    with get_db() as c:
        c.execute("INSERT INTO station_history (station_id,timestamp,temp) VALUES (?,?,?)", (sid, time.time(), temp))
        c.execute("DELETE FROM station_history WHERE station_id=? AND timestamp<?", (sid, time.time() - 48 * 3600))

# ============== BINDINGS ==============
def get_all_bindings():
    with get_db() as c: return [dict(r) for r in c.execute("SELECT * FROM station_market_bindings").fetchall()]

def get_bindings(sid):
    with get_db() as c: return [dict(r) for r in c.execute("SELECT * FROM station_market_bindings WHERE station_id=?", (sid,)).fetchall()]

def get_binding(bid):
    with get_db() as c:
        r = c.execute("SELECT * FROM station_market_bindings WHERE id=?", (bid,)).fetchone()
        return dict(r) if r else None

def add_binding(sid, slug, name):
    with get_db() as c:
        return c.execute("INSERT INTO station_market_bindings (station_id,market_slug,market_name) VALUES (?,?,?)", (sid, slug, name)).lastrowid

def delete_binding(bid):
    with get_db() as c:
        c.execute("DELETE FROM station_market_bindings WHERE id=?", (bid,))
        c.execute("DELETE FROM settings WHERE key LIKE ?", (f"bind_{bid}_%",))

def update_binding_name(sid, slug, name):
    with get_db() as c:
        c.execute("UPDATE station_market_bindings SET market_name=? WHERE station_id=? AND market_slug=?", (name, sid, slug))

# ============== MARKETS ==============
def get_markets():
    with get_db() as c:
        res = []
        for r in c.execute("SELECT * FROM markets ORDER BY id").fetchall():
            m = dict(r)
            m["last_probs"] = json.loads(m.get("last_probs") or "{}")
            res.append(m)
        return res

def get_market(mid):
    with get_db() as c:
        r = c.execute("SELECT * FROM markets WHERE id=?", (mid,)).fetchone()
        if r:
            m = dict(r)
            m["last_probs"] = json.loads(m.get("last_probs") or "{}")
            return m
        return None

def add_market(name, slug):
    with get_db() as c:
        return c.execute("INSERT INTO markets (name,slug,enabled,last_probs) VALUES (?,?,1,'{}')", (name, slug)).lastrowid

def update_market(mid, **kw):
    if "last_probs" in kw and isinstance(kw["last_probs"], dict): kw["last_probs"] = json.dumps(kw["last_probs"])
    if not kw: return
    with get_db() as c:
        c.execute(f"UPDATE markets SET {','.join(f'{k}=?' for k in kw)} WHERE id=?", list(kw.values()) + [mid])
        if "name" in kw:
            row = c.execute("SELECT slug FROM markets WHERE id=?", (mid,)).fetchone()
            if row: c.execute("UPDATE station_market_bindings SET market_name=? WHERE market_slug=?", (kw["name"], row["slug"]))

def delete_market(mid, slug):
    with get_db() as c:
        c.execute("DELETE FROM market_history WHERE market_id=?", (mid,))
        c.execute("DELETE FROM markets WHERE id=?", (mid,))
        c.execute("DELETE FROM station_market_bindings WHERE market_slug=?", (slug,))

def get_market_history(mid, hours=24):
    with get_db() as c:
        return [[r["timestamp"], json.loads(r["probs"])] for r in c.execute(
            "SELECT timestamp,probs FROM market_history WHERE market_id=? AND timestamp>? ORDER BY timestamp",
            (mid, time.time() - hours * 3600)).fetchall()]

def add_market_history(mid, probs):
    with get_db() as c:
        c.execute("INSERT INTO market_history (market_id,timestamp,probs) VALUES (?,?,?)", (mid, time.time(), json.dumps(probs)))
        c.execute("DELETE FROM market_history WHERE market_id=? AND timestamp<?", (mid, time.time() - 48 * 3600))

def get_market_by_slug(slug):
    with get_db() as c:
        r = c.execute("SELECT * FROM markets WHERE slug=?", (slug,)).fetchone()
        if r:
            m = dict(r)
            m["last_probs"] = json.loads(m.get("last_probs") or "{}")
            return m
        return None

# ============== POSITIONS ==============
def add_position(is_demo, slug, token_id, side, size, sl, tp, entry_price, question, outcome):
    with get_db() as c:
        c.execute("INSERT INTO active_positions (is_demo, slug, token_id, side, size, sl, tp, entry_price, question, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (is_demo, slug, token_id, side, size, sl, tp, entry_price, question, outcome))

def get_positions():
    with get_db() as c: return [dict(r) for r in c.execute("SELECT * FROM active_positions").fetchall()]

def get_position_by_id(pid):
    with get_db() as c:
        r = c.execute("SELECT * FROM active_positions WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

def remove_position(pid):
    with get_db() as c: c.execute("DELETE FROM active_positions WHERE id=?", (pid,))

def update_position_limits(pid, sl, tp):
    with get_db() as c: c.execute("UPDATE active_positions SET sl=?, tp=? WHERE id=?", (sl, tp, pid))

# ============== TRADE HISTORY ==============
def add_trade_history(is_demo, slug, question, outcome, side, size, entry_price, close_price, pnl):
    with get_db() as c:
        c.execute("INSERT INTO trade_history (is_demo, slug, question, outcome, side, size, entry_price, close_price, pnl, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (is_demo, slug, question, outcome, side, size, entry_price, close_price, pnl, time.time()))

def get_trade_statistics(is_demo):
    with get_db() as c: return [dict(r) for r in c.execute("SELECT * FROM trade_history WHERE is_demo=? ORDER BY timestamp DESC", (is_demo,)).fetchall()]

def clear_trade_statistics(is_demo):
    with get_db() as c: c.execute("DELETE FROM trade_history WHERE is_demo=?", (is_demo,))

# ============== BINDING SETTINGS ==============
def set_binding_setting(binding_id, key, value):
    set_setting(f"bind_{binding_id}_{key}", str(value))

def get_binding_setting(binding_id, key, default=None):
    return get_setting(f"bind_{binding_id}_{key}", str(default) if default is not None else "")