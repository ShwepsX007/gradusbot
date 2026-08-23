import logging
from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
from telegram.ext import ContextTypes
import telegram.error
from database import (get_stations, get_station, update_station, delete_station, get_market_by_slug, add_market, add_binding, get_bindings, get_setting, set_setting, add_station)
from utils import format_temp, search_markets
from state import us, icon
from keyboards import st_kb, back

log = logging.getLogger("bot")

def _build_toggle_kb(stations):
    kb = [[Btn(f"{'✅' if st.get('enabled') else '❌'} {icon(st)} {st['name']}", callback_data=f"st_tog_{st['id']}")] for st in stations]
    kb.append(back("st_menu"))
    return KB(kb)

def _metar_int(key, default):
    try:
        v = get_setting(key, None)
        if v is None or str(v).strip() == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _metar_win_short(sid):
    m1 = _metar_int(f"st_{sid}_metar_m1", -1)
    m2 = _metar_int(f"st_{sid}_metar_m2", -1)
    if m1 < 0 and m2 < 0:
        return "общее окно"
    parts = [f":{m:02d}" for m in (m1, m2) if 0 <= m <= 59]
    return " и ".join(parts)


def _metar_win_text(sid):
    st = get_station(sid)
    name = st["name"] if st else f"#{sid}"
    m1 = _metar_int(f"st_{sid}_metar_m1", -1)
    m2 = _metar_int(f"st_{sid}_metar_m2", -1)
    lead = _metar_int(f"st_{sid}_metar_lead", 3)
    window = _metar_int(f"st_{sid}_metar_window", 8)
    fast = _metar_int(f"st_{sid}_metar_fast", _metar_int("metar_burst_interval", 10))
    slow = _metar_int("metar_interval", 60)

    lines = [f"✈️ *{name}*", ""]
    if m1 < 0 and m2 < 0:
        lines.append("Персональные окна не заданы — работает общее турбо-окно.")
    else:
        for i, m in enumerate((m1, m2), 1):
            if 0 <= m <= 59:
                lines.append(f"Окно {i}: с :{(m - lead) % 60:02d} до :{(m + window) % 60:02d} "
                             f"(выпуск :{m:02d})")
        lines.append("")
        lines.append(f"В окне: каждые *{fast}с* · вне окна: каждые *{slow}с*")
    return "\n".join(lines)


