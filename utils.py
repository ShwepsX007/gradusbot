import re, time, json, logging, requests
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import urlparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from config import WU_API_KEYS

log = logging.getLogger("bot")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
})

MONTH_NAMES = {1:"january",2:"february",3:"march",4:"april",5:"may",6:"june",7:"july",8:"august",9:"september",10:"october",11:"november",12:"december"}
ICAO_TO_CITY = {"EGLC":"london","EGLL":"london","LFPB":"paris","LFPG":"paris","RCSS":"taipei"}

def f_to_c(f):
    try: return round((float(f)-32)*5/9, 2)
    except: return 0.0

def c_to_f(c):
    try: return round(float(c)*9/5+32, 2)
    except: return 0.0

def format_temp(tc, units, signed=False):
    try: tc = float(tc)
    except: return "?"
    if signed:
        s = "+" if tc > 0 else ""
        if units == "F": return f"{s}{c_to_f(tc):.1f}°F"
        if units == "B": return f"{s}{tc:.1f}°C / {s}{c_to_f(tc):.1f}°F"
        return f"{s}{tc:.1f}°C"
    else:
        if units == "F": return f"{c_to_f(tc):.1f}°F"
        if units == "B": return f"{tc:.1f}°C / {c_to_f(tc):.1f}°F"
        return f"{tc:.1f}°C"

def _fmt_plain(v):
    try: v = float(v); return str(int(v)) if v.is_integer() else f"{v:.1f}"
    except: return str(v)

def _fmt_signed(v):
    try: v = float(v); return f"{int(v):+d}" if v.is_integer() else f"{v:+.1f}"
    except: return str(v)

def format_temp_delta(old_tc, new_tc, units="C"):
    try: diff_c = float(new_tc) - float(old_tc)
    except: return ""
    if units == "F": return f"{_fmt_signed(diff_c * 9 / 5)}°F"
    if units == "B": return f"{_fmt_signed(diff_c)}°C / {_fmt_signed(diff_c * 9 / 5)}°F"
    return f"{_fmt_signed(diff_c)}°C"

# ==== НОВАЯ ФУНКЦИЯ ДЛЯ CHECKWX ====
# Состояние ротации ключей CheckWX в памяти
_CHECKWX_KEY_STATE = {
    "exhausted": {},   # key -> timestamp (UTC sec) когда снова можно пробовать
}


def _get_checkwx_keys(api_key_legacy=""):
    """
    Возвращает список ключей в порядке использования.
    Берём из настройки 'checkwx_api_keys' (через запятую) + legacy одиночный 'checkwx_api_key'.
    """
    from database import get_setting
    raw = get_setting("checkwx_api_keys", "")
    keys = []
    if raw:
        for k in raw.split(","):
            k = k.strip()
            if k:
                keys.append(k)
    legacy = (api_key_legacy or get_setting("checkwx_api_key", "")).strip()
    if legacy and legacy not in keys:
        keys.append(legacy)
    return keys


