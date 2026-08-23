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
        "stop_temp":    get_binding_setting(bid, "stop_temp", "0"),
        "entry_type":   get_binding_setting(bid, "entry_type", "market"),
        "blocked":      get_binding_setting(bid, "blocked", "0"),
        "blocked_reason": get_binding_setting(bid, "blocked_reason", ""),
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

    if strat in ("station_stop", "market_maker"):
        stop_manual = str(p["stop_temp"]) not in ("0", "", "None")
        stop_txt = f"{p['stop_temp']}°{unit}" if stop_manual else "авто (по корзине входа)"
        et = "⚡️ По рынку (FOK)" if p["entry_type"] == "market" else "📋 Отложником (GTC)"

        msg += (
            f"🛑 Стоп-температура: *{stop_txt}*\n"
            f"🚪 Способ входа: *{et}*\n"
            f"🚧 Цена отложника: *{p['thresh']}¢*\n"
            f"💸 TP: *+{p['tp']}¢* | SL: *{('-' + str(p['sl']) + '¢') if str(p['sl']) not in ('0', '') else 'выкл'}*\n"
            f"📦 Объём: *{p['size']}* ({bm})\n"
        )
        if p["blocked"] == "1":
            reasons = {"station_stop": "стоп по станции", "sl": "стоп-лосс", "tp": "тейк-профит"}
            msg += f"\n🔒 *Связка заблокирована* ({reasons.get(p['blocked_reason'], 'сделка закрыта')}). Новых входов не будет.\n"

        msg += (
            f"\n_Логика: входим кнопкой в нужную корзину. Если станция покажет "
            f"температуру {'выше' if p['direction'] == 'up' else 'ниже'} стопа — "
            f"позиция немедленно продаётся по рынку._"
        )

        kb.append([Btn("🎯 Войти в рынок", callback_data=f"stbind_enter_{bid}")])
        kb.append([
            Btn(("» ⚡️ По рынку «" if p["entry_type"] == "market" else "⚡️ По рынку"),
                callback_data=f"stbind_et_{bid}_market"),
            Btn(("» 📋 Отложник «" if p["entry_type"] == "limit" else "📋 Отложник"),
                callback_data=f"stbind_et_{bid}_limit"),
        ])
        kb.append([
            Btn(f"🛑 Стоп: {stop_txt}", callback_data=f"stbind_edit_{bid}_stop_temp"),
            Btn(f"🚧 Цена: {p['thresh']}¢", callback_data=f"stbind_edit_{bid}_thresh"),
        ])
        kb.append([
            Btn(f"💸 TP: +{p['tp']}¢", callback_data=f"stbind_edit_{bid}_tp"),
            Btn(f"🛑 SL: -{p['sl']}¢", callback_data=f"stbind_edit_{bid}_sl"),
        ])
        bm_label = "» 💵 $ «" if p["budget_mode"] == "dollars" else "💵 $"
        sh_label = "» 📦 Шары «" if p["budget_mode"] == "shares" else "📦 Шары"
        kb.append([
            Btn(f"📦 Объём: {p['size']}", callback_data=f"stbind_edit_{bid}_size"),
            Btn(sh_label, callback_data=f"stbind_bm_{bid}_shares"),
            Btn(bm_label, callback_data=f"stbind_bm_{bid}_dollars"),
        ])
        if p["blocked"] == "1":
            kb.append([Btn("🔓 Снять блокировку", callback_data=f"stbind_unblock_{bid}")])

    else:
        msg += f"🎯 Целевая температура: *{p['target']}°{unit}*\n"
        msg += (
            f"🚧 Порог входа (макс цена): *{p['thresh']}¢*\n"
            f"💸 Профит (+ к цене): *+{p['tp']}¢*\n"
            f"🛑 Стоп-лосс (− от цены): *{('-' + str(p['sl']) + '¢') if str(p['sl']) not in ('0', '') else 'выкл'}*\n"
            f"📦 Объём: *{p['size']}* ({bm})\n"
        )
        kb.append([Btn(f"🎯 Цель: {p['target']}°{unit}", callback_data=f"stbind_edit_{bid}_target")])
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