def _metar_win_kb(sid):
    m1 = _metar_int(f"st_{sid}_metar_m1", -1)
    m2 = _metar_int(f"st_{sid}_metar_m2", -1)
    lead = _metar_int(f"st_{sid}_metar_lead", 3)
    window = _metar_int(f"st_{sid}_metar_window", 8)
    fast = _metar_int(f"st_{sid}_metar_fast", _metar_int("metar_burst_interval", 10))

    def mm(v):
        return f":{v:02d}" if 0 <= v <= 59 else "—"

    kb = [
        [Btn(f"🎯 Выпуск 1: {mm(m1)}", callback_data=f"st_met_m1_{sid}"),
         Btn(f"🎯 Выпуск 2: {mm(m2)}", callback_data=f"st_met_m2_{sid}")],
        [Btn(f"+ После: {window}м", callback_data=f"st_met_o_{sid}"),
         Btn(f"- Заранее: {lead}м", callback_data=f"st_met_l_{sid}"),
         Btn(f"⚡ В окне: {fast}с", callback_data=f"st_met_f_{sid}")],
        [Btn("🚫 Убрать второе окно", callback_data=f"st_met_clr2_{sid}")],
        [Btn("♻️ Сбросить на общее окно", callback_data=f"st_met_off_{sid}")],
        [Btn("⬅️ Назад", callback_data="st_met_win")],
    ]
    return KB(kb)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    
    try: await q.answer()
    except: pass

    d = q.data
    s = us(q.message.chat_id)

    async def edit_text(text, **kwargs):
        try: return await q.edit_message_text(text, **kwargs)
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e): return q.message
            raise

    if d == "st_cwx_win":
        sts = [st for st in get_stations() if st.get("station_type") == "checkwx"]
        if not sts:
            return await edit_text("Нет CheckWX станций.", reply_markup=st_kb())
        kb = []
        for st in sts:
            minute = get_setting(f"st_{st['id']}_cwx_minute", get_setting("checkwx_default_minute", "0"))
            window = get_setting(f"st_{st['id']}_cwx_window", get_setting("checkwx_default_window", "10"))
            lead   = get_setting(f"st_{st['id']}_cwx_lead", get_setting("checkwx_default_lead", "5"))
            kb.append([Btn(f"⚡ {st['name']} :{minute} / +{window} / -{lead}", callback_data=f"st_cwx_w_{st['id']}")])
        kb.append(back("st_menu"))
        return await edit_text(
            "⚡ *Окно опроса CheckWX*\n"
            "Формат: `минута / +окно / -заранее`\n"
            "Например `:25 / +10 / -5` = опрашивать с :20 до :35.",
            parse_mode="Markdown",
            reply_markup=KB(kb)
        )

    if d.startswith("st_cwx_w_"):
        sid = int(d[len("st_cwx_w_"):])
        st = get_station(sid)
        if not st:
            return await edit_text("Станция не найдена.", reply_markup=st_kb())
        minute = get_setting(f"st_{sid}_cwx_minute", get_setting("checkwx_default_minute", "0"))
        window = get_setting(f"st_{sid}_cwx_window", get_setting("checkwx_default_window", "10"))
        lead   = get_setting(f"st_{sid}_cwx_lead", get_setting("checkwx_default_lead", "5"))
        kb = [
            [Btn(f"🎯 Минута: {minute}", callback_data=f"st_cwx_m_{sid}"),
             Btn(f"+ Окно: {window}", callback_data=f"st_cwx_o_{sid}"),
             Btn(f"- Заранее: {lead}", callback_data=f"st_cwx_l_{sid}")],
            [Btn("⬅️ Назад", callback_data="st_cwx_win")]
        ]
        return await edit_text(
            f"⚡ *{st['name']}*\n"
            f"Сейчас: с :{(int(minute)-int(lead))%60:02d} до :{(int(minute)+int(window))%60:02d}\n"
            f"Что менять?",
            parse_mode="Markdown",
            reply_markup=KB(kb)
        )

    if d.startswith("st_cwx_m_"):
        sid = int(d[len("st_cwx_m_"):])
        s["state"] = "wait_cwx_minute"
        s["cwx_edit_sid"] = sid
        return await edit_text("🎯 Введите минуту часа (0-59), когда обычно выходит METAR для этой станции:")

    if d.startswith("st_cwx_o_"):
        sid = int(d[len("st_cwx_o_"):])
        s["state"] = "wait_cwx_window"
        s["cwx_edit_sid"] = sid
        return await edit_text("➕ Введите ширину окна в минутах (1-30):")

    if d.startswith("st_cwx_l_"):
        sid = int(d[len("st_cwx_l_"):])
        s["state"] = "wait_cwx_lead"
        s["cwx_edit_sid"] = sid
        return await edit_text("➖ Введите за сколько минут до METAR начинать опрос (0-15):")

    # =====================================================
    # Персональные окна выпуска METAR (до двух в час)
    # =====================================================
    if d == "st_met_win":
        sts = [st for st in get_stations() if st.get("station_type") == "metar"]
        if not sts:
            return await edit_text("Нет METAR станций.", reply_markup=st_kb())
        kb = []
        for st in sts:
            kb.append([Btn(f"✈️ {st['name']} — {_metar_win_short(st['id'])}",
                           callback_data=f"st_met_w_{st['id']}")])
        kb.append(back("st_menu"))
        return await edit_text(
            "✈️ *Окна выпуска METAR*\n\n"
            "У каждого аэропорта своя минута выпуска сводки, и часто она приходит "
            "*дважды в час* — например Париж :25 и :55, а другая станция :08 и :38.\n\n"
            "Внутри окна бот опрашивает станцию часто, вне окна — по обычному интервалу. "
            "Если окна не заданы, работает общее турбо-окно из настроек.",
            parse_mode="Markdown",
            reply_markup=KB(kb)
        )

    if d.startswith("st_met_w_"):
        sid = int(d[len("st_met_w_"):])
        return await edit_text(_metar_win_text(sid), parse_mode="Markdown",
                               reply_markup=_metar_win_kb(sid))

    if d.startswith("st_met_off_"):
        sid = int(d[len("st_met_off_"):])
        for k in ("m1", "m2"):
            set_setting(f"st_{sid}_metar_{k}", "-1")
        return await edit_text(_metar_win_text(sid), parse_mode="Markdown",
                               reply_markup=_metar_win_kb(sid))

    if d.startswith("st_met_clr2_"):
        sid = int(d[len("st_met_clr2_"):])
        set_setting(f"st_{sid}_metar_m2", "-1")
        return await edit_text(_metar_win_text(sid), parse_mode="Markdown",
                               reply_markup=_metar_win_kb(sid))

    _MET_EDIT = {
        "st_met_m1_": ("wait_metar_m1",     "🎯 Введите *первую* минуту часа (0–59), когда выходит METAR этой станции:"),
        "st_met_m2_": ("wait_metar_m2",     "🎯 Введите *вторую* минуту часа (0–59). Данные часто приходят дважды в час:"),
        "st_met_o_":  ("wait_metar_window", "➕ Сколько минут после выпуска продолжать частый опрос (1–30)?"),
        "st_met_l_":  ("wait_metar_lead",   "➖ За сколько минут до выпуска начинать частый опрос (0–15)?"),
        "st_met_f_":  ("wait_metar_fast",   "⚡ Интервал опроса внутри окна в секундах (5–120):"),
    }
    for pref, (state, prompt) in _MET_EDIT.items():
        if d.startswith(pref):
            sid = int(d[len(pref):])
            s["state"] = state
            s["met_edit_sid"] = sid
            return await edit_text(prompt, parse_mode="Markdown")

    if d == "st_menu":
        return await edit_text("🌡 *Станции*", parse_mode="Markdown", reply_markup=st_kb())
    if d == "st_add":
        s["state"] = "wait_wu_url"
        return await edit_text("📡 Отправьте ссылку на станцию Weather Underground:", reply_markup=KB([back("st_menu")]))
    if d == "st_metar":
        s["state"] = "wait_metar_code"
        return await edit_text("✈️ Введите ICAO код станции (AWC):", reply_markup=KB([back("st_menu")]))
    if d == "st_cwx":
        s["state"] = "wait_cwx_code"
        return await edit_text("⚡ Введите ICAO код станции для быстрого API CheckWX:", reply_markup=KB([back("st_menu")]))
    if d == "st_list":
        sts = get_stations()
        if not sts: return await edit_text("📋 Список станций пуст.", reply_markup=st_kb())
        u = get_setting("units", "C")
        msg = "📋 *Список станций:*\n\n"
        for st in sts:
            status = "✅" if st.get("enabled") else "❌"
            if st.get("station_type") == "metar": stype = "✈️ METAR"
            elif st.get("station_type") == "checkwx": stype = "⚡ CheckWX"
            else: stype = "📡 WU"
            temp_str = format_temp(st["last_temp"], u) if st.get("last_temp") is not None else "нет данных"
            msg += f"{status} {stype} *{st['name']}*\n   🌡 {temp_str} | Код: `{st.get('station_code', '?')}`\n\n"
        if len(msg) > 4000: msg = msg[:4000] + "\n..."
        return await edit_text(msg, parse_mode="Markdown", reply_markup=st_kb())
    if d == "st_toggle":
        sts = get_stations()
        if not sts: return await edit_text("Нет станций для переключения.", reply_markup=st_kb())
        return await edit_text("🔄 Нажмите на станцию для вкл/выкл:", reply_markup=_build_toggle_kb(sts))
    if d.startswith("st_tog_"):
        sid = int(d[7:])
        st = get_station(sid)
        if st: update_station(sid, enabled=0 if st.get("enabled") else 1)
        sts = get_stations()
        return await edit_text("🔄 Нажмите на станцию для вкл/выкл:", reply_markup=_build_toggle_kb(sts))
    
    if d == "st_rename":
        sts = get_stations()
        if not sts: return await edit_text("Нет станций для переименования.", reply_markup=st_kb())
        kb = [[Btn(f"{icon(st)} {st['name']}", callback_data=f"st_ren_{st['id']}")] for st in sts]
        kb.append(back("st_menu"))
        return await edit_text("✏️ Выберите станцию для переименования:", reply_markup=KB(kb))
    
    if d.startswith("st_ren_"):
        sid = int(d[7:])
        st = get_station(sid)
        if st:
            s["state"] = "wait_st_rename_name"
            s["rename_st_id"] = sid
            return await edit_text(f"✏️ Введите новое имя для станции *{st['name']}*:", parse_mode="Markdown", reply_markup=KB([back("st_menu")]))
        return await edit_text("❌ Станция не найдена.", reply_markup=st_kb())

    if d == "st_delete":
        sts = get_stations()
        if not sts: return await edit_text("Нет станций для удаления.", reply_markup=st_kb())
        kb = [[Btn(f"🗑 {icon(st)} {st['name']}", callback_data=f"st_del_{st['id']}")] for st in sts]
        kb.append(back("st_menu"))
        return await edit_text("🗑 Выберите станцию для удаления:", reply_markup=KB(kb))

    if d.startswith("st_del_"):
        sid = int(d[7:])
        st = get_station(sid)
        if st:
            delete_station(sid)
            return await edit_text(f"✅ Станция *{st['name']}* удалена.", parse_mode="Markdown", reply_markup=st_kb())
        return await edit_text("❌ Станция не найдена.", reply_markup=st_kb())

    if d == "st_find":
        sts = get_stations()
        if not sts: return await edit_text("Нет станций. Сначала добавьте станцию.", reply_markup=st_kb())
        kb = [[Btn(f"{icon(st)} {st['name']}", callback_data=f"st_find_{st['id']}")] for st in sts]
        kb.append(back("st_menu"))
        return await edit_text("🔍 Выберите станцию для поиска рынков:", reply_markup=KB(kb))

    if d.startswith("st_find_"):
        sid = int(d[8:])
        st = get_station(sid)
        if not st: return await edit_text("❌ Станция не найдена.", reply_markup=st_kb())
        await edit_text(f"⏳ Ищу рынки для станции *{st['name']}*...", parse_mode="Markdown")
        
        sc = st.get("station_code", "")
        wu = st.get("wu_url", "")
        results = search_markets(sc, wu)
        
        if not results: return await edit_text(f"❌ Рынки для станции *{st['name']}* не найдены.", parse_mode="Markdown", reply_markup=st_kb())
        
        found = results[:10]
        s["found_markets"] = found
        s["found_market_station_id"] = sid
        
        kb = []
        for i, r in enumerate(found):
            title = r.get("title") or r.get("slug", "Без названия")
            kb.append([Btn(f"➕ {title[:50]}", callback_data=f"st_bind_add_{i}")])
        kb.append(back("st_menu"))
        
        return await edit_text(f"🔍 Найдено рынков для *{st['name']}*: {len(found)}\nНажмите для добавления:", parse_mode="Markdown", reply_markup=KB(kb))

    if d.startswith("st_bind_add_"):
        try: idx = int(d[len("st_bind_add_"):])
        except Exception as e: return await edit_text("❌ Ошибка индекса.", reply_markup=st_kb())
        
        found = s.get("found_markets") or []
        sid = s.get("found_market_station_id")
        if sid is None or idx < 0 or idx >= len(found): return await edit_text("❌ Данные поиска устарели. Повторите поиск.", reply_markup=st_kb())
        
        item = found[idx]
        slug = (item.get("slug") or "").strip()
        title = item.get("title") or slug
        if not slug: return await edit_text("❌ У рынка пустой slug.", reply_markup=st_kb())
        
        st = get_station(sid)
        if not st: return await edit_text("❌ Станция не найдена.", reply_markup=st_kb())
        
        existing = get_market_by_slug(slug)
        if not existing:
            try: add_market(title, slug)
            except Exception as e: return await edit_text(f"❌ Не удалось добавить рынок:\n`{e}`", parse_mode="Markdown", reply_markup=st_kb())
        
        existing_bindings = get_bindings(sid)
        already_bound = any(b.get("market_slug") == slug for b in existing_bindings)
        if not already_bound:
            try: add_binding(sid, slug, title)
            except Exception as e: return await edit_text(f"⚠️ Рынок добавлен, но не привязался:\n`{e}`", parse_mode="Markdown", reply_markup=st_kb())
            
        return await edit_text(f"✅ Рынок *{title}*\nдобавлен и привязан к станции *{st['name']}*", parse_mode="Markdown", reply_markup=st_kb())