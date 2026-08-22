from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes

from database import (
    get_markets, get_market, update_market, delete_market,
    get_stations, get_station, get_bindings, add_binding, delete_binding,
    add_station, get_market_by_slug,
)
from utils import (
    find_station_for_market,
    extract_code_from_wu, extract_api_url, extract_code_from_api,
)

from state import us, icon
from keyboards import mk_kb, back


def _build_toggle_kb(markets):
    kb = [[Btn(f"{'✅' if m.get('enabled') else '❌'} {m['name']}", callback_data=f"mk_tog_{m['id']}")] for m in markets]
    kb.append(back("mk_menu"))
    return KB(kb)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    s = us(q.message.chat_id)

    if d == "mk_menu":
        return await q.edit_message_text("📊 *Рынки*", parse_mode="Markdown", reply_markup=mk_kb())

    if d == "mk_add":
        s["state"] = "wait_mk_url"
        return await q.edit_message_text(
            "📊 Отправьте ссылку на рынок Polymarket (polymarket.com/event/...):",
            reply_markup=KB([back("mk_menu")])
        )

    if d == "mk_list":
        mks = get_markets()
        if not mks:
            return await q.edit_message_text("📋 Список рынков пуст.", reply_markup=mk_kb())
        msg = "📋 *Список рынков:*\n\n"
        for m in mks:
            status = "✅" if m.get("enabled") else "❌"
            probs = m.get("last_probs", {})
            prob_str = " | ".join([f"{k}: {v}%" for k, v in list(probs.items())[:2]]) if probs else "нет данных"
            msg += f"{status} 📊 *{m['name']}*\n   🔗 `{m['slug']}`\n   📈 {prob_str}\n\n"
        if len(msg) > 4000:
            msg = msg[:4000] + "\n..."
        return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=mk_kb())

    if d == "mk_toggle":
        mks = get_markets()
        if not mks:
            return await q.edit_message_text("Нет рынков для переключения.", reply_markup=mk_kb())
        return await q.edit_message_text("🔄 Нажмите на рынок для вкл/выкл:", reply_markup=_build_toggle_kb(mks))

    if d.startswith("mk_tog_"):
        mid = int(d[7:])
        m = get_market(mid)
        if m:
            update_market(mid, enabled=0 if m.get("enabled") else 1)
        mks = get_markets()
        return await q.edit_message_text("🔄 Нажмите на рынок для вкл/выкл:", reply_markup=_build_toggle_kb(mks))

    if d == "mk_rename":
        mks = get_markets()
        if not mks:
            return await q.edit_message_text("Нет рынков для переименования.", reply_markup=mk_kb())
        kb = [[Btn(f"📊 {m['name']}", callback_data=f"mk_ren_{m['id']}")] for m in mks]
        kb.append(back("mk_menu"))
        return await q.edit_message_text("✏️ Выберите рынок для переименования:", reply_markup=KB(kb))

    if d.startswith("mk_ren_"):
        mid = int(d[7:])
        m = get_market(mid)
        if m:
            s["state"] = "wait_mk_rename_name"
            s["rename_mk_id"] = mid
            return await q.edit_message_text(
                f"✏️ Введите новое имя для рынка *{m['name']}*:",
                parse_mode="Markdown", reply_markup=KB([back("mk_menu")])
            )
        return await q.edit_message_text("❌ Рынок не найден.", reply_markup=mk_kb())

    if d == "mk_delete":
        mks = get_markets()
        if not mks:
            return await q.edit_message_text("Нет рынков для удаления.", reply_markup=mk_kb())
        kb = [[Btn(f"🗑 {m['name']}", callback_data=f"mk_del_{m['id']}")] for m in mks]
        kb.append(back("mk_menu"))
        return await q.edit_message_text("🗑 Выберите рынок для удаления:", reply_markup=KB(kb))

    if d.startswith("mk_del_"):
        mid = int(d[7:])
        m = get_market(mid)
        if m:
            delete_market(mid, m["slug"])
            return await q.edit_message_text(f"✅ Рынок *{m['name']}* удалён.", parse_mode="Markdown", reply_markup=mk_kb())
        return await q.edit_message_text("❌ Рынок не найден.", reply_markup=mk_kb())

    if d == "mk_bind":
        sts = get_stations()
        mks = get_markets()
        if not sts:
            return await q.edit_message_text("Сначала добавьте станцию.", reply_markup=mk_kb())
        if not mks:
            return await q.edit_message_text("Сначала добавьте рынок.", reply_markup=mk_kb())
        kb = [[Btn(f"{icon(st)} {st['name']}", callback_data=f"mk_bind_st_{st['id']}")] for st in sts]
        kb.append(back("mk_menu"))
        return await q.edit_message_text("🔗 Выберите станцию для привязки:", reply_markup=KB(kb))

    if d.startswith("mk_bind_st_"):
        sid = int(d[11:])
        st = get_station(sid)
        if not st:
            return await q.edit_message_text("❌ Станция не найдена.", reply_markup=mk_kb())
        s["bind_station_id"] = sid
        mks = get_markets()
        if not mks:
            return await q.edit_message_text("Нет рынков.", reply_markup=mk_kb())
        kb = [[Btn(f"📊 {m['name']}", callback_data=f"mk_bind_mk_{m['id']}")] for m in mks]
        kb.append(back("mk_menu"))
        return await q.edit_message_text(
            f"🔗 Выберите рынок для привязки к *{st['name']}*:",
            parse_mode="Markdown", reply_markup=KB(kb)
        )

    if d.startswith("mk_bind_mk_"):
        mid = int(d[11:])
        sid = s.get("bind_station_id")
        if not sid:
            return await q.edit_message_text("❌ Ошибка сессии. Начните заново.", reply_markup=mk_kb())
        m = get_market(mid)
        st = get_station(sid)
        if m and st:
            already_bound = any(b["market_slug"] == m["slug"] for b in get_bindings(sid))
            if not already_bound:
                add_binding(sid, m["slug"], m["name"])
            return await q.edit_message_text(
                f"✅ Рынок *{m['name']}* привязан к станции *{st['name']}*",
                parse_mode="Markdown", reply_markup=mk_kb()
            )
        return await q.edit_message_text("❌ Ошибка.", reply_markup=mk_kb())

    if d == "mk_unbind":
        sts = get_stations()
        if not sts:
            return await q.edit_message_text("Нет станций.", reply_markup=mk_kb())
        kb = [[Btn(f"{icon(st)} {st['name']}", callback_data=f"mk_unbind_st_{st['id']}")] for st in sts]
        kb.append(back("mk_menu"))
        return await q.edit_message_text("🔓 Выберите станцию для отвязки рынка:", reply_markup=KB(kb))

    if d.startswith("mk_unbind_st_"):
        sid = int(d[13:])
        st = get_station(sid)
        bindings = get_bindings(sid)
        if not bindings:
            return await q.edit_message_text(
                f"Нет привязок для станции *{st['name'] if st else sid}*",
                parse_mode="Markdown", reply_markup=mk_kb()
            )
        kb = [[Btn(f"🔓 {b['market_name']}", callback_data=f"mk_unbind_b_{b['id']}")] for b in bindings]
        kb.append(back("mk_menu"))
        return await q.edit_message_text("🔓 Выберите привязку для удаления:", reply_markup=KB(kb))

    if d.startswith("mk_unbind_b_"):
        bid = int(d[12:])
        delete_binding(bid)
        return await q.edit_message_text("✅ Привязка удалена.", reply_markup=mk_kb())

    # =========================================================
    # 🔍 Найти станцию по рынку
    # =========================================================
    if d == "mk_find":
        mks = get_markets()
        if not mks:
            return await q.edit_message_text(
                "Нет рынков. Сначала добавьте рынок.", reply_markup=mk_kb()
            )
        kb = [[Btn(f"📊 {m['name']}", callback_data=f"mk_find_{m['id']}")] for m in mks]
        kb.append(back("mk_menu"))
        return await q.edit_message_text(
            "🔍 Выберите рынок для поиска станции:", reply_markup=KB(kb)
        )

    if d.startswith("mk_find_"):
        mid = int(d[8:])
        m = get_market(mid)
        if not m:
            return await q.edit_message_text("❌ Рынок не найден.", reply_markup=mk_kb())

        await q.edit_message_text(f"⏳ Ищу станцию для рынка *{m['name']}*...", parse_mode="Markdown")
        result = find_station_for_market(m["slug"])

        icao = result.get("icao", "")
        wu_url = result.get("wu_url", "")

        msg = f"🔍 *Результат для {m['name']}:*\n\n"
        kb = []

        if icao:
            msg += f"✈️ ICAO: `{icao}`\n"
            if result.get("metar_ok"):
                msg += f"🌡 METAR температура: `{result['metar_temp']}°C`\n"
            else:
                msg += "⚠️ METAR недоступен\n"
            s["found_station_icao"] = icao
            s["found_station_market_id"] = mid
            kb.append([Btn(f"➕ Добавить как METAR ({icao})", callback_data="mk_add_st_metar")])

        if wu_url:
            msg += f"📡 WU URL: `{wu_url[:60]}`\n"
            s["found_station_wu_url"] = wu_url
            s["found_station_market_id"] = mid
            kb.append([Btn("➕ Добавить как WU станцию", callback_data="mk_add_st_wu")])

        if not icao and not wu_url:
            msg += "❌ Станция не найдена автоматически.\n"

        kb.append(back("mk_menu"))
        return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=KB(kb))

    if d == "mk_add_st_metar":
        icao = s.get("found_station_icao", "")
        mid = s.get("found_station_market_id")
        if not icao:
            return await q.edit_message_text("❌ Нет данных. Повторите поиск.", reply_markup=mk_kb())

        name = f"{icao} (METAR)"
        add_station(name, "", "", icao, "metar")

        new_st_id = None
        for st in get_stations():
            if st.get("station_code", "") == icao and st.get("station_type") == "metar":
                new_st_id = st["id"]

        mk = get_market(mid) if mid else None
        if new_st_id and mk:
            already = any(b["market_slug"] == mk["slug"] for b in get_bindings(new_st_id))
            if not already:
                add_binding(new_st_id, mk["slug"], mk["name"])

        return await q.edit_message_text(
            f"✅ METAR станция *{icao}* добавлена и привязана",
            parse_mode="Markdown", reply_markup=mk_kb()
        )

    if d == "mk_add_st_wu":
        wu_url = s.get("found_station_wu_url", "")
        mid = s.get("found_station_market_id")
        if not wu_url:
            return await q.edit_message_text("❌ Нет данных. Повторите поиск.", reply_markup=mk_kb())

        sc = extract_code_from_wu(wu_url)
        api = extract_api_url(wu_url)
        code = extract_code_from_api(api) or sc or ""
        name = f"{code or 'WU'} (WU)"
        add_station(name, api or "", wu_url, code, "wunderground")

        new_st_id = None
        for st in get_stations():
            if st.get("wu_url", "") == wu_url:
                new_st_id = st["id"]

        mk = get_market(mid) if mid else None
        if new_st_id and mk:
            already = any(b["market_slug"] == mk["slug"] for b in get_bindings(new_st_id))
            if not already:
                add_binding(new_st_id, mk["slug"], mk["name"])

        return await q.edit_message_text(
            f"✅ WU станция добавлена и привязана",
            parse_mode="Markdown", reply_markup=mk_kb()
        )