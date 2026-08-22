import os
import logging

from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes
import telegram.error

from database import (
    get_setting, set_setting,
    get_stations, get_station,
    get_bindings, get_binding,
    get_binding_setting, set_binding_setting,
    get_all_bindings,
)

import strategies as strategies_mod
from strategies import STRATEGIES

from state import us
from keyboards import api_settings_kb, trade_kb, back

log = logging.getLogger("bot")


# =========================================================
# helpers
# =========================================================

def _safe_strategy_id(strat_id):
    return strat_id if strat_id in STRATEGIES else "front_runner"


def _bind_strategy(bid):
    sid = get_binding_setting(bid, "strategy", "front_runner")
    return _safe_strategy_id(sid)


def _bind_params(bid):
    strat = _bind_strategy(bid)
    return {
        "enabled":      get_binding_setting(bid, "enabled", "0"),
        "direction":    get_binding_setting(bid, "direction", "up"),
        "target":       get_binding_setting(bid, "target", "18"),
        "thresh":       get_binding_setting(bid, "thresh", "40"),
        "tp":           get_binding_setting(bid, "tp", "40"),
        "sl":           get_binding_setting(bid, "sl", "0"),
        "size":         get_binding_setting(bid, "size", "10"),
        "unit":         (get_binding_setting(bid, "unit", "C") or "C").upper(),
        "strategy":     strat,
        "budget_mode":  get_binding_setting(bid, "budget_mode", "shares"),
        "entry_temp":   get_binding_setting(bid, "entry_temp", "17"),
        "stop_temp":    get_binding_setting(bid, "stop_temp", "18"),
    }


def _strategy_name(sid):
    cls = STRATEGIES.get(sid)
    return cls.name if cls else sid


def _bind_card(bid):
    b = get_binding(bid)
    if not b:
        return None, None

    st = get_station(b["station_id"])
    p = _bind_params(bid)
    sname = _strategy_name(p["strategy"])
    strat = p["strategy"]

    dir_text = "📈 Рост (UP)" if p["direction"] == "up" else "📉 Падение (DOWN)"
    en_text = "✅ ВКЛ" if p["enabled"] == "1" else "❌ ВЫКЛ"
    unit = p["unit"]
    bm = "💵 Доллары" if p["budget_mode"] == "dollars" else "📦 Шары"

    msg = (
        f"🤖 *Стратегия:* {sname}\n\n"
        f"🌡 Станция: *{st['name'] if st else b['station_id']}*\n"
        f"📊 Рынок: *{b['market_name']}*\n\n"
        f"⚙️ Статус: {en_text}\n"
        f"↕️ Направление: {dir_text}\n"
        f"📐 Единицы: *°{unit}*\n"
    )

    if strat == "station_stop":
        msg += (
            f"🎯 Температура входа: *{p['entry_temp']}°{unit}*\n"
            f"🛑 Стоп температура: *{p['stop_temp']}°{unit}*\n"
        )
    else:
        msg += f"🎯 Целевая температура: *{p['target']}°{unit}*\n"

    msg += (
        f"🚧 Порог входа (макс цена): *{p['thresh']}¢*\n"
        f"💸 Профит (+ к цене): *+{p['tp']}¢*\n"
        f"🛑 Стоп-лосс (− от цены): *{('-' + str(p['sl']) + '¢') if str(p['sl']) not in ('0', '') else 'выкл'}*\n"
        f"📦 Объём: *{p['size']}* ({bm})\n"
    )

    kb = [
        [Btn(("⏸ Остановить" if p["enabled"] == "1" else "▶️ Запустить"),
             callback_data=f"stbind_toggle_{bid}")],
        [Btn(("» 📈 UP «" if p["direction"] == "up" else "📈 UP"),
             callback_data=f"stbind_dir_{bid}_up"),
         Btn(("» 📉 DOWN «" if p["direction"] == "down" else "📉 DOWN"),
             callback_data=f"stbind_dir_{bid}_down")],
        [Btn(("» °C «" if unit == "C" else "°C"),
             callback_data=f"stbind_unit_{bid}_C"),
         Btn(("» °F «" if unit == "F" else "°F"),
             callback_data=f"stbind_unit_{bid}_F")],
    ]

    if strat == "station_stop":
        kb.append([
            Btn(f"🎯 Вход: {p['entry_temp']}°{unit}", callback_data=f"stbind_edit_{bid}_entry_temp"),
            Btn(f"🛑 Стоп: {p['stop_temp']}°{unit}", callback_data=f"stbind_edit_{bid}_stop_temp")
        ])
    else:
        kb.append([
            Btn(f"🎯 Цель: {p['target']}°{unit}", callback_data=f"stbind_edit_{bid}_target")
        ])

    kb.append([
        Btn(f"🚧 Порог: {p['thresh']}¢", callback_data=f"stbind_edit_{bid}_thresh"),
        Btn(f"💸 TP: +{p['tp']}¢", callback_data=f"stbind_edit_{bid}_tp"),
        Btn(f"🛑 SL: -{p['sl']}¢", callback_data=f"stbind_edit_{bid}_sl")
    ])

    bm_label = "» 💵 $ «" if p["budget_mode"] == "dollars" else "💵 $"
    sh_label = "» 📦 Шары «" if p["budget_mode"] == "shares" else "📦 Шары"
    kb.append([
        Btn(f"📦 Объём: {p['size']}", callback_data=f"stbind_edit_{bid}_size"),
        Btn(sh_label, callback_data=f"stbind_bm_{bid}_shares"),
        Btn(bm_label, callback_data=f"stbind_bm_{bid}_dollars"),
    ])

    kb.append([Btn("⬅️ К связкам",
                   callback_data=f"strat_pick_st_{p['strategy']}_{st['id'] if st else 0}")])
    kb.append([Btn("⬅️ К стратегиям", callback_data="tr_strategies")])

    return msg, KB(kb)