async def _station_stop_enter(bid, option):
    """
    Ручной вход стратегии «Метео Стоп»: по рынку (FOK) или отложником (GTC).
    Позиция сохраняется вместе с meta: связка, станция, стоп-температура, корзина.
    """
    import polymarket_trading as pt
    from database import add_position, get_setting as _gs
    from strategies import (
        parse_option_bucket, detect_market_unit, auto_stop_temp,
        convert_station_temp, sort_asks,
    )

    b = get_binding(bid)
    if not b:
        return "❌ Связка не найдена."

    p = _bind_params(bid)
    token_id = option.get("token_yes")
    label = option.get("label", "?")
    demo = _gs("demo_mode", "0") == "1"

    market_unit = detect_market_unit([option], default=p["unit"]) or p["unit"]
    bucket = parse_option_bucket(label)

    # Стоп: ручной (в единицах связки) либо авто по корзине (в единицах рынка)
    try:
        manual_stop = float(p["stop_temp"])
    except (TypeError, ValueError):
        manual_stop = 0.0

    if manual_stop:
        stop_temp = manual_stop
        if (p["unit"] or "C").upper() != market_unit:
            stop_c = manual_stop if (p["unit"] or "C").upper() == "C" else (manual_stop - 32) * 5 / 9
            stop_temp = convert_station_temp(stop_c, market_unit)
        stop_src = "задан вручную"
    else:
        stop_temp = auto_stop_temp(bucket, p["direction"])
        stop_src = "рассчитан по корзине"

    if stop_temp is None:
        return (f"❌ Для корзины «{label}» стоп в сторону "
                f"{'вверх' if p['direction'] == 'up' else 'вниз'} невозможен — "
                f"она открыта в эту сторону. Выберите другую корзину или направление.")

    try:
        size_val = float(p["size"])
    except (TypeError, ValueError):
        size_val = 10.0

    try:
        thresh_cents = float(p["thresh"])
    except (TypeError, ValueError):
        thresh_cents = 55.0
    limit_price = max(0.01, min(0.99, thresh_cents / 100.0))

    entry_type = p["entry_type"]

    # ---- расчёт цены и объёма
    book = pt.get_order_book(token_id) if not demo else None
    est_price = limit_price
    if book and book.get("asks"):
        est_price = sort_asks(book["asks"])[0]["price"]

    if demo:
        shares = round(size_val / est_price, 2) if p["budget_mode"] == "dollars" else size_val
        fill_cents = int(round(est_price * 100))
        ok, order_type, order_id = True, ("FOK" if entry_type == "market" else "GTC"), f"DEMO-{bid}"
        filled_size = shares
    elif entry_type == "market":
        if p["budget_mode"] == "dollars":
            res = pt.place_market_order(token_id, "BUY", size_val, limit_price, "FOK")
        else:
            res = pt.place_order(token_id, "BUY", limit_price, size_val, "FOK")
            if res.get("success") and not res.get("filled"):
                res = {"error": f"FOK не исполнен: нет {size_val} шар дешевле {round(limit_price*100)}¢"}
        ok = bool(res.get("success"))
        if not ok:
            return f"❌ *Вход не состоялся*\n🎯 {label}\n`{res.get('error')}`"
        order_type, order_id = res.get("order_type", "FOK"), res.get("orderID", "—")
        filled_size = float(res.get("filled_size") or 0) or size_val
        fill_cents = int(round(float(res.get("avg_price_cents") or est_price * 100)))
    else:
        shares = round(size_val / limit_price, 2) if p["budget_mode"] == "dollars" else size_val
        res = pt.place_order(token_id, "BUY", limit_price, shares, "GTC")
        ok = bool(res.get("success"))
        if not ok:
            return f"❌ *Отложник не выставлен*\n🎯 {label}\n`{res.get('error')}`"
        order_type, order_id = "GTC", res.get("orderID", "—")
        filled_size = float(res.get("filled_size") or 0) or shares
        fill_cents = int(round(float(res.get("avg_price_cents") or limit_price * 100)))

    # ---- TP/SL от фактической цены
    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    tp_delta, sl_delta = _int(p["tp"]), _int(p["sl"])
    tp_abs = min(99, fill_cents + tp_delta) if tp_delta > 0 else 0
    sl_abs = max(1, fill_cents - sl_delta) if sl_delta > 0 else 0

    add_position(
        1 if demo else 0,
        b["market_slug"], token_id, "BUY", filled_size,
        sl_abs, tp_abs, fill_cents, b["market_name"], "YES",
        meta={
            "strategy": "station_stop",
            "binding_id": bid,
            "station_id": b["station_id"],
            "outcome_label": label,
            "market_unit": market_unit,
            "direction": p["direction"],
            "stop_temp": stop_temp,
            "bucket": list(bucket[:2]) if bucket else None,
            "entry_type": entry_type,
        }
    )

    mode_label = "🎮 ДЕМО" if demo else "💰 РЕАЛ"
    kind = "рыночный FOK" if entry_type == "market" else f"отложник GTC по {round(limit_price*100)}¢"
    arrow = "выше" if p["direction"] == "up" else "ниже"

    return (
        f"✅ *Вход выполнен ({mode_label})*\n\n"
        f"📊 {b['market_name']}\n"
        f"🎯 Корзина: *{label}*\n"
        f"⚡️ Ордер: {kind}\n"
        f"💲 Цена: {fill_cents}¢ | 📦 Объём: {filled_size} шт.\n"
        f"🆔 `{str(order_id)[:14]}`\n\n"
        f"🛑 Стоп по станции: *{stop_temp}°{market_unit}* и {arrow} ({stop_src})\n"
        f"💸 TP: {tp_abs or '—'}¢ | SL: {sl_abs or '—'}¢\n\n"
        f"_При сигнале станции позиция будет продана по рынку немедленно._"
    )


