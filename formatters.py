import logging
from database import get_bindings, get_market_by_slug, get_setting
from utils import fetch_market, fetch_metar, fetch_weather, fetch_checkwx

log = logging.getLogger("bot")

def fetch_station_data(s):
    stype = s.get("station_type")
    if stype == "metar":
        return fetch_metar(s.get("station_code", ""))
    elif stype == "checkwx":
        api_key = get_setting("checkwx_api_key", "")
        return fetch_checkwx(s.get("station_code", ""), api_key)
    return fetch_weather(s.get("api_url", "")) if s.get("api_url") else None

def _fmt_diff(diff):
    try:
        d = float(diff)
        if d.is_integer(): return f"{int(d):+d}"
        return f"{round(d, 1):+}"
    except: return str(diff)

def format_bound_markets_block(station_id, limit=None):
    try:
        bindings = get_bindings(station_id)
        if not bindings: return ""
        shown = bindings[:limit] if limit is not None else bindings
        lines = ["", "📎 Привязанные рынки:"]
        for b in shown:
            slug = b.get("market_slug", "")
            title = b.get("market_name") or slug or "Без названия"
            md = fetch_market(slug)
            if not md or not md.get("options"):
                lines.append(f"📊 {title}: нет данных"); continue
            db_market = get_market_by_slug(slug) if slug else None
            last_probs = (db_market.get("last_probs") if db_market else {}) or {}
            lines.append(f"📊 {title}")
            for o in md["options"]:
                lbl = o.get("label", "?"); prob = o.get("prob"); old = last_probs.get(lbl)
                if prob is None or prob == "?":
                    lines.append(f"   🔹 {lbl}: ?"); continue
                if old is None:
                    lines.append(f"   🔹 {lbl}: 🆕 {prob}%"); continue
                try: diff = float(prob) - float(old)
                except: diff = 0
                if diff > 0: lines.append(f"   🔹 {lbl}: {old}% → {prob}% 📈 ({_fmt_diff(diff)}%)")
                elif diff < 0: lines.append(f"   🔹 {lbl}: {old}% → {prob}% 📉 ({_fmt_diff(diff)}%)")
                else: lines.append(f"   🔹 {lbl}: {old}% → {prob}% ➡️ (0%)")
        if limit is not None and len(bindings) - limit > 0: lines.append(f"…и ещё {len(bindings) - limit}")
        return "\n".join(lines)
    except: return ""

def format_weather_full(data, units="C"):
    from utils import format_temp
    lines = []
    src = data.get("_source", "")
    if src: lines.append(f"📡 Источник: {src}")
    if "temp" in data: lines.append(f"🌡 Температура: {format_temp(data['temp'], units)}")
    if "dew_point" in data: lines.append(f"💧 Точка росы: {format_temp(data['dew_point'], units)}")
    if "humidity" in data: lines.append(f"💦 Влажность: {data['humidity']}%")
    if "cloud_cover" in data: lines.append(f"☁️ Облачность: {data['cloud_cover']}%")
    if "wx_phrase" in data: lines.append(f"🌤 Погода: {data['wx_phrase']}")
    if "wind_speed" in data:
        w = f"💨 Ветер: {data['wind_speed']} {'mph' if src=='WU' else 'kt'}"
        if "wind_dir" in data: w += f" ({data['wind_dir']}°)"
        if "wind_gust" in data: w += f" (порывы {data['wind_gust']})"
        lines.append(w)
    if "pressure" in data: lines.append(f"🔵 Давление: {data['pressure']} inHg")
    if "visibility" in data: lines.append(f"👁 Видимость: {data['visibility']} mi")
    if "uv" in data: lines.append(f"☀️ УФ: {data['uv']}")
    if "precip_rate" in data: lines.append(f"🌧 Осадки: {data['precip_rate']} in/h")
    if "precip_total" in data: lines.append(f"🌧 Всего: {data['precip_total']} in")
    if "obs_time" in data: lines.append(f"🕐 Время: {data['obs_time']}")
    if "raw_metar" in data: lines.append(f"📋 METAR: `{data['raw_metar']}`")
    return "\n".join(lines)