def fetch_checkwx(icao, api_key=""):
    """
    icao — ICAO станции.
    api_key — игнорируется для совместимости; реально используется список из settings.
    """
    icao = icao.upper().strip()
    keys = _get_checkwx_keys(api_key)
    if not keys:
        log.warning("CheckWX: нет ни одного API ключа")
        return None

    now = time.time()
    exhausted = _CHECKWX_KEY_STATE["exhausted"]

    # Отфильтруем ключи которые ещё в "бане"
    active_keys = [k for k in keys if exhausted.get(k, 0) <= now]
    if not active_keys:
        # все ключи 429-нуты, ждём
        log.warning("CheckWX: все ключи исчерпаны лимитом, ждём сброса")
        return None

    url = f"https://api.checkwx.com/metar/{icao}/decoded"

    for key in active_keys:
        try:
            headers = {"X-API-Key": key}
            r = SESSION.get(url, headers=headers, timeout=10)

            if r.status_code == 429:
                # лимит на сегодня
                reset_at = now + 60 * 60  # по умолчанию час
                try:
                    body = r.json()
                    resets = body.get("resets") or ""
                    if resets:
                        from datetime import datetime, timezone
                        # формат '2026-06-20T00:00:00Z'
                        dt = datetime.strptime(resets.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
                        reset_at = max(now + 60, dt.timestamp())
                except:
                    pass
                exhausted[key] = reset_at
                log.warning(f"CheckWX key=...{key[-4:]} HTTP 429, в бан до {int(reset_at)}, пробую следующий ключ")
                continue

            if r.status_code == 401 or r.status_code == 403:
                log.warning(f"CheckWX key=...{key[-4:]} HTTP {r.status_code}, ключ невалидный")
                exhausted[key] = now + 24 * 3600
                continue

            if r.status_code != 200:
                log.warning(f"CheckWX {icao} key=...{key[-4:]}: HTTP {r.status_code} body={r.text[:200]}")
                continue

            data = r.json()
            if not data or "data" not in data or len(data["data"]) == 0:
                log.warning(f"CheckWX {icao}: пустой ответ data={str(data)[:200]}")
                return None

            m = data["data"][0]
            result = {"_source": "⚡ CheckWX"}
            if m.get("temperature") and m["temperature"].get("celsius") is not None:
                result["temp"] = float(m["temperature"]["celsius"])
            if m.get("dewpoint") and m["dewpoint"].get("celsius") is not None:
                result["dew_point"] = float(m["dewpoint"]["celsius"])
            if m.get("wind"):
                if m["wind"].get("speed_kts") is not None:
                    result["wind_speed"] = float(m["wind"]["speed_kts"])
                if m["wind"].get("degrees") is not None:
                    result["wind_dir"] = m["wind"]["degrees"]
                if m["wind"].get("gust_kts") is not None:
                    result["wind_gust"] = float(m["wind"]["gust_kts"])
            if m.get("visibility") and m["visibility"].get("miles_float") is not None:
                result["visibility"] = float(m["visibility"]["miles_float"])
            if m.get("barometer") and m["barometer"].get("hg") is not None:
                result["pressure"] = round(float(m["barometer"]["hg"]), 2)
            if m.get("clouds") and len(m["clouds"]) > 0:
                result["wx_phrase"] = m["clouds"][0].get("text")
            if m.get("raw_text"):
                result["raw_metar"] = m["raw_text"]
            if m.get("observed"):
                result["obs_time"] = m["observed"]

            if "temp" in result:
                return result

            log.warning(f"CheckWX {icao}: ответ без температуры")
            return None

        except Exception as e:
            log.warning(f"CheckWX {icao} key=...{key[-4:]} exception: {e}")
            continue

    return None

def fetch_metar(icao):
    icao = icao.upper().strip()
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json&taf=false"
        r = SESSION.get(url, timeout=20) 
        if r.status_code != 200: return None
        data = r.json()
        if not data or not isinstance(data, list): return None
        m = data[0]
        result = {"_source": "METAR (AWC)"}
        if m.get("temp") is not None: result["temp"] = float(m["temp"])
        if m.get("dewp") is not None: result["dew_point"] = float(m["dewp"])
        if m.get("wspd") is not None: result["wind_speed"] = float(m["wspd"])
        if m.get("wdir") is not None: result["wind_dir"] = m["wdir"]
        if m.get("wgst") is not None: result["wind_gust"] = float(m["wgst"])
        if m.get("visib") is not None: result["visibility"] = m["visib"]
        if m.get("altim") is not None: result["pressure"] = round(float(m["altim"]), 2)
        if m.get("cover") is not None: result["wx_phrase"] = m["cover"]
        if m.get("rawOb") is not None: result["raw_metar"] = m["rawOb"]
        if m.get("obsTime") is not None: result["obs_time"] = m["obsTime"]
        if "temp" not in result: return None
        return result
    except: return None

def fetch_weather(api_url):
    try:
        r = SESSION.get(api_url, timeout=10).json()
        result = {"_source": "WU"}
        if "observations" in r and isinstance(r["observations"], list):
            obs = r["observations"][0]; imp = obs.get("imperial", {})
            result.update({"temp": f_to_c(imp.get("temp")) if imp.get("temp") is not None else None, "humidity": obs.get("humidity"), "wind_speed": imp.get("windSpeed"), "wind_gust": imp.get("windGust"), "pressure": imp.get("pressure"), "dew_point": f_to_c(imp.get("dewpt")) if imp.get("dewpt") is not None else None, "wind_dir": obs.get("winddir")})
        elif "temperature" in r:
            result.update({"temp": f_to_c(r.get("temperature")), "humidity": r.get("relativeHumidity"), "wind_speed": r.get("windSpeed"), "wind_gust": r.get("windGust"), "pressure": r.get("pressureAltimeter"), "dew_point": f_to_c(r.get("temperatureDewPoint")) if r.get("temperatureDewPoint") is not None else None, "wind_dir": r.get("windDirection")})
        else: return None
        result = {k: v for k, v in result.items() if v is not None}
        return result if "temp" in result else None
    except: return None

def extract_api_url(wu_url):
    try:
        path = urlparse(wu_url).path.rstrip("/"); sid = None
        if "/weather/" in path: sid = path.split("/weather/")[-1].split("/")[0]
        elif "/dashboard/pws/" in path: sid = path.split("/dashboard/pws/")[-1].split("/")[0]
        elif "/history/daily/" in path: sid = path.split("/")[-1]
        if sid:
            for ak in WU_API_KEYS:
                for url in [f"https://api.weather.com/v3/wx/observations/current?icaoCode={sid}&language=en-US&units=e&format=json&apiKey={ak}", f"https://api.weather.com/v2/pws/observations/current?stationId={sid}&format=json&units=e&apiKey={ak}"]:
                    try:
                        r = SESSION.get(url, timeout=10)
                        if r.status_code == 200 and ("temperature" in r.json() or r.json().get("observations")): return url
                    except: pass
        return None
    except: return None

def extract_code_from_api(url):
    if not url: return ""
    m = re.search(r'icaoCode=([A-Z0-9]{3,6})', url)
    if m: return m.group(1)
    m = re.search(r'stationId=([A-Z0-9]+)', url)
    return m.group(1) if m else ""

def extract_code_from_wu(url):
    if not url: return ""
    try:
        parts = urlparse(url).path.rstrip("/").split("/")
        if parts and re.match(r'^[A-Z0-9]{2,10}$', parts[-1]): return parts[-1]
    except: pass
    return ""

def fetch_market(slug):
    def _as_list(val):
        if isinstance(val, list): return val
        if isinstance(val, str):
            try: return json.loads(val) if isinstance(json.loads(val), list) else []
            except:
                import re
                floats = re.findall(r"0\.\d+", val)
                if floats: return floats
                return []
        return []
    def _to_percent(val):
        try:
            x = float(val); return round(x*100) if x <= 1 else round(x)
        except: return None

    try:
        r = SESSION.get(f"https://gamma-api.polymarket.com/events/slug/{slug}", timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        if isinstance(data, list): data = data[0] if data else {}
        if not isinstance(data, dict): return None

        markets_list = data.get("markets")
        if not markets_list:
            if "outcomePrices" in data or "probability" in data or "question" in data: markets_list = [data]
            else: markets_list = []

        opts = []
        is_closed = data.get("closed", False)

        for m in markets_list:
            label = m.get("groupItemTitle") or m.get("title") or m.get("question") or "Без названия"
            prices = _as_list(m.get("outcomePrices"))
            prob = None
            if prices and len(prices) > 0: prob = _to_percent(prices[0])
            elif m.get("probability") is not None: prob = _to_percent(m.get("probability"))
            if prob is None: prob = 0 if is_closed else "?"
            opts.append({"label": label, "prob": prob})

        return {"title": data.get("title") or data.get("question") or slug, "options": opts, "closed": is_closed}
    except: return None

def build_trend(lp, nd):
    msg = ""
    for o in nd["options"]:
        lbl = str(o["label"])
        prob = o["prob"]
        old = lp.get(lbl)
        if prob == "?":
            msg += f"🔹 {lbl}: Нет цены\n"; continue
        if old is None:
            msg += f"🔹 {lbl}: 🆕 {_fmt_plain(prob)}%\n"; continue
        try: diff = float(prob) - float(old)
        except: diff = 0
        if diff > 0: msg += f"🔹 {lbl}: {_fmt_plain(old)}% → {_fmt_plain(prob)}% 📈 ({_fmt_signed(diff)}%)\n"
        elif diff < 0: msg += f"🔹 {lbl}: {_fmt_plain(old)}% → {_fmt_plain(prob)}% 📉 ({_fmt_signed(diff)}%)\n"
        else: msg += f"🔹 {lbl}: {_fmt_plain(old)}% → {_fmt_plain(prob)}% ➡️ (0%)\n"
    return msg

def threshold_exceeded(lp, nd, th):
    if not lp: return False
    return any(lp.get(o["label"]) is not None and str(o["prob"]) != "?" and abs(float(o["prob"])-float(lp[o["label"]])) >= th for o in nd["options"])

def _fetch_detail(slug):
    try:
        r = SESSION.get(f"https://gamma-api.polymarket.com/events/slug/{slug}", timeout=12)
        return r.json() if r.status_code == 200 else {}
    except: return {}

def _guess_city(icao):
    icao = icao.upper()
    if icao in ICAO_TO_CITY: return ICAO_TO_CITY[icao]
    try:
        r = SESSION.get(f"https://gamma-api.polymarket.com/events?limit=50&active=true&closed=false&q={icao}", timeout=15)
        if r.status_code == 200 and isinstance(r.json(), list):
            for ev in r.json():
                m = re.search(r'temperature-in-([a-z0-9-]+)-on-', ev.get("slug",""))
                if m: ICAO_TO_CITY[icao] = m.group(1); return m.group(1)
    except: pass
    return ""

def search_markets(sc, wu="", days=4):
    sc = (sc or "").upper().strip(); wu = (wu or "").strip()
    if not sc and not wu: return []
    if not sc and wu: sc = extract_code_from_wu(wu)
    found, seen = [], set()
    today = datetime.utcnow().date()
    city = ""
    if wu:
        try:
            parts = [p for p in urlparse(wu).path.rstrip("/").split("/") if p]
            if "/history/daily/" in urlparse(wu).path and len(parts) >= 4: city = parts[-2].lower().replace("_","-")
        except: pass
    if not city and sc: city = _guess_city(sc)
    if city:
        for off in range(days+1):
            d = today + timedelta(days=off); ds = d.strftime("%Y-%m-%d")
            for tt in ("highest","lowest"):
                sl = f"{tt}-temperature-in-{city}-on-{MONTH_NAMES[d.month]}-{d.day}-{d.year}"
                if sl in seen: continue
                try:
                    r = SESSION.get(f"https://gamma-api.polymarket.com/events/slug/{sl}", timeout=10)
                    if r.status_code != 200: continue
                    det = r.json(); rs = det.get("slug", sl)
                    if not det.get("title") or det.get("closed"): continue
                    if rs not in seen: seen.add(rs); found.append({"slug":rs,"title":det.get("title",""),"date":ds})
                except: pass
    found.sort(key=lambda x: (x["date"], x["title"])); return found

def extract_wu_from_market(slug):
    """Достаём ссылку на wunderground.com из описания/резолюшна рынка."""
    try:
        det = _fetch_detail(slug)
        if not det:
            return ""
        texts = [str(det.get("resolutionSource", "") or ""), str(det.get("description", "") or "")]
        for mk in det.get("markets", []):
            texts.append(str(mk.get("resolutionSource", "") or ""))
            texts.append(str(mk.get("description", "") or ""))
        full = " ".join(texts)
        patterns = [
            r'(https?://(?:www\.)?wunderground\.com/history/daily/[^\s\"\'<>,]+)',
            r'(https?://(?:www\.)?wunderground\.com/weather/[^\s\"\'<>,]+)',
            r'(https?://(?:www\.)?wunderground\.com/[^\s\"\'<>,]+)',
        ]
        for pat in patterns:
            m = re.search(pat, full, re.I)
            if m:
                return m.group(1).rstrip('.,)')
        return ""
    except:
        return ""


def find_station_for_market(slug):
    """
    Ищем для рынка станцию:
      1) WU-ссылка из описания рынка → берём ICAO/slug из неё
      2) site=XXXX в описании
      3) icao=XXXX
      4) wunderground.com/.../XXXX
    """
    det = _fetch_detail(slug) or {}
    icao = ""
    wu_url = ""

    # 1. WU ссылка
    wu_url = extract_wu_from_market(slug)
    if wu_url:
        code = extract_code_from_wu(wu_url)
        if code:
            icao = code.upper()

    # 2/3/4. fallback в свободный текст
    if not icao:
        texts = [
            str(det.get("resolutionSource", "") or ""),
            str(det.get("description", "") or ""),
            str(det.get("title", "") or ""),
            str(det.get("slug", "") or ""),
        ]
        for mk in det.get("markets", []):
            texts.append(str(mk.get("resolutionSource", "") or ""))
            texts.append(str(mk.get("description", "") or ""))
            texts.append(str(mk.get("question", "") or ""))
        full = " ".join(texts)

        for pat in (
            r'site=([A-Z]{4})',
            r'\bicao[=:\s]+([A-Z]{4})\b',
            r'wunderground\.com/[^\s"\'<>]*?/([A-Z]{4})\b',
            r'\b([KLEFMRZOPSUVWY][A-Z0-9]{3})\b',  # последний шанс: что-то похожее на ICAO
        ):
            m = re.search(pat, full, re.I)
            if m:
                icao = m.group(1).upper()
                break

    metar_ok = False
    metar_temp = None
    if icao:
        try:
            t = fetch_metar(icao)
            if t and "temp" in t:
                metar_ok = True
                metar_temp = t["temp"]
        except:
            pass

    return {
        "wu_url": wu_url,
        "icao": icao,
        "metar_ok": metar_ok,
        "metar_temp": metar_temp,
    }

def generate_plot(title, history, is_market=False):
    if not history or len(history) < 2: return None
    h24 = [h for h in history if h[0] >= time.time()-24*3600]
    if len(h24) < 2: return None
    plt.figure(figsize=(10,6)); t = [datetime.fromtimestamp(h[0]) for h in h24]
    if not is_market:
        plt.plot(t,[h[1] for h in h24],marker='.',color='#3498db',linewidth=2); plt.ylabel("Temperature (°C)")
    else:
        labels = set()
        for h in h24: labels.update(h[1].keys())
        for lbl in sorted(labels):
            probs = [h[1].get(lbl,0) for h in h24]
            if max(probs) > 1: plt.plot(t, probs, label=lbl, linewidth=2)
        plt.ylabel("Probability (%)"); plt.legend(fontsize='x-small'); plt.ylim(0,105)
    plt.title(title); plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.gcf().autofmt_xdate()
    buf = BytesIO(); plt.savefig(buf, format='png', bbox_inches='tight'); plt.close(); buf.seek(0)
    return buf