async def _safe_edit(q, text, **kwargs):
    """
    edit_message_text c подавлением 'Message is not modified' и страховкой
    от кривой разметки: если Telegram не смог разобрать Markdown
    (например, из-за подчёркиваний в именах переменных), отправляем как есть.
    """
    try:
        return await q.edit_message_text(text, **kwargs)
    except telegram.error.BadRequest as e:
        msg = str(e)
        if "Message is not modified" in msg:
            try:
                await q.answer()
            except Exception:
                pass
            return
        if "parse entities" in msg or "parse_mode" in msg:
            log.warning(f"Markdown не разобран ({msg}) — отправляю без разметки")
            plain = dict(kwargs)
            plain.pop("parse_mode", None)
            try:
                return await q.edit_message_text(_strip_md(text), **plain)
            except telegram.error.BadRequest as e2:
                if "Message is not modified" in str(e2):
                    return
                raise
        raise


def _md(text) -> str:
    """Экранируем markdown-символы в подставляемых значениях (POLY_FUNDER и т.п.)."""
    out = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        out = out.replace(ch, "\\" + ch)
    return out


def _strip_md(text: str) -> str:
    """Убираем markdown-символы, чтобы текст читался без разметки."""
    out = str(text)
    for ch in ("*", "`"):
        out = out.replace(ch, "")
    return out


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
        info = pt.wallet_diagnostics()
        txt = (
            "🩺 *Диагностика кошелька Polymarket*\n\n"
            f"🔑 Подписант (EOA): `{info['eoa'] or '—'}`\n"
            f"🏦 Кошелёк ордеров (funder): `{info['funder'] or 'НЕ ЗАДАН'}`\n"
            f"✍️ Тип подписи: *{info['signature_type']}* — {_md(info['sig_name'])}\n"
            f"🔐 API-ключи: {'✅' if info['has_creds'] else '❌'}\n"
            f"🤖 Клиент: {'✅ готов' if info['ready'] else '❌ не инициализирован'}\n"
            f"💰 Баланс: {info['balance'] if info['balance'] is not None else '—'}$\n"
        )
        u = info.get("unified") or {}
        txt += (
            "\n🧩 *Официальный SDK (polymarket-client)*\n"
            f"Установлен: {'✅' if u.get('installed') else '❌ нет'}\n"
            f"Режим POLY_SDK: `{u.get('mode', 'auto')}`\n"
            f"Python: {u.get('python', '?')}"
            f"{'' if u.get('python_ok', True) else ' ⚠️ нужен 3.11+'}\n"
        )
        if u.get("hint"):
            txt += f"❗ {_md(u['hint'])}\n"
        if u.get("ready"):
            txt += (f"Кошелёк аккаунта: `{u.get('wallet')}`\n"
                    f"Тип: *{_md(u.get('wallet_type'))}*\n")
        elif u.get("error"):
            txt += f"Ошибка: `{_md(str(u['error'])[:150])}`\n"

        if info["problems"]:
            txt += "\n⚠️ *Проблемы:*\n" + "\n".join(f"• {_md(p)}" for p in info["problems"])
            txt += (
                "\n\n💡 Все кошельки Polymarket, созданные с мая 2026, — это Deposit Wallet, "
                "и старый py-clob-client-v2 их не умеет. Поставьте `polymarket-client`, "
                "пропишите `POLY_SDK=unified` и адрес кошелька аккаунта в `POLY_FUNDER`. "
                "Точную конфигурацию покажет `python3 poly_wallet_check.py` на сервере."
            )
        else:
            txt += "\n✅ Конфигурация выглядит корректно."
        return await _safe_edit(
            q, txt, parse_mode="Markdown",
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
            if p["strategy"] in ("station_stop", "market_maker"):
                stop_txt = f"{p['stop_temp']}°{unit}" if str(p["stop_temp"]) not in ("0", "", "None") else "авто"
                target_line = f"стоп по станции {stop_txt}" + (" 🔒" if p["blocked"] == "1" else "")
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

    # === МЕТЕО СТОП: ручной вход в рынок ===
    if d.startswith("stbind_et_"):
        rest = d[len("stbind_et_"):]
        try:
            bid_str, mode = rest.split("_", 1)
            bid = int(bid_str)
        except Exception:
            return await q.answer("Ошибка", show_alert=True)
        if mode not in ("market", "limit"):
            return await q.answer("Неизвестный способ входа", show_alert=True)
        set_binding_setting(bid, "entry_type", mode)
        msg, kb = _bind_card(bid)
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_unblock_"):
        bid = int(d[len("stbind_unblock_"):])
        set_binding_setting(bid, "blocked", "0")
        set_binding_setting(bid, "blocked_reason", "")
        await q.answer("🔓 Блокировка снята", show_alert=False)
        msg, kb = _bind_card(bid)
        return await _safe_edit(q, msg, parse_mode="Markdown", reply_markup=kb)

    if d.startswith("stbind_enter_"):
        bid = int(d[len("stbind_enter_"):])
        b = get_binding(bid)
        if not b:
            return await _safe_edit(q, "❌ Связка не найдена.", reply_markup=KB([back("tr_strategies")]))

        if get_binding_setting(bid, "blocked", "0") == "1":
            return await q.answer("🔒 Связка заблокирована. Сначала снимите блокировку.", show_alert=True)

        await _safe_edit(q, "⏳ Загружаю исходы рынка...")

        from utils import fetch_market
        md = fetch_market(b["market_slug"])
        if not md or not md.get("options"):
            return await _safe_edit(q, "❌ Нет данных с Polymarket.",
                                    reply_markup=KB([[Btn("⬅️ Назад", callback_data=f"stbind_{bid}")]]))

        tradable = [o for o in md["options"] if o.get("token_yes") and o.get("active", True)]
        if not tradable:
            return await _safe_edit(q, "❌ У рынка нет торгуемых исходов.",
                                    reply_markup=KB([[Btn("⬅️ Назад", callback_data=f"stbind_{bid}")]]))

        s[f"stopenter_{bid}"] = tradable
        p = _bind_params(bid)
        et = "⚡️ по рынку (FOK)" if p["entry_type"] == "market" else f"📋 отложником по {p['thresh']}¢"

        kb = []
        lines = [f"🎯 *Вход в рынок* — {et}", f"📊 {b['market_name']}", "", "Выберите температурную корзину:"]
        for i, o in enumerate(tradable[:20]):
            prob = o.get("prob")
            price = f"{prob}¢" if isinstance(prob, (int, float)) else "?"
            lines.append(f"🔹 {o['label']}: {price}")
            kb.append([Btn(f"{o['label']} · {price}", callback_data=f"stbind_go_{bid}_{i}")])
        kb.append([Btn("⬅️ Назад", callback_data=f"stbind_{bid}")])
        return await _safe_edit(q, "\n".join(lines), parse_mode="Markdown", reply_markup=KB(kb))

    if d.startswith("stbind_go_"):
        rest = d[len("stbind_go_"):]
        try:
            bid_str, idx_str = rest.rsplit("_", 1)
            bid, idx = int(bid_str), int(idx_str)
        except Exception:
            return await q.answer("Ошибка", show_alert=True)

        options = s.get(f"stopenter_{bid}") or []
        if idx >= len(options):
            return await q.answer("Список устарел, откройте заново", show_alert=True)

        await _safe_edit(q, "⏳ Отправляю ордер...")
        text = await _station_stop_enter(bid, options[idx])
        return await _safe_edit(
            q, text, parse_mode="Markdown",
            reply_markup=KB([[Btn("⬅️ К связке", callback_data=f"stbind_{bid}")]])
        )

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
            "stop_temp":  f"🛑 Введите *стоп-температуру в °{unit}* (0 — считать автоматически по корзине входа):",
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