async def _safe_edit(q, text, **kwargs):
    """edit_message_text c подавлением 'Message is not modified'."""
    try:
        return await q.edit_message_text(text, **kwargs)
    except telegram.error.BadRequest as e:
        if "Message is not modified" in str(e):
            try:
                await q.answer()
            except Exception:
                pass
            return
        raise


# =========================================================
# handler
# =========================================================

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    s = us(q.message.chat_id)

    # === Главное меню торговли ===
    if d == "tr_back":
        s["state"] = None
        import polymarket_trading as pt
        demo = get_setting("demo_mode", "0") == "1"
        if demo:
            return await _safe_edit(q, "🎮 *Торговля (ДЕМО)*\nБаланс не используется.",
                                    parse_mode="Markdown", reply_markup=trade_kb())
        if not pt.is_ready():
            return await _safe_edit(q, "💰 *Торговля*\n⚠️ Клиент не инициализирован!",
                                    parse_mode="Markdown", reply_markup=trade_kb())
        return await _safe_edit(
            q,
            f"💰 *Торговля*\nКошелёк: `{pt.get_wallet_address()}`\nБаланс: *{pt.get_balance()}$*",
            parse_mode="Markdown", reply_markup=trade_kb()
        )

    if d == "tr_api_menu":
        return await _safe_edit(q, "⚙️ *Настройки Polymarket API*",
                                parse_mode="Markdown", reply_markup=api_settings_kb())

    if d == "trade_diagnose":
        import polymarket_trading as pt
        return await _safe_edit(
            q,
            f"🔍 *Диагностика Polymarket*\n🔑 EOA: `{pt.get_eoa_address()}`\n"
            f"🤖 Клиент: {'✅ ОК' if pt.is_ready() else '❌ Ошибка'}",
            parse_mode="Markdown",
            reply_markup=KB([
                [Btn("🔄 Переинициализировать", callback_data="trade_reinit")],
                back("tr_api_menu")
            ])
        )

    if d == "trade_reinit":
        import polymarket_trading as pt
        await _safe_edit(q, "⏳ Подключаюсь...")
        if pt.init_trading():
            return await _safe_edit(q, "✅ Подключено!", parse_mode="Markdown",
                                    reply_markup=KB([back("tr_api_menu")]))
        return await _safe_edit(
            q, "❌ Ошибка.", parse_mode="Markdown",
            reply_markup=KB([[Btn("🔑 Привязать", callback_data="trade_add_keys")],
                             back("tr_api_menu")])
        )

    if d == "trade_add_keys":
        s["state"] = "wait_poly_pk"
        return await _safe_edit(q, "Отправьте *Приватный Ключ*.",
                                parse_mode="Markdown",
                                reply_markup=KB([back("tr_api_menu")]))

    if d == "trade_del_keys":
        import polymarket_trading as pt
        pt.update_env_and_config({
            "POLY_PRIVATE_KEY": "", "POLY_API_KEY": "",
            "POLY_API_SECRET": "", "POLY_API_PASSPHRASE": "", "POLY_FUNDER": ""
        })
        pt._client = None
        return await _safe_edit(q, "✅ Ключи сброшены.", parse_mode="Markdown",
                                reply_markup=KB([back("tr_api_menu")]))

    if d == "set_order_timeout":
        s["state"] = "wait_order_timeout"
        return await _safe_edit(
            q,
            "⏱ Введите *таймаут ордера в секундах* (от 5 до 300).\n"
            "_Сколько ждать исполнения, прежде чем отменить ордер._",
            parse_mode="Markdown",
            reply_markup=KB([back("tr_api_menu")])
        )

    if d == "set_order_retries":
        s["state"] = "wait_order_retries"
        return await _safe_edit(
            q,
            "🔁 Введите *количество попыток входа* (от 1 до 10).\n"
            "_Если ордер не исполнился — сколько раз пробовать заново._",
            parse_mode="Markdown",
            reply_markup=KB([back("tr_api_menu")])
        )

    if d == "tr_toggle_demo" or d == "toggle_demo_mode":
        curr = get_setting("demo_mode", "0")
        set_setting("demo_mode", "1" if curr == "0" else "0")
        demo = get_setting("demo_mode", "0") == "1"
        import polymarket_trading as pt
        msg = ("🎮 *Торговля (ДЕМО)*\nБаланс не используется."
               if demo
               else f"💰 *Торговля*\nКошелёк: `{pt.get_wallet_address()}`\nБаланс: *{pt.get_balance()}$*")
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=trade_kb())

    if d == "sys_logs":
        try:
            if not os.path.exists("bot.log"):
                return await _safe_edit(q, "Файл логов пуст.", reply_markup=KB([back("tr_back")]))
            with open("bot.log", "r", encoding="utf-8") as f:
                lines = f.readlines()[-40:]
            log_txt = "".join(lines)
            if len(log_txt) > 4000:
                log_txt = log_txt[-4000:]
            return await _safe_edit(q, f"```text\n{log_txt}\n```", parse_mode="Markdown",
                                    reply_markup=KB([back("tr_back")]))
        except Exception as e:
            return await _safe_edit(q, f"Ошибка: {e}", reply_markup=KB([back("tr_back")]))

    # =========================================================
    # МЕНЮ СТРАТЕГИЙ
    # =========================================================
    if d == "tr_strategies":
        strats = strategies_mod.get_strategies_list()
        if not strats:
            return await _safe_edit(q, "Нет доступных стратегий.",
                                    reply_markup=KB([back("tr_back")]))
        kb = [[Btn(f"🤖 {st['name']}", callback_data=f"strat_pick_{st['id']}")] for st in strats]
        kb.append([Btn("📋 Активные стратегии", callback_data="strat_active")])
        kb.append(back("tr_back"))
        return await _safe_edit(
            q,
            "🤖 *Стратегии*\nВыберите стратегию для настройки:",
            parse_mode="Markdown", reply_markup=KB(kb)
        )

    if d == "strat_active":
        all_b = get_all_bindings()
        active = [b for b in all_b if get_binding_setting(b["id"], "enabled", "0") == "1"]
        if not active:
            return await _safe_edit(
                q, "📋 *Активные стратегии*\n\nНет включённых.",
                parse_mode="Markdown",
                reply_markup=KB([back("tr_strategies")])
            )
        msg = "📋 *Активные стратегии:*\n\n"
        kb = []
        for b in active:
            st = get_station(b["station_id"])
            p = _bind_params(b["id"])
            sname = _strategy_name(p["strategy"])
            dir_s = "📈" if p["direction"] == "up" else "📉"
            unit = p["unit"]
            if p["strategy"] == "station_stop":
                target_line = f"вход {p['entry_temp']}°{unit}, стоп {p['stop_temp']}°{unit}"
            else:
                target_line = f"цель {p['target']}°{unit}"
            msg += (
                f"• [{sname}] {dir_s} {st['name'] if st else b['station_id']} → {b['market_name']}\n"
                f"  {target_line}, порог {p['thresh']}¢, TP +{p['tp']}¢, объём {p['size']}\n"
            )
            kb.append([Btn(f"⚙️ {b['market_name'][:40]}", callback_data=f"stbind_{b['id']}")])
        kb.append(back("tr_strategies"))
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=KB(kb))

    if d.startswith("strat_pick_st_"):
        rest = d[len("strat_pick_st_"):]
        try:
            strat_id, sid_str = rest.rsplit("_", 1)
            sid = int(sid_str)
        except:
            return await q.answer("Ошибка", show_alert=True)
        strat_id = _safe_strategy_id(strat_id)

        st = get_station(sid)
        if not st:
            return await _safe_edit(q, "❌ Станция не найдена.",
                                    reply_markup=KB([back("tr_strategies")]))

        binds = get_bindings(sid)
        if not binds:
            return await _safe_edit(
                q,
                f"🌡 *{st['name']}*\n\nК этой станции не привязан ни один рынок.\n\n"
                f"Привяжите рынки через меню *📊 Рынки → 🔗 Привязать*.",
                parse_mode="Markdown",
                reply_markup=KB([
                    [Btn("⬅️ К стратегии", callback_data=f"strat_pick_{strat_id}")],
                    back("tr_strategies")
                ])
            )

        kb = []
        for b in binds:
            p = _bind_params(b["id"])
            mark = "✅" if p["enabled"] == "1" else "▫️"
            dir_s = "📈" if p["direction"] == "up" else "📉"
            kb.append([
                Btn(f"{mark} {dir_s} {b['market_name'][:45]}",
                    callback_data=f"stbind_open_{b['id']}_{strat_id}")
            ])
        kb.append([Btn("⬅️ Назад к станциям", callback_data=f"strat_pick_{strat_id}")])
        kb.append(back("tr_strategies"))

        return await _safe_edit(
            q,
            f"🤖 *{_strategy_name(strat_id)}*\n🌡 *{st['name']}*\n\nВыберите связку:",
            parse_mode="Markdown", reply_markup=KB(kb)
        )

    if d.startswith("strat_pick_"):
        strat_id = _safe_strategy_id(d[len("strat_pick_"):])
        sts = get_stations()
        if not sts:
            return await _safe_edit(
                q,
                f"🤖 *{_strategy_name(strat_id)}*\n\nСначала добавьте хотя бы одну станцию.",
                parse_mode="Markdown",
                reply_markup=KB([back("tr_strategies")])
            )
        kb = [[Btn(f"🌡 {st['name']}", callback_data=f"strat_pick_st_{strat_id}_{st['id']}")] for st in sts]
        kb.append(back("tr_strategies"))
        return await _safe_edit(
            q,
            f"🤖 *{_strategy_name(strat_id)}*\nВыберите станцию:",
            parse_mode="Markdown", reply_markup=KB(kb)
        )

    # === Карточка связки ===
    if d.startswith("stbind_open_"):
        rest = d[len("stbind_open_"):]
        try:
            bid_str, strat_id = rest.split("_", 1)
            bid = int(bid_str)
        except:
            return await q.answer("Ошибка", show_alert=True)
        strat_id = _safe_strategy_id(strat_id)
        cur = get_binding_setting(bid, "strategy", "")
        if cur != strat_id:
            set_binding_setting(bid, "strategy", strat_id)
        msg, kb = _bind_card(bid)
        if msg is None:
            return await _safe_edit(q, "❌ Связка не найдена.",
                                    reply_markup=KB([back("tr_strategies")]))
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_toggle_"):
        bid = int(d[len("stbind_toggle_"):])
        cur = get_binding_setting(bid, "enabled", "0")
        set_binding_setting(bid, "enabled", "0" if cur == "1" else "1")
        msg, kb = _bind_card(bid)
        if msg is None:
            return await _safe_edit(q, "❌ Связка не найдена.",
                                    reply_markup=KB([back("tr_strategies")]))
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_dir_"):
        rest = d[len("stbind_dir_"):]
        try:
            bid_str, direction = rest.split("_", 1)
            bid = int(bid_str)
        except:
            return await q.answer("Ошибка", show_alert=True)
        if direction not in ("up", "down"):
            return await q.answer("Ошибка направления", show_alert=True)
        current = get_binding_setting(bid, "direction", "up")
        if current == direction:
            return await q.answer(f"Уже выбрано: {direction.upper()}", show_alert=False)
        set_binding_setting(bid, "direction", direction)
        msg, kb = _bind_card(bid)
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_unit_"):
        rest = d[len("stbind_unit_"):]
        try:
            bid_str, unit = rest.split("_", 1)
            bid = int(bid_str)
        except:
            return await q.answer("Ошибка", show_alert=True)
        unit = (unit or "C").upper()
        if unit not in ("C", "F"):
            return await q.answer("Только C или F", show_alert=True)
        current = (get_binding_setting(bid, "unit", "C") or "C").upper()
        if current == unit:
            return await q.answer(f"Уже выбрано: °{unit}", show_alert=False)
        set_binding_setting(bid, "unit", unit)
        msg, kb = _bind_card(bid)
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_bm_"):
        rest = d[len("stbind_bm_"):]
        try:
            bid_str, mode = rest.split("_", 1)
            bid = int(bid_str)
        except:
            return await q.answer("Ошибка", show_alert=True)
        if mode not in ("shares", "dollars"):
            return await q.answer("Только shares или dollars", show_alert=True)
        current = get_binding_setting(bid, "budget_mode", "shares")
        if current == mode:
            return await q.answer(f"Уже выбрано: {mode}", show_alert=False)
        set_binding_setting(bid, "budget_mode", mode)
        msg, kb = _bind_card(bid)
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_edit_"):
        rest = d[len("stbind_edit_"):]
        try:
            bid_str, param = rest.split("_", 1)
            bid = int(bid_str)
        except:
            return await q.answer("Ошибка", show_alert=True)
        if param not in ("target", "thresh", "tp", "sl", "size", "entry_temp", "stop_temp"):
            return await q.answer("Неизвестный параметр", show_alert=True)

        s["state"] = f"wait_bind_{param}"
        s["edit_bind_id"] = bid

        unit = (get_binding_setting(bid, "unit", "C") or "C").upper()

        prompts = {
            "target":     f"🎯 Введите *целевую температуру в °{unit}* (например 89 или -5):",
            "entry_temp": f"🎯 Введите *температуру входа в °{unit}*:",
            "stop_temp":  f"🛑 Введите *стоп температуру в °{unit}* (при достижении — закрытие):",
            "thresh":     "🚧 Введите *порог входа* в центах (1–99):",
            "tp":         "💸 Введите *профит* в центах (+ к цене входа), 0 — выключить:",
            "sl":         "🛑 Введите *стоп-лосс* в центах (− от цены входа), 0 — выключить:",
            "size":       "📦 Введите *объём* (в шарах или долларах — зависит от выбранного режима):"
        }
        return await _safe_edit(
            q,
            prompts[param], parse_mode="Markdown",
            reply_markup=KB([[Btn("⬅️ Отмена", callback_data=f"stbind_{bid}")]])
        )

    if d.startswith("stbind_"):
        try:
            bid = int(d[len("stbind_"):])
        except:
            return
        msg, kb = _bind_card(bid)
        if msg is None:
            return await _safe_edit(q, "❌ Связка не найдена.",
                                    reply_markup=KB([back("tr_strategies")]))
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)