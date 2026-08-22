import logging
import asyncio
import time
import json
import math
from datetime import datetime, timezone

from telegram.ext import ContextTypes

from database import (
    get_stations, get_markets, get_setting, set_setting,
    add_station_history, update_station, get_station_history,
    add_market_history, update_market,
    get_positions, remove_position, add_trade_history,
    get_bindings, add_position, update_position_size, update_position_limits,
    get_binding_setting, get_station,
)
from utils import fetch_market
from formatters import fetch_station_data, format_bound_markets_block
from strategies import STRATEGIES, sort_asks, sort_bids
import polymarket_trading as pt

log = logging.getLogger("bot")


def round_metar(temp):
    if temp is None:
        return None
    return int(math.floor(temp + 0.5))


def fmt_metar_temp(val):
    if val is None:
        return ""
    return f"M{abs(val)}" if val < 0 else f"{val}"


def _checkwx_window_now(st):
    """Сейчас ли окно опроса для CheckWX станции?"""
    sid = st["id"]

    try:
        minute = int(get_setting(f"st_{sid}_cwx_minute",
                                 get_setting("checkwx_default_minute", "0")))
    except:
        minute = 0
    try:
        window = int(get_setting(f"st_{sid}_cwx_window",
                                 get_setting("checkwx_default_window", "10")))
    except:
        window = 10
    try:
        lead = int(get_setting(f"st_{sid}_cwx_lead",
                               get_setting("checkwx_default_lead", "5")))
    except:
        lead = 5

    now_utc = datetime.now(timezone.utc)
    cur = now_utc.minute

    start = (minute - lead) % 60
    end = (minute + window) % 60

    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end


