from datetime import datetime
from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes

from database import (
    get_positions, get_position_by_id, remove_position,
    update_position_limits, add_trade_history,
    get_trade_statistics, clear_trade_statistics,
    get_setting
)

from state import us
from keyboards import back


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    s = us(q.message.chat_id)

    if d == "tr_orders":
        import polymarket_trading as pt
        demo = get_setting("demo_mode", "0") == "1"
        await q.edit_message_text("⏳ Загружаю портфель...")

        positions = [p for p in get_positions() if p["is_demo"] == (1 if demo else 0)]
        open_orders = [] if demo else pt.get_open_orders()

        if not positions and not open_orders:
            return await q.edit_message_text(
                f"📋 В режиме {'ДЕМО' if demo else 'РЕАЛ'} нет открытых позиций.",
                reply_markup=KB([back("tr_back")])
            )

        msg = f"💼 *ПОРТФЕЛЬ ({'🎮 ДЕМО' if demo else '💰 РЕАЛ'}):*\n\n"
        kb = []
        s["cancel_map"] = {}

        for p in positions[:8]:
            info = pt.get_event_markets(p["slug"])
            curr_price = 0
            if info:
                for m in info.get("markets", []):
                    if m["token_yes"] == p["token_id"]:
                        curr_price = m["price_yes"]
                    elif m["token_no"] == p["token_id"]:
                        curr_price = m["price_no"]

            ep = int(p["entry_price"])
            diff = (curr_price - ep) if p["side"] == "BUY" else (ep - curr_price)
            pnl = round(diff * p["size"] / 100, 2)

            msg += f"📦 *{p['question']}*\n"
            msg += f"🔹 Действие: *{p['side']} ({p['outcome']})* | Объём: *{p['size']}*\n"
            msg += f"🔹 Вход: *{ep}¢* | Текущая: *{curr_price}¢*\n"
            msg += f"🔹 SL: *{p['sl']}¢* | TP: *{p['tp']}¢*\n"
            msg += f"🔹 PnL: {'📈+' if pnl > 0 else '📉'}{pnl}$\n───────────────────\n"

            kb.append([Btn(f"🛑 Продать сейчас ({curr_price}¢)", callback_data=f"pos_close_{p['id']}_{curr_price}")])
            kb.append([Btn("📉 SL = 0", callback_data=f"pos_sl0_{p['id']}"), Btn("📈 TP = 100", callback_data=f"pos_tp100_{p['id']}")])
            kb.append([Btn("🗑 Убрать из трекера", callback_data=f"pos_forget_{p['id']}")])

        if open_orders:
            msg += "\n📦 *ОТКРЫТЫЕ ЛИМИТНЫЕ ОРДЕРА:*\n"
            for i, o in enumerate(open_orders[:5]):
                oid = o.get("id", "?")
                s["cancel_map"][str(i)] = oid
                msg += f"🔸 `{oid[:8]}...` {o.get('side')} {o.get('original_size')} шт.\n"
                kb.append([Btn(f"❌ Отменить ордер {oid[:8]}", callback_data=f"trc_{i}")])

        kb.append(back("tr_back"))
        if len(msg) > 4000:
            msg = msg[:4000] + "\n..."
        return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB(kb))

    if d.startswith("pos_forget_"):
        pid = int(d.split("_")[2])
        remove_position(pid)
        return await q.edit_message_text("✅ Позиция стерта из памяти бота (ордер на биржу не отправлялся).", reply_markup=KB([back("tr_orders")]))

    if d.startswith("pos_sl0_"):
        pid = int(d.split("_")[2])
        pos = get_position_by_id(pid)
        if pos:
            update_position_limits(pid, sl=0, tp=pos["tp"])
        return await q.answer("📉 Стоп-лосс установлен на 0¢!", show_alert=True)

    if d.startswith("pos_tp100_"):
        pid = int(d.split("_")[2])
        pos = get_position_by_id(pid)
        if pos:
            update_position_limits(pid, sl=pos["sl"], tp=100)
        return await q.answer("📈 Тейк-профит установлен на 100¢!", show_alert=True)

    if d.startswith("pos_close_"):
        parts = d.split("_")
        pid = int(parts[2])
        close_price = int(parts[3])

        pos = get_position_by_id(pid)
        if not pos:
            return await q.edit_message_text("❌ Позиция не найдена.", reply_markup=KB([back("tr_orders")]))

        await q.edit_message_text("⏳ Закрываю позицию рыночным FAK...")

        from jobs import execute_exit, _best_price_from_book
        from database import get_setting as _get_setting, update_position_size

        side = str(pos.get("side", "BUY")).upper()
        exit_side = "SELL" if side == "BUY" else "BUY"

        # Отталкиваемся от живого стакана, а не от котировки Gamma
        best_price, _ = _best_price_from_book(pos["token_id"], exit_side) if pos["is_demo"] != 1 else (None, None)
        if best_price is None:
            best_price = close_price / 100.0

        try:
            slippage = float(_get_setting("exit_slippage_cents", "2")) / 100.0
        except (TypeError, ValueError):
            slippage = 0.02

        if exit_side == "SELL":
            worst_price = max(0.001, best_price - slippage)
        else:
            worst_price = min(0.999, best_price + slippage)

        res = execute_exit(pos, worst_price, "FAK")

        if not res.get("success"):
            return await q.edit_message_text(
                f"❌ Выход не исполнился (FAK):\n`{res.get('error')}`",
                parse_mode="Markdown", reply_markup=KB([back("tr_orders")])
            )

        close_cents = res["fill_cents"]
        closed_size = res.get("filled_size", pos["size"])
        ep = pos["entry_price"]
        diff = (close_cents - ep) if side == "BUY" else (ep - close_cents)
        pnl = round(diff * float(closed_size) / 100.0, 2)

        add_trade_history(
            pos["is_demo"], pos["slug"], pos["question"], pos["outcome"],
            pos["side"], closed_size, ep, close_cents, pnl
        )

        note = ""
        if res.get("partial"):
            remaining = round(float(pos["size"]) - float(closed_size), 2)
            update_position_size(pid, remaining)
            note = f"\n⚠️ Исполнено частично, в позиции осталось {remaining} шт."
        else:
            remove_position(pid)

        return await q.edit_message_text(
            f"✅ *Позиция закрыта рыночным FAK*\n\n"
            f"⚡️ Цена исполнения: {close_cents}¢\n"
            f"📦 Объём: {closed_size} шт.\n"
            f"💰 PnL: {'+' if pnl > 0 else ''}{pnl}$" + note,
            parse_mode="Markdown", reply_markup=KB([back("tr_orders")])
        )

    if d.startswith("trc_"):
        import polymarket_trading as pt
        oid = s.get("cancel_map", {}).get(d[4:])
        if not oid:
            return await q.edit_message_text("❌ Ордер не найден.", reply_markup=KB([back("tr_orders")]))

        await q.edit_message_text(f"⏳ Отменяю ордер `{oid}`...", parse_mode="Markdown")
        res = pt.cancel_order(oid)

        if isinstance(res, dict) and res.get("error"):
            return await q.edit_message_text(
                f"❌ Ошибка отмены ордера\n\nID: `{oid}`\nОшибка: `{res.get('error')}`",
                parse_mode="Markdown", reply_markup=KB([back("tr_orders")])
            )

        return await q.edit_message_text(
            f"✅ Ордер отменён\n\nID: `{oid}`",
            parse_mode="Markdown", reply_markup=KB([back("tr_orders")])
        )

    if d == "tr_stats":
        demo = get_setting("demo_mode", "0") == "1"
        stats = get_trade_statistics(1 if demo else 0)
        positions = [p for p in get_positions() if p["is_demo"] == (1 if demo else 0)]

        total_trades = len(stats)
        wins = sum(1 for t in stats if t["pnl"] > 0)
        total_pnl = sum(t["pnl"] for t in stats)

        msg = f"📊 *СТАТИСТИКА ({'🎮 ДЕМО' if demo else '💰 РЕАЛ'})*\n\n"
        msg += f"🔹 Открытых позиций: *{len(positions)}*\n"
        msg += f"🔹 Закрытых сделок: *{total_trades}*\n"

        if total_trades > 0:
            msg += f"🔹 Успешных: *{wins}*\n"
            msg += f"💰 Общий PnL: *{'+' if total_pnl > 0 else ''}{round(total_pnl, 2)}$*\n\n"
            msg += "*Последние 5 закрытых сделок:*\n"
            for t in stats[:5]:
                dt = datetime.fromtimestamp(t["timestamp"]).strftime("%d.%m %H:%M")
                msg += f"▫️ `{dt}` | {t['outcome']} ({t['side']})\n   {t['question'][:30]}...\n   PnL: {'+' if t['pnl'] > 0 else ''}{t['pnl']}$\n"
        else:
            msg += "\n_Закрытых сделок пока нет._\n"

        kb = [[Btn("🗑 Сбросить статистику", callback_data="tr_clear_stats")], back("tr_back")]
        return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB(kb))

    if d == "tr_clear_stats":
        demo = get_setting("demo_mode", "0") == "1"
        clear_trade_statistics(1 if demo else 0)
        return await q.edit_message_text("✅ Статистика очищена.", reply_markup=KB([back("tr_back")]))