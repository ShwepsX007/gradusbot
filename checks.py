import logging
import time
import asyncio
import telegram.error
from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes

from database import get_stations, get_markets, get_market, get_station, get_station_history, get_setting
from utils import fetch_metar, fetch_weather, fetch_market
from bot.formatters import format_weather_full, format_bound_markets_block # Добавлен импорт
from bot.keyboards import chk_kb, back

log = logging.getLogger("bot")


def local_round_metar(v):
    try:
        return int(float(v))
    except:
        return 0


def local_fmt_metar_temp(v):
    try:
        val = int(v)
        return f"{val:+d}" if val != 0 else "0"
    except:
        return str(v)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    
    try:
        await q.answer()
    except:
        pass

    d = q.data

    if d == "chk_menu":
        try:
            await q.edit_message_text("🔍 *Проверить*", parse_mode="Markdown", reply_markup=chk_kb())
        except telegram.error.BadRequest:
            pass
        return

    if d == "chk_wu":
        sts = [st for st in get_stations() if st.get("station_type") == "wunderground"]
        if not sts:
            try:
                await q.edit_message_text("Нет станций WU.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        kb = [[Btn(f"📡 {st['name']}", callback_data=f"chk_cw_{st['id']}")] for st in sts]
        kb.append(back("chk_menu"))
        try:
            await q.edit_message_text("📡 Выберите станцию:", reply_markup=KB(kb))
        except telegram.error.BadRequest:
            pass
        return

    if d.startswith("chk_cw_"):
        st_id = int(d[7:])
        st = get_station(st_id)
        if not st:
            try:
                await q.edit_message_text("❌ Станция не найдена.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        try:
            await q.edit_message_text("⏳ Запрашиваю WU...")
            await asyncio.sleep(0.1)
        except telegram.error.BadRequest:
            pass
            
        w = fetch_weather(st.get("api_url", ""))
        if not w:
            try:
                await q.edit_message_text("❌ Ошибка или нет данных с WU.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
            
        # ИСПРАВЛЕНИЕ: Получаем юниты и привязанные рынки
        u = get_setting("units", "C")
        bound_text = format_bound_markets_block(st_id)
        msg = f"📡 *{st['name']}*\n\n{format_weather_full(w, units=u)}\n{bound_text}"
        
        try:
            await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB([back("chk_menu")]))
        except telegram.error.BadRequest:
            pass
        return

    if d == "chk_cwx":
        sts = [st for st in get_stations() if st.get("station_type") == "checkwx"]
        if not sts:
            try:
                await q.edit_message_text("Нет CheckWX станций.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        kb = [[Btn(f"⚡ {st['name']}", callback_data=f"chk_cx_{st['id']}")] for st in sts]
        kb.append(back("chk_menu"))
        try:
            await q.edit_message_text("⚡ Выберите CheckWX станцию:", reply_markup=KB(kb))
        except telegram.error.BadRequest:
            pass
        return

    if d.startswith("chk_cx_"):
        st_id = int(d[7:])
        st = get_station(st_id)
        if not st:
            try:
                await q.edit_message_text("❌ Станция не найдена.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        try:
            await q.edit_message_text("⏳ Запрашиваю CheckWX...")
            await asyncio.sleep(0.1)
        except telegram.error.BadRequest:
            pass

        from utils import fetch_checkwx
        api_key = get_setting("checkwx_api_key", "")
        if not api_key:
            try:
                await q.edit_message_text(
                    "❌ Не установлен API ключ CheckWX.\n"
                    "Меню *⚙️ Настройки → 🔑 API Ключ CheckWX*.",
                    parse_mode="Markdown",
                    reply_markup=KB([back("chk_menu")])
                )
            except telegram.error.BadRequest:
                pass
            return

        w = fetch_checkwx(st.get("station_code", ""), api_key)
        if not w:
            try:
                await q.edit_message_text(
                    "❌ Нет данных с CheckWX. Возможно неверный ключ или ICAO.",
                    reply_markup=KB([back("chk_menu")])
                )
            except telegram.error.BadRequest:
                pass
            return

        u = get_setting("units", "C")
        bound_text = format_bound_markets_block(st_id)
        msg = f"⚡ *{st['name']}*\n\n{format_weather_full(w, units=u)}\n{bound_text}"
        try:
            await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB([back("chk_menu")]))
        except telegram.error.BadRequest:
            pass
        return

    if d == "chk_metar":
        sts = [st for st in get_stations() if st.get("station_type") == "metar"]
        if not sts:
            try:
                await q.edit_message_text("Нет METAR станций.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        kb = [[Btn(f"✈️ {st['name']}", callback_data=f"chk_cm_{st['id']}")] for st in sts]
        kb.append(back("chk_menu"))
        try:
            await q.edit_message_text("✈️ Выберите METAR:", reply_markup=KB(kb))
        except telegram.error.BadRequest:
            pass
        return

    if d.startswith("chk_cm_"):
        st_id = int(d[7:])
        st = get_station(st_id)
        if not st:
            try:
                await q.edit_message_text("❌ Станция не найдена.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        try:
            await q.edit_message_text("⏳ Запрашиваю METAR...")
            await asyncio.sleep(0.1)
        except telegram.error.BadRequest:
            pass
            
        w = fetch_metar(st.get("station_code", ""))
        if not w:
            try:
                await q.edit_message_text("❌ Ошибка или нет данных METAR (Таймаут).", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
            
        # ИСПРАВЛЕНИЕ: Получаем юниты и привязанные рынки
        u = get_setting("units", "C")
        bound_text = format_bound_markets_block(st_id)
        msg = f"✈️ *{st['name']}*\n\n{format_weather_full(w, units=u)}\n{bound_text}"
        
        try:
            await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB([back("chk_menu")]))
        except telegram.error.BadRequest:
            pass
        return

    if d == "chk_metar_pred":
        sts = [st for st in get_stations() if st.get("station_type") == "wunderground"]
        if not sts:
            try:
                await q.edit_message_text("Нет станций WU для прогноза.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        kb = [[Btn(f"🔮 {st['name']}", callback_data=f"chk_cp_{st['id']}")] for st in sts]
        kb.append(back("chk_menu"))
        try:
            await q.edit_message_text("🔮 Выберите станцию для прогноза METAR:", reply_markup=KB(kb))
        except telegram.error.BadRequest:
            pass
        return

    if d.startswith("chk_cp_"):
        st = get_station(int(d[7:]))
        if not st:
            try:
                await q.edit_message_text("❌ Станция не найдена.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        
        try:
            await q.edit_message_text("⏳ Расчет прогноза...")
            await asyncio.sleep(0.1)
        except telegram.error.BadRequest:
            pass
        
        pred_window_minutes = int(get_setting("metar_pred_window", "10"))
        pred_window_seconds = pred_window_minutes * 60
        
        st_history = get_station_history(st["id"])
        time_limit = time.time() - pred_window_seconds
        recent_temps = [h[1] for h in st_history if h[0] >= time_limit]
        
        if not recent_temps:
            try:
                await q.edit_message_text(
                    f"❌ Недостаточно данных в истории за последние {pred_window_minutes} минут.\nБот собирает поминутную историю в фоне, подождите несколько минут.",
                    parse_mode="Markdown", reply_markup=KB([back("chk_menu")])
                )
            except telegram.error.BadRequest:
                pass
            return
            
        avg_temp = sum(recent_temps) / len(recent_temps)
        current_pred_val = local_round_metar(avg_temp)
        
        msg = (
            f"🔮 *Расчет прогноза METAR*\n"
            f"📡 Станция: *{st['name']}*\n\n"
            f"⏱ *Окно расчета:* Последние {pred_window_minutes} минут\n"
            f"📊 *Выборок в памяти:* {len(recent_temps)} шт.\n"
            f"🌡 *Средняя температура:* `{avg_temp:.3f}°C`\n\n"
            f"➡️ *Ожидаемый METAR:* `{local_fmt_metar_temp(current_pred_val)}°C`\n\n"
            f"_(Расчет построен на основе накопленной истории WU)_"
        )
        try:
            await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB([back("chk_menu")]))
        except telegram.error.BadRequest:
            pass
        return

    if d == "chk_market":
        mks = get_markets()
        if not mks:
            try:
                await q.edit_message_text("Нет рынков.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        kb = [[Btn(f"📊 {m['name']}", callback_data=f"chk_ck_{m['id']}")] for m in mks]
        kb.append(back("chk_menu"))
        try:
            await q.edit_message_text("📊 Выберите рынок:", reply_markup=KB(kb))
        except telegram.error.BadRequest:
            pass
        return

    if d.startswith("chk_ck_"):
        mid = int(d[7:])
        mk = get_market(mid)
        if not mk:
            try:
                await q.edit_message_text("❌ Рынок не найден.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        try:
            await q.edit_message_text("⏳ Запрашиваю рынок...")
            await asyncio.sleep(0.1)
        except telegram.error.BadRequest:
            pass
        md = fetch_market(mk["slug"])
        if not md or not md.get("options"):
            try:
                await q.edit_message_text("❌ Нет данных с Polymarket.", reply_markup=KB([back("chk_menu")]))
            except telegram.error.BadRequest:
                pass
            return
        msg = f"📊 *{md['title']}*\n\n"
        for o in md["options"]:
            msg += f"🔹 {o['label']}: `{o['prob']}%`\n"
        try:
            await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB([back("chk_menu")]))
        except telegram.error.BadRequest:
            pass
        return