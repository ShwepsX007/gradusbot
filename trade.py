import uuid
from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes

from database import get_markets, get_market, get_setting, add_position

from state import us
from keyboards import back


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    s = us(q.message.chat_id)

    if d in ("tr_buy", "tr_sell"):
        s["trade_side"] = "BUY" if d == "tr_buy" else "SELL"
        mks = get_markets()
        if not mks:
            return await q.edit_message_text("Нет рынков.", reply_markup=KB([back("tr_back")]))
        kb = [[Btn(f"📊 {m['name']}", callback_data=f"trm_{m['id']}")] for m in mks] + [back("tr_back")]
        return await q.edit_message_text(
            f"{'📈 Купить' if d == 'tr_buy' else '📉 Продать'} — выберите рынок:",
            reply_markup=KB(kb)
        )

    if d.startswith("trm_"):
        import polymarket_trading as pt
        mk = get_market(int(d[4:]))
        if not mk:
            return
        s["trade_slug"] = mk["slug"]
        s["trade_market_name"] = mk["name"]
        await q.edit_message_text(f"⏳ Загружаю *{mk['name']}*...", parse_mode="Markdown")
        info = pt.get_event_markets(mk["slug"])
        if not info or not info.get("markets"):
            return await q.edit_message_text("❌ Ошибка загрузки рынка.", reply_markup=KB([back("tr_back")]))

        s["trade_event"] = info
        active = [m for m in info["markets"] if m.get("active", True)]
        if not active:
            active = info["markets"]

        msg = f"💰 *{info['title']}*\nВыберите исход:\n\n"
        kb = []
        for i, m in enumerate(active[:20]):
            py = m["price_yes"]
            pn = m["price_no"]
            qs = m["question"][:40]
            msg += f"*{i+1}.* {qs}\n   ✅ Yes: {py}¢ | ❌ No: {pn}¢\n\n"
            kb.append([
                Btn(f"✅ {qs[:25]} YES {py}¢", callback_data=f"try_{i}"),
                Btn(f"❌ NO {pn}¢", callback_data=f"trn_{i}")
            ])

        kb.append(back("tr_back"))
        return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB(kb))

    if d.startswith("try_") or d.startswith("trn_"):
        idx = int(d[4:])
        active = [m for m in s.get("trade_event", {}).get("markets", []) if m.get("active", True)]
        if not active:
            active = s.get("trade_event", {}).get("markets", [])
        if idx >= len(active):
            return
        m = active[idx]
        is_yes = d.startswith("try_")
        s["trade_token_id"] = m["token_yes"] if is_yes else m["token_no"]
        s["trade_outcome"] = "YES" if is_yes else "NO"
        s["trade_question"] = m["question"]
        s["state"] = "wait_trade_price"
        return await q.edit_message_text(
            f"{'✅' if is_yes else '❌'} *{m['question']}* → {s['trade_outcome']}\n\n💲 Введите цену покупки/продажи (1-99 центов):",
            parse_mode="Markdown",
            reply_markup=KB([back("tr_back")])
        )

    if d == "tr_confirm":
        token_id = s.get("trade_token_id")
        side = s.get("trade_side", "BUY")
        price = s.get("trade_price", 0)
        size = s.get("trade_size", 0)
        question = s.get("trade_question", "?")
        outcome = s.get("trade_outcome", "?")
        price_cents = s.get("trade_price_cents", 0)
        slug = s.get("trade_slug", "")
        demo = get_setting("demo_mode", "0") == "1"

        if not token_id or not price or not size:
            return await q.edit_message_text("❌ Ошибка данных.", reply_markup=KB([back("tr_back")]))

        await q.edit_message_text("⏳ Размещаю ордер...")

        if demo:
            success = True
            order_id = f"DEMO-{uuid.uuid4().hex[:8]}"
        else:
            import polymarket_trading as pt
            res = pt.place_order(token_id, side, price, size)
            if isinstance(res, dict) and res.get("error"):
                return await q.edit_message_text(f"❌ Ошибка:\n`{res['error']}`", parse_mode="Markdown", reply_markup=KB([back("tr_back")]))
            order_id = res.get("orderID", "unknown") if isinstance(res, dict) else "unknown"
            success = res.get("success", False) if isinstance(res, dict) else True

        if success and slug:
            add_position(
                1 if demo else 0,
                slug, token_id, side, size,
                s.get("abs_sl", 0), s.get("abs_tp", 0),
                price_cents, question, outcome
            )

        for k in list(s.keys()):
            if k.startswith("trade_"):
                del s[k]

        msg = (
            f"✅ *Ордер размещён!* {'(🎮 ДЕМО)' if demo else ''}\n\n"
            f"📊 Рынок: {question}\n"
            f"🎯 Исход: {outcome}\n"
            f"💲 Цена входа: {price_cents}¢\n"
            f"📦 Объём: {size}\n"
            f"🆔 ID: `{str(order_id)[:12]}...`"
        )
        return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB([back("tr_back")]))