async def job_stations(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    cid = job.data.get("cid") if job.data else None
    if not cid:
        return

    notify_stations = get_setting("notifications", "1") == "1"

    stype_filter = job.data.get("stype")
    active_stations = [
        st for st in get_stations()
        if st.get("enabled") and st.get("station_type") == stype_filter
    ]

    for st in active_stations:
        try:
            # Для CheckWX — пропускаем опрос вне окна
            if st.get("station_type") == "checkwx":
                if not _checkwx_window_now(st):
                    continue

            st_data = fetch_station_data(st)
            if not st_data or st_data.get("temp") is None:
                continue

            current_temp = float(st_data["temp"])
            old_temp = st.get("last_temp")

            stype = st.get("station_type")
            always_metar = (
                get_setting("metar_always_notify", "0") == "1"
                and stype in ("metar", "checkwx")
            )
            temp_changed = old_temp is not None and float(old_temp) != current_temp
            first_time = old_temp is None

            should_notify = notify_stations and (temp_changed or always_metar or first_time)

            if should_notify:
                if old_temp is None:
                    old_temp_f = current_temp
                    diff = 0.0
                    trend_emoji, diff_str = ("🆕", "первое")
                else:
                    old_temp_f = float(old_temp)
                    diff = current_temp - old_temp_f
                    if diff > 0:
                        trend_emoji, diff_str = ("📈", f"+{diff:.1f}")
                    elif diff < 0:
                        trend_emoji, diff_str = ("📉", f"{diff:.1f}")
                    else:
                        trend_emoji, diff_str = ("➡️", "без изменений")

                try:
                    bound_text = format_bound_markets_block(st["id"])
                except:
                    bound_text = ""
                bindings = get_bindings(st["id"])
                unit = get_binding_setting(bindings[0]["id"], "unit", "C") if bindings else "C"

                title = (
                    "🌡 *Обновление METAR*"
                    if stype in ("metar", "checkwx")
                    else "🌡 *Изменение погоды*"
                )
                msg = (
                    f"{title}: {st.get('name', 'Станция')}\n"
                    f"{trend_emoji} Было: `{old_temp_f}°{unit}` ➡️ Стало: `{current_temp}°{unit}` ({diff_str})\n\n"
                    f"{bound_text}"
                )
                await context.bot.send_message(chat_id=cid, text=msg, parse_mode="Markdown")

            add_station_history(st["id"], current_temp)
            update_station(st["id"], last_temp=current_temp)

            # Прогноз METAR только для WU
            if st.get("station_type") == "wunderground":
                st_history = get_station_history(st["id"])
                pred_window_minutes = int(get_setting("metar_pred_window", "10"))
                time_limit = time.time() - (pred_window_minutes * 60)
                recent_temps = [h[1] for h in st_history if h[0] >= time_limit]
                if current_temp not in recent_temps and len(recent_temps) == 0:
                    recent_temps.append(current_temp)

                if recent_temps:
                    avg_temp = sum(recent_temps) / len(recent_temps)
                    current_pred_val = round_metar(avg_temp)
                    prev_pred_str = get_setting(f"last_pred_metar_{st['id']}", "")

                    if prev_pred_str != "":
                        prev_pred_val = int(prev_pred_str)
                        if current_pred_val != prev_pred_val:
                            trend_label = "📈 Повышение" if current_pred_val > prev_pred_val else "📉 Понижение"
                            msg_pred = (
                                f"🔮 *Опережающий сигнал METAR*\n"
                                f"📡 *Станция:* {st.get('name', 'WU Станция')}\n\n"
                                f"{trend_label} температуры в следующем отчете!\n"
                                f"Было в тренде: `{fmt_metar_temp(prev_pred_val)}°C` ➡️ Ожидается: `{fmt_metar_temp(current_pred_val)}°C`\n\n"
                                f"💡 *Обоснование:* Среднее значение по датчикам WU за {pred_window_minutes} мин: `{avg_temp:.2f}°C`."
                            )
                            if notify_stations:
                                await context.bot.send_message(chat_id=cid, text=msg_pred, parse_mode="Markdown")
                    set_setting(f"last_pred_metar_{st['id']}", str(current_pred_val))

            # Торговая логика
            bindings = get_bindings(st["id"])
            for b in bindings:
                bid = b["id"]
                if get_binding_setting(bid, "enabled", "0") != "1":
                    continue
                strat_id = get_binding_setting(bid, "strategy", "temperature_sniper")
                if strat_id not in STRATEGIES:
                    continue

                kwargs = {
                    "direction": get_binding_setting(bid, "direction", "up"),
                    "target": get_binding_setting(bid, "target", "0"),
                    "thresh": get_binding_setting(bid, "thresh", "55"),
                    "size": get_binding_setting(bid, "size", "10"),
                    "budget_mode": get_binding_setting(bid, "budget_mode", "dollars"),
                    "unit": get_binding_setting(bid, "unit", "C"),
                }
                strategy_inst = STRATEGIES[strat_id](**kwargs)
                market_slug = b.get("market_slug")
                market_data = fetch_market(market_slug)
                if not market_data or not market_data.get("options"):
                    continue

                order_books = {}
                for opt in market_data.get("options", []):
                    token_id = opt.get("token_yes") or opt.get("condition_id")
                    if token_id:
                        book = pt.get_order_book(token_id)
                        if book:
                            order_books[token_id] = book
                        await asyncio.sleep(0.05)

                signal = strategy_inst.analyze_market(market_data, {"temp": current_temp}, order_books)
                if signal and signal.get("action") == "BUY":
                    existing_positions = get_positions()
                    if any(p["token_id"] == signal["token_id"] for p in existing_positions):
                        continue

                    demo_mode = get_setting("demo_mode", "0") == "1"

                    res = execute_entry(signal, demo_mode)

                    if not res.get("success"):
                        await context.bot.send_message(
                            chat_id=cid,
                            text=(
                                f"⚠️ *Вход не состоялся ({signal.get('order_type', 'FOK')})*\n"
                                f"📌 {signal.get('question', '')}\n"
                                f"`{res.get('error')}`"
                            ),
                            parse_mode="Markdown"
                        )
                        continue

                    # Позиция пишется в БД ТОЛЬКО по факту подтверждённого филла
                    filled_size = res["filled_size"]
                    fill_cents = res["fill_cents"]

                    # tp/sl в связке заданы как дельта в центах от цены входа
                    def _delta(key):
                        try:
                            return int(float(get_binding_setting(bid, key, "0") or 0))
                        except (TypeError, ValueError):
                            return 0

                    tp_delta, sl_delta = _delta("tp"), _delta("sl")
                    tp_val = min(99, fill_cents + tp_delta) if tp_delta > 0 else 0
                    sl_val = max(1, fill_cents - sl_delta) if sl_delta > 0 else 0

                    add_position(
                        1 if demo_mode else 0,
                        market_slug, signal["token_id"], "BUY", filled_size,
                        sl_val, tp_val, fill_cents,
                        signal["question"], "YES"
                    )

                    mode_label = "🎮 ДЕМО" if demo_mode else "💰 РЕАЛ"
                    partial = ""
                    if signal.get("order_style") == "MARKET" and res.get("filled_cash"):
                        partial = f"\n💵 *Потрачено:* {res['filled_cash']}$"
                    msg = (
                        f"🚀 *Вход в позицию ({mode_label})*\n\n"
                        f"📌 *Рынок:* {signal['question']}\n"
                        f"⚡️ *Тип ордера:* рыночный {res.get('order_type', 'FOK')}\n"
                        f"📊 *Цена факт. исполнения:* {fill_cents}¢ "
                        f"(потолок {round(signal['worst_price'] * 100)}¢)\n"
                        f"📦 *Объём:* {filled_size} шт.{partial}\n"
                        f"💡 *Основание:* {signal['reason']}"
                    )
                    await context.bot.send_message(chat_id=cid, text=msg, parse_mode="Markdown")

        except Exception as e:
            log.error(f"Ошибка выполнения итерации в job_stations: {e}", exc_info=True)


# =========================================================
# ИСПОЛНЕНИЕ ОРДЕРОВ (рыночные FOK на вход / FAK на выход)
# =========================================================

def _best_price_from_book(token_id, side):
    """
    side="SELL" -> best bid (по нему мы выходим из лонга)
    side="BUY"  -> best ask
    Возвращает (price_0_1, book) или (None, None).
    """
    book = pt.get_order_book(token_id)
    if not book:
        return None, None
    if side.upper() == "SELL":
        levels = sort_bids(book.get("bids"))
    else:
        levels = sort_asks(book.get("asks"))
    if not levels:
        return None, book
    return levels[0]["price"], book


def execute_entry(signal: dict, demo_mode: bool) -> dict:
    """
    Вход в рынок настоящим рыночным ордером.
      order_style="MARKET"    -> MarketOrderArgs, amount в USDC, OrderType.FOK
      order_style="LIMIT_FOK" -> ровно N шар по цене не хуже worst_price, OrderType.FOK
    Возвращает success только при ПОДТВЕРЖДЁННОМ филле.
    """
    token_id = signal["token_id"]
    order_style = signal.get("order_style", "MARKET")
    order_type = signal.get("order_type", "FOK")
    worst_price = float(signal.get("worst_price") or signal.get("price") or 0)
    amount = float(signal.get("amount") or signal.get("size") or 0)

    if demo_mode:
        est_price = float(signal.get("expected_vwap_cents", signal.get("expected_fill_cents", 0))) / 100.0
        est_shares = float(signal.get("size") or 0)
        if order_style == "MARKET" and est_price > 0:
            est_shares = round(amount / est_price, 2)
        return {
            "success": True,
            "demo": True,
            "order_id": f"DEMO-{int(time.time())}",
            "order_type": order_type,
            "filled_size": est_shares,
            "filled_cash": round(est_shares * est_price, 2),
            "fill_cents": int(round(est_price * 100)),
        }

    if order_style == "MARKET":
        res = pt.place_market_order(token_id, "BUY", amount, worst_price, order_type)
    else:
        res = pt.place_order(token_id, "BUY", worst_price, amount, order_type)
        # для лимитного FOK факт филла тоже обязателен
        if res.get("success") and not res.get("filled"):
            res = {"error": f"{order_type} не исполнен: недостаточно ликвидности в пределах "
                            f"{round(worst_price * 100)}¢"}

    if res.get("error") or not res.get("success"):
        return {"success": False, "error": res.get("error", "unknown error")}

    filled_size = float(res.get("filled_size") or 0)
    fill_cents = res.get("avg_price_cents")
    if not fill_cents:
        fill_cents = signal.get("expected_fill_cents", 0)

    return {
        "success": True,
        "demo": False,
        "order_id": res.get("orderID", "unknown"),
        "order_type": res.get("order_type", order_type),
        "filled_size": round(filled_size, 2) if filled_size else float(signal.get("size") or 0),
        "filled_cash": res.get("filled_cash"),
        "fill_cents": int(round(float(fill_cents))),
    }


def execute_exit(pos: dict, worst_price: float, order_type: str = "FAK") -> dict:
    """
    Выход из позиции рыночным ордером.
    FAK: заберём столько, сколько есть в стакане по цене не хуже worst_price,
    остаток отменяется (в отличие от FOK не рискуем остаться в позиции целиком).
    """
    if pos.get("is_demo") == 1:
        return {
            "success": True,
            "demo": True,
            "filled_size": float(pos["size"]),
            "fill_cents": int(round(worst_price * 100)),
        }

    close_side = "SELL" if str(pos.get("side", "BUY")).upper() == "BUY" else "BUY"

    if close_side == "SELL":
        # amount в шарах
        res = pt.place_market_order(pos["token_id"], "SELL", float(pos["size"]), worst_price, order_type)
    else:
        # закрытие шорта — покупаем обратно, amount в долларах
        res = pt.place_market_order(
            pos["token_id"], "BUY", round(float(pos["size"]) * worst_price, 2), worst_price, order_type
        )

    if res.get("error") or not res.get("success"):
        return {"success": False, "error": res.get("error", "unknown error")}

    fill_cents = res.get("avg_price_cents") or round(worst_price * 100, 1)
    return {
        "success": True,
        "demo": False,
        "order_id": res.get("orderID", "unknown"),
        "filled_size": float(res.get("filled_size") or pos["size"]),
        "fill_cents": int(round(float(fill_cents))),
        "partial": float(res.get("filled_size") or 0) + 1e-9 < float(pos["size"]),
    }


async def _handle_missing_book(context, cid, pos):
    """
    Стакан по позиции недоступен. Пара сбоев подряд — норм, но если рынок закрыт,
    выключаем авто-SL/TP для этой позиции, чтобы не долбить API и не спамить логи.
    """
    key = f"pos_{pos['id']}_nobook"
    try:
        misses = int(get_setting(key, "0") or 0) + 1
    except (TypeError, ValueError):
        misses = 1
    set_setting(key, str(misses))

    if misses < 3:
        return

    info = pt.get_market_info(pos["token_id"])
    market_dead = bool(info and (info.get("closed") or not info.get("accepting_orders")))

    # Гасим авто-выход: позиция остаётся в трекере, но джоб её больше не трогает
    update_position_limits(pos["id"], sl=0, tp=0)
    set_setting(key, "0")

    reason = (
        "рынок закрыт или уже разрешён" if market_dead
        else "стакан недоступен (нет ордербука по токену)"
    )
    log.info(f"Авто-SL/TP отключён для позиции {pos['id']}: {reason}")

    await context.bot.send_message(
        chat_id=cid,
        text=(
            f"ℹ️ *Авто-SL/TP отключён для позиции*\n\n"
            f"📌 {pos.get('question', '')}\n"
            f"Причина: {reason}.\n"
            f"Позиция осталась в трекере — закройте её вручную или уберите из списка "
            f"кнопкой «🗑 Убрать из трекера»."
        ),
        parse_mode="Markdown"
    )


async def job_positions(context: ContextTypes.DEFAULT_TYPE):
    """
    Автоматический контроль открытых позиций: SL / TP по реальному стакану.
    Закрытие идёт рыночным FAK, чтобы не зависнуть отложником при обвале цены.
    """
    job = context.job
    cid = job.data.get("cid") if job.data else None
    if not cid:
        return

    try:
        positions = get_positions()
    except Exception as e:
        log.error(f"job_positions: не удалось прочитать позиции: {e}")
        return

    for pos in positions:
        try:
            sl = int(pos.get("sl") or 0)
            tp = int(pos.get("tp") or 0)
            if sl <= 0 and tp <= 0:
                continue

            side = str(pos.get("side", "BUY")).upper()
            exit_side = "SELL" if side == "BUY" else "BUY"
            best_price, _ = _best_price_from_book(pos["token_id"], exit_side)

            if best_price is None:
                # Стакана нет: рынок закрыт/разрешён либо временный сбой API.
                await _handle_missing_book(context, cid, pos)
                continue

            set_setting(f"pos_{pos['id']}_nobook", "0")

            cur_cents = round(best_price * 100, 1)

            hit = None
            if side == "BUY":
                if sl > 0 and cur_cents <= sl:
                    hit = ("SL", sl)
                elif tp > 0 and cur_cents >= tp:
                    hit = ("TP", tp)
            else:
                if sl > 0 and cur_cents >= sl:
                    hit = ("SL", sl)
                elif tp > 0 and cur_cents <= tp:
                    hit = ("TP", tp)

            if not hit:
                continue

            kind, level = hit

            # worst_price с запасом: SL важнее исполнить, чем выторговать тик
            slippage = float(get_setting("exit_slippage_cents", "2")) / 100.0
            if exit_side == "SELL":
                worst_price = max(0.001, best_price - slippage)
            else:
                worst_price = min(0.999, best_price + slippage)

            res = execute_exit(pos, worst_price, "FAK")

            if not res.get("success"):
                await context.bot.send_message(
                    chat_id=cid,
                    text=(
                        f"⚠️ *{kind} сработал, но выход не исполнился*\n"
                        f"📌 {pos.get('question', '')}\n"
                        f"Текущий bid: {cur_cents}¢ | Уровень: {level}¢\n"
                        f"`{res.get('error')}`"
                    ),
                    parse_mode="Markdown"
                )
                continue

            close_cents = res["fill_cents"]
            closed_size = res.get("filled_size", pos["size"])
            ep = float(pos["entry_price"])
            diff = (close_cents - ep) if side == "BUY" else (ep - close_cents)
            pnl = round(diff * float(closed_size) / 100.0, 2)

            add_trade_history(
                pos["is_demo"], pos["slug"], pos["question"], pos["outcome"],
                pos["side"], closed_size, ep, close_cents, pnl
            )

            partial_note = ""
            if res.get("partial"):
                remaining = round(float(pos["size"]) - float(closed_size), 2)
                partial_note = f"\n⚠️ Частичное исполнение, остаток {remaining} шт. закроется на следующей проверке."
                update_position_size(pos["id"], remaining)
            else:
                remove_position(pos["id"])

            icon = "🛑" if kind == "SL" else "🎯"
            mode_label = "🎮 ДЕМО" if pos["is_demo"] == 1 else "💰 РЕАЛ"
            await context.bot.send_message(
                chat_id=cid,
                text=(
                    f"{icon} *{kind} — позиция закрыта ({mode_label})*\n\n"
                    f"📌 {pos.get('question', '')}\n"
                    f"⚡️ Рыночный FAK по {close_cents}¢ (уровень {level}¢)\n"
                    f"📦 Объём: {closed_size} шт.\n"
                    f"💰 PnL: {'+' if pnl > 0 else ''}{pnl}$" + partial_note
                ),
                parse_mode="Markdown"
            )

        except Exception as e:
            log.error(f"job_positions: ошибка обработки позиции {pos.get('id')}: {e}", exc_info=True)


async def job_markets(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    cid = job.data.get("cid") if job.data else None
    if not cid:
        return

    notify_markets = get_setting("market_notifications", "1") == "1"
    try:
        active_markets = [m for m in get_markets() if m.get("enabled")]
        for m in active_markets:
            md = fetch_market(m["slug"])
            if not md or not md.get("options"):
                continue
            probs = {o.get("label", "?"): o.get("prob", 0) for o in md["options"]}
            old_probs = m.get("last_probs")
            if isinstance(old_probs, str):
                try:
                    old_probs = json.loads(old_probs)
                except:
                    old_probs = {}

            changed, lines = False, []
            if old_probs and isinstance(old_probs, dict):
                for k, current_v in probs.items():
                    old_v = old_probs.get(k)
                    try:
                        cur_f = float(current_v)
                        old_f = float(old_v) if old_v is not None else cur_f
                    except:
                        cur_f, old_f = 0.0, 0.0

                    if old_v is not None and cur_f != old_f:
                        changed, diff = True, cur_f - old_f
                        trend, diff_str = (
                            ("📈", f"+{round(diff, 1)}%") if diff > 0
                            else ("📉", f"{round(diff, 1)}%")
                        )
                        lines.append(f"• *{k}:* `{old_f}%` ➡️ `{cur_f}%` {trend} ({diff_str})")
                    else:
                        lines.append(f"• *{k}:* `{cur_f}%` ➖")

            add_market_history(m["id"], probs)
            update_market(m["id"], last_probs=probs)

            if notify_markets and changed and old_probs:
                msg = (
                    f"📊 *Движение на Polymarket!*\n"
                    f"🎰 *Рынок:* [{md.get('name', m['slug'])}](https://polymarket.com/event/{m['slug']})\n\n"
                    + "\n".join(lines)
                )
                await context.bot.send_message(
                    chat_id=cid, text=msg, parse_mode="Markdown", disable_web_page_preview=True
                )
    except Exception as e:
        log.error(f"Ошибка в job_markets: {e}")


def schedule_jobs(context, cid=None):
    jq = context.job_queue if hasattr(context, "job_queue") else context.application.job_queue
    current_cid = cid
    if current_cid is None:
        for name in ("st_wu_job", "st_metar_job", "st_cwx_job", "mk_job"):
            for j in jq.get_jobs_by_name(name):
                if getattr(j, "data", None) and j.data.get("cid"):
                    current_cid = j.data.get("cid")
                    break
            if current_cid is not None:
                break

    for nm in ("st_job", "st_wu_job", "st_metar_job", "st_cwx_job", "mk_job", "pos_job"):
        for j in jq.get_jobs_by_name(nm):
            j.schedule_removal()

    if not current_cid:
        return

    si_wu = int(get_setting("interval", "60"))
    si_metar = int(get_setting("metar_interval", "60"))
    si_cwx = int(get_setting("checkwx_interval", "30"))
    smi = int(get_setting("m_interval", "30"))

    jq.run_repeating(
        job_stations, interval=si_wu, first=1,
        name="st_wu_job",
        data={"cid": current_cid, "stype": "wunderground"},
        job_kwargs={"misfire_grace_time": 60}
    )
    jq.run_repeating(
        job_stations, interval=si_metar, first=2,
        name="st_metar_job",
        data={"cid": current_cid, "stype": "metar"},
        job_kwargs={"misfire_grace_time": 60}
    )
    jq.run_repeating(
        job_stations, interval=si_cwx, first=3,
        name="st_cwx_job",
        data={"cid": current_cid, "stype": "checkwx"},
        job_kwargs={"misfire_grace_time": 60}
    )
    jq.run_repeating(
        job_positions, interval=int(get_setting("pos_interval", "20")), first=7,
        name="pos_job",
        data={"cid": current_cid},
        job_kwargs={"misfire_grace_time": 30}
    )
    jq.run_repeating(
        job_markets, interval=smi, first=5,
        name="mk_job",
        data={"cid": current_cid},
        job_kwargs={"misfire_grace_time": 60}
    )