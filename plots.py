from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes

from database import get_markets, get_market, get_stations, get_station, get_station_history, get_market_history
from utils import generate_plot

from bot.state import icon


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data

    if d == "plots_menu":
        return await q.edit_message_text(
            "📈 *Категории:*",
            parse_mode="Markdown",
            reply_markup=KB([
                [Btn("📊 Рынки", callback_data="plots_sub_markets")],
                [Btn("✈️ METAR", callback_data="plots_sub_metar")],
                [Btn("📡 WU", callback_data="plots_sub_wu")],
                [Btn("❌ Закрыть", callback_data="close_inline")]
            ])
        )

    if d == "plots_sub_markets":
        mks = [m for m in get_markets() if m.get("enabled")]
        if not mks:
            return await q.edit_message_text("Нет активных рынков.", reply_markup=KB([[Btn("⬅️ Назад", callback_data="plots_menu")]]))
        kb = [[Btn(f"📊 {m['name']}", callback_data=f"chrm_{m['id']}")] for m in mks] + [[Btn("⬅️ Назад", callback_data="plots_menu")]]
        return await q.edit_message_text("📊 *Выберите рынок:*", parse_mode="Markdown", reply_markup=KB(kb))

    if d == "plots_sub_metar":
        sts = [st for st in get_stations() if st.get("enabled") and st.get("station_type") == "metar"]
        if not sts:
            return await q.edit_message_text("Нет активных METAR станций.", reply_markup=KB([[Btn("⬅️ Назад", callback_data="plots_menu")]]))
        kb = [[Btn(f"✈️ {st['name']}", callback_data=f"chrs_{st['id']}")] for st in sts] + [[Btn("⬅️ Назад", callback_data="plots_menu")]]
        return await q.edit_message_text("✈️ *Выберите METAR:*", parse_mode="Markdown", reply_markup=KB(kb))

    if d == "plots_sub_wu":
        sts = [st for st in get_stations() if st.get("enabled") and st.get("station_type") == "wunderground"]
        if not sts:
            return await q.edit_message_text("Нет активных WU станций.", reply_markup=KB([[Btn("⬅️ Назад", callback_data="plots_menu")]]))
        kb = [[Btn(f"📡 {st['name']}", callback_data=f"chrs_{st['id']}")] for st in sts] + [[Btn("⬅️ Назад", callback_data="plots_menu")]]
        return await q.edit_message_text("📡 *Выберите WU:*", parse_mode="Markdown", reply_markup=KB(kb))

    if d.startswith("chrs_"):
        st = get_station(int(d[5:]))
        if not st:
            return
        p = generate_plot(f"{icon(st)} {st['name']}", get_station_history(st["id"]))
        if p:
            return await q.message.reply_photo(p)
        return await q.edit_message_text("Мало данных.", reply_markup=KB([[Btn("⬅️ Назад", callback_data="plots_menu")]]))

    if d.startswith("chrm_"):
        m = get_market(int(d[5:]))
        if not m:
            return
        p = generate_plot(f"📊 {m['name']}", get_market_history(m["id"]), True)
        if p:
            return await q.message.reply_photo(p)
        return await q.edit_message_text("Мало данных.", reply_markup=KB([[Btn("⬅️ Назад", callback_data="plots_menu")]]))