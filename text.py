import logging
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes

from database import (
    add_station, add_market, update_station, update_market, get_setting, set_setting,
    set_binding_setting,
)
from utils import (
    extract_code_from_wu, extract_api_url, extract_code_from_api,
)

from bot.state import us
from bot.keyboards import (
    REPLY_KB, st_kb, mk_kb, chk_kb, trade_kb, settings_kb, notif_kb, back
)
from bot.handlers.common import send_internal_error

log = logging.getLogger("bot")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        cid = update.effective_chat.id
        s = us(cid)
        state = s.get("state")

        # === Реплай-меню (Сбрасывает любые зависшие состояния) ===
        if text == "💰 Торговля":
            s["state"] = None
            import polymarket_trading as pt
            demo = get_setting("demo_mode", "0") == "1"
            if demo:
                return await update.message.reply_text("🎮 *Торговля (ДЕМО-РЕЖИМ)*", parse_mode="Markdown", reply_markup=trade_kb())
            if not pt.is_ready():
                return await update.message.reply_text(
                    f"💰 *Торговля Polymarket*\n⚠️ Клиент не инициализирован!\n📍 Funder: `{pt.get_wallet_address() or 'НЕТ'}`",
                    parse_mode="Markdown", reply_markup=trade_kb())
            return await update.message.reply_text(
                f"💰 *Торговля Polymarket*\n📍 Кошелёк: `{pt.get_wallet_address()}`\n💵 Баланс: *{pt.get_balance()}$*",
                parse_mode="Markdown", reply_markup=trade_kb())

        if text == "🌡 Станции":
            s["state"] = None
            return await update.message.reply_text("🌡 *Станции*", parse_mode="Markdown", reply_markup=st_kb())

        if text == "📊 Рынки":
            s["state"] = None
            return await update.message.reply_text("📊 *Рынки*", parse_mode="Markdown", reply_markup=mk_kb())

        if text == "🔍 Проверить":
            s["state"] = None
            return await update.message.reply_text("🔍 *Проверить*", parse_mode="Markdown", reply_markup=chk_kb())

        if text == "📈 Графики":
            s["state"] = None
            return await update.message.reply_text(
                "📈 *Выберите категорию:*", parse_mode="Markdown",
                reply_markup=KB([
                    [Btn("📊 Рынки", callback_data="plots_sub_markets")],
                    [Btn("✈️ METAR", callback_data="plots_sub_metar")],
                    [Btn("📡 WU", callback_data="plots_sub_wu")],
                    [Btn("❌ Закрыть", callback_data="close_inline")]
                ]))

        if text == "⚙️ Настройки":
            s["state"] = None
            return await update.message.reply_text("⚙️ *Настройки*", parse_mode="Markdown", reply_markup=settings_kb())

        if text == "🔔 Уведомления":
            s["state"] = None
            return await update.message.reply_text("🔔 *Уведомления*", parse_mode="Markdown", reply_markup=notif_kb())
        
        # === Настройки исполнения ордеров ===
        if state == "wait_order_timeout":
            try:
                val = int(text)
                if not (5 <= val <= 300):
                    raise ValueError
            except ValueError:
                return await update.message.reply_text(
                    "❌ Введите целое число от 5 до 300.",
                    reply_markup=KB([back("tr_api_menu")])
                )
            set_setting("order_timeout", str(val))
            s["state"] = None
            return await update.message.reply_text(
                f"✅ Таймаут ордера: *{val}с*",
                parse_mode="Markdown",
                reply_markup=KB([back("tr_api_menu")])
            )

        if state == "wait_order_retries":
            try:
                val = int(text)
                if not (1 <= val <= 10):
                    raise ValueError
            except ValueError:
                return await update.message.reply_text(
                    "❌ Введите целое число от 1 до 10.",
                    reply_markup=KB([back("tr_api_menu")])
                )
            set_setting("order_retries", str(val))
            s["state"] = None
            return await update.message.reply_text(
                f"✅ Попыток входа: *{val}*",
                parse_mode="Markdown",
                reply_markup=KB([back("tr_api_menu")])
            )
        
        # === Ввод параметров стратегии для связки ===
        if state and state.startswith("wait_bind_"):
            param = state[len("wait_bind_"):]
            bid = s.get("edit_bind_id")
            if not bid:
                s["state"] = None
                return await update.message.reply_text("❌ Сессия слетела.", reply_markup=KB([back("tr_strategies")]))

            try:
                if param in ("target", "entry_temp", "stop_temp"):
                    val = float(text.replace(",", "."))
                elif param == "size":
                    val = float(text.replace(",", "."))
                    if val <= 0:
                        raise ValueError
                elif param in ("thresh", "tp"):
                    val = int(text)
                    if not (1 <= val <= 99):
                        raise ValueError
                else:
                    raise ValueError
            except ValueError:
                return await update.message.reply_text(
                    "❌ Неверное значение.",
                    reply_markup=KB([[Btn("⬅️ Вернуться", callback_data=f"stbind_{bid}")]])
                )

            set_binding_setting(bid, param, val)
            s["state"] = None
            return await update.message.reply_text(
                f"✅ Сохранено: {val}",
                reply_markup=KB([[Btn("⬅️ К стратегии", callback_data=f"stbind_{bid}")]])
            )

        # === Ручная торговля ===
        if state == "wait_trade_price":
            try:
                price_cents = int(float(text.replace(",", ".")))
            except ValueError:
                return await update.message.reply_text("❌ Введите целое число (1-99)", reply_markup=KB([back("tr_back")]))
            if price_cents < 1 or price_cents > 99:
                return await update.message.reply_text("❌ Цена от 1 до 99 центов", reply_markup=KB([back("tr_back")]))
            s["trade_price_cents"] = price_cents
            s["trade_price"] = price_cents / 100.0
            s["state"] = "wait_trade_size"
            return await update.message.reply_text("💵 Введите количество шар (shares):", reply_markup=KB([back("tr_back")]))

        if state == "wait_trade_size":
            try:
                size = float(text.replace(",", "."))
            except ValueError:
                return await update.message.reply_text("❌ Введите число", reply_markup=KB([back("tr_back")]))
            if size <= 0:
                return await update.message.reply_text("❌ Минимум 0.1", reply_markup=KB([back("tr_back")]))
            s["trade_size"] = size
            s["state"] = "wait_trade_tp"
            return await update.message.reply_text(
                "🟢 Введите отступ Take Profit в центах (0-99):",
                parse_mode="Markdown", reply_markup=KB([back("tr_back")]))

        if state == "wait_trade_tp":
            try:
                tp = int(text)
            except ValueError:
                return await update.message.reply_text("❌ Введите целое число (0-99)", reply_markup=KB([back("tr_back")]))
            s["trade_tp_offset"] = max(0, min(99, tp))
            s["state"] = "wait_trade_sl"
            return await update.message.reply_text(
                "🔴 Введите отступ Stop Loss в центах (0-99):",
                parse_mode="Markdown", reply_markup=KB([back("tr_back")]))

        if state == "wait_trade_sl":
            try:
                sl = int(text)
            except ValueError:
                return await update.message.reply_text("❌ Введите целое число (0-99)", reply_markup=KB([back("tr_back")]))

            s["trade_sl_offset"] = max(0, min(99, sl))
            s["state"] = None

            side = s.get("trade_side", "BUY")
            price = s["trade_price"]
            price_cents = s["trade_price_cents"]
            size = s["trade_size"]
            outcome = s.get("trade_outcome", "?")
            question = s.get("trade_question", "?")
            demo = get_setting("demo_mode", "0") == "1"

            s["abs_tp"] = 0
            s["abs_sl"] = 0
            tp_off = s["trade_tp_offset"]
            sl_off = s["trade_sl_offset"]
            if tp_off > 0 or sl_off > 0:
                if side == "BUY":
                    if tp_off > 0: s["abs_tp"] = min(99, price_cents + tp_off)
                    if sl_off > 0: s["abs_sl"] = max(1, price_cents - sl_off)
                else:
                    if tp_off > 0: s["abs_tp"] = max(1, price_cents - tp_off)
                    if sl_off > 0: s["abs_sl"] = min(99, price_cents + sl_off)

            msg = (
                f"{'🎮 [ДЕМО] ' if demo else ''}{'🟢' if side == 'BUY' else '🔴'} *Подтверждение*\n\n"
                f"📊 Рынок: {question}\n🎯 Исход: *{outcome}*\n"
                f"🔘 Действие: *{'Купить' if side == 'BUY' else 'Продать'}*\n"
                f"💲 Цена: *{price_cents}¢* | 📦 Объём: *{size}*\n"
                f"💰 Итого: *{round(price * size, 2)}$*\n"
            )
            if tp_off > 0 or sl_off > 0:
                msg += "\n⚙️ *Автозакрытие:*\n"
                if tp_off > 0: msg += f"🟢 TP: при *{s['abs_tp']}¢*\n"
                if sl_off > 0: msg += f"🔴 SL: при *{s['abs_sl']}¢*\n"

            return await update.message.reply_text(
                msg, parse_mode="Markdown",
                reply_markup=KB([[Btn("✅ Подтвердить ордер", callback_data="tr_confirm"),
                                  Btn("❌ Отмена", callback_data="tr_back")]]))

        # === API ключи ===
        if state == "wait_poly_pk":
            pk = text.lower()
            if pk.startswith("0x"): pk = pk[2:]
            if len(pk) != 64:
                return await update.message.reply_text("❌ Неверный формат ключа.", reply_markup=KB([back("tr_back")]))
            s["temp_pk"] = text
            s["state"] = "wait_poly_funder"
            return await update.message.reply_text("Отправьте *Funder адрес* (0x... 42 символа).", parse_mode="Markdown")

        if state == "wait_poly_funder":
            funder = text
            pk = s.get("temp_pk")
            s["state"] = None
            import polymarket_trading as pt
            status = await update.message.reply_text("⏳ Инициализация API...")
            pt.update_env_and_config({"POLY_PRIVATE_KEY": pk, "POLY_FUNDER": funder, "POLY_SIGNATURE_TYPE": "3"})
            keys = pt.auto_generate_polymarket_keys(pk)
            if keys:
                pt.update_env_and_config(keys)
            if pt.init_trading():
                return await status.edit_text("✅ Аккаунт успешно подключён!", reply_markup=KB([back("tr_back")]))
            return await status.edit_text("⚠️ Ошибка авторизации. Проверьте логи.", reply_markup=KB([back("tr_back")]))

        # === Новые настройки (API ключи CheckWX и окна) ===
        if state == "wait_cwx_api_key":
            set_setting("checkwx_api_key", text)
            s["state"] = None
            return await update.message.reply_text("✅ API ключ CheckWX успешно сохранен!", parse_mode="Markdown")
        
        if state == "wait_cwx_api_keys":
            clean = ",".join([k.strip() for k in text.split(",") if k.strip()])
            set_setting("checkwx_api_keys", clean)
            s["state"] = None
            count = len([k for k in clean.split(",") if k])
            return await update.message.reply_text(
                f"✅ Сохранено ключей: *{count}*",
                parse_mode="Markdown"
            )
        
        if state == "wait_cwx_minute":
            try:
                val = int(text)
                if not (0 <= val <= 59):
                    raise ValueError
            except ValueError:
                return await update.message.reply_text("❌ Введите число от 0 до 59")
            sid = s.get("cwx_edit_sid")
            if sid:
                set_setting(f"st_{sid}_cwx_minute", str(val))
            s["state"] = None
            return await update.message.reply_text(f"✅ Минута опроса: :{val:02d}")

        if state == "wait_cwx_window":
            try:
                val = int(text)
                if not (1 <= val <= 30):
                    raise ValueError
            except ValueError:
                return await update.message.reply_text("❌ Введите число от 1 до 30")
            sid = s.get("cwx_edit_sid")
            if sid:
                set_setting(f"st_{sid}_cwx_window", str(val))
            s["state"] = None
            return await update.message.reply_text(f"✅ Окно: {val} мин")

        if state == "wait_cwx_lead":
            try:
                val = int(text)
                if not (0 <= val <= 15):
                    raise ValueError
            except ValueError:
                return await update.message.reply_text("❌ Введите число от 0 до 15")
            sid = s.get("cwx_edit_sid")
            if sid:
                set_setting(f"st_{sid}_cwx_lead", str(val))
            s["state"] = None
            return await update.message.reply_text(f"✅ Запас: {val} мин")
        
        if state == "wait_metar_pred_window":
            if not text.isdigit() or int(text) <= 0:
                return await update.message.reply_text("❌ Введите корректное число минут (больше 0).")
            set_setting("metar_pred_window", text)
            s["state"] = None
            return await update.message.reply_text(f"✅ Окно прогноза METAR установлено на: `{text}` минут.", parse_mode="Markdown")

        # === Станции / Рынки ===
        if state == "wait_wu_url":
            sc = extract_code_from_wu(text)
            api = extract_api_url(text)
            if api:
                s.update({"api": api, "code": extract_code_from_api(api) or sc,
                          "wu": text, "state": "wait_wu_name", "type": "wunderground"})
            elif sc:
                s.update({"api": "", "code": sc, "wu": text, "state": "wait_wu_name", "type": "wunderground"})
            else:
                return await update.message.reply_text("❌ Ошибка ссылки.")
            return await update.message.reply_text("Имя станции:")

        if state == "wait_wu_name":
            add_station(text, s.get("api", ""), s.get("wu", ""), s.get("code", ""), s.get("type", "wunderground"))
            s["state"] = None
            return await update.message.reply_text(f"✅ Добавлена: {text}")

        if state == "wait_metar_code":
            s.update({"code": text.upper(), "type": "metar", "state": "wait_metar_name"})
            return await update.message.reply_text(f"✅ {text.upper()} найдена! Имя:")

        if state == "wait_metar_name":
            add_station(text, "", "", s.get("code", ""), "metar")
            s["state"] = None
            return await update.message.reply_text(f"✅ METAR добавлена: {text}")

        # НОВОЕ: Обработчик для быстрого добавления CheckWX
        if state == "wait_cwx_code":
            code = text.upper()
            add_station(f"{code} (CheckWX)", "", "", code, "checkwx")
            s["state"] = None
            return await update.message.reply_text(f"✅ Станция CheckWX добавлена: {code}")

        if state == "wait_mk_url":
            try:
                slug = urlparse(text).path.split("/event/")[1].split("/")[0]
                s.update({"slug": slug, "state": "wait_mk_name"})
                return await update.message.reply_text("Имя рынка:")
            except:
                return await update.message.reply_text("❌ Ошибка ссылки.")

        if state == "wait_mk_name":
            add_market(text, s["slug"])
            s["state"] = None
            return await update.message.reply_text(f"✅ Добавлен: {text}")

        if state == "wait_st_rename_name":
            sid = s.get("rename_st_id")
            if sid:
                update_station(sid, name=text)
                s["state"] = None
                return await update.message.reply_text(f"✅ Станция переименована: {text}", reply_markup=st_kb())
            s["state"] = None
            return await update.message.reply_text("❌ Ошибка.", reply_markup=st_kb())

        if state == "wait_mk_rename_name":
            mid = s.get("rename_mk_id")
            if mid:
                update_market(mid, name=text)
                s["state"] = None
                return await update.message.reply_text(f"✅ Рынок переименован: {text}", reply_markup=mk_kb())
            s["state"] = None
            return await update.message.reply_text("❌ Ошибка.", reply_markup=mk_kb())

    except Exception as e:
        log.exception(f"on_text error: {e}")
        await send_internal_error(update)