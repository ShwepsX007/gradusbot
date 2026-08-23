from telegram import Update, ForceReply
from telegram.ext import ContextTypes
import telegram.error
from database import set_setting, get_setting
from keyboards import settings_kb, notif_kb
from jobs import schedule_jobs

def _burst_text():
    on = get_setting("metar_burst", "1") == "1"
    return (
        "⚡ *Турбо-окно опроса METAR*\n\n"
        f"Состояние: *{'включено' if on else 'выключено'}*\n"
        f"В окне: каждые *{get_setting('metar_burst_interval', '10')}с*\n"
        f"Вне окна: каждые *{get_setting('metar_interval', '60')}с*\n"
        f"Окно: с *:{get_setting('metar_burst_from', '45')}* по *:{get_setting('metar_burst_to', '10')}* минуту\n"
        f"Лимит к AWC: *{get_setting('awc_rate_per_min', '20')}* запросов/мин\n\n"
        "_METAR выпускается раз в час (обычно :50–:56) плюс внеплановые SPECI. "
        "Частый опрос имеет смысл только в этом окне._"
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except:
        pass
    d = q.data

    # === Ключи CheckWX (ВАЖНО: проверяем до общего smet_cwx_) ===
    if d == "smet_cwx_key":
        from state import us
        us(update.effective_chat.id)["state"] = "wait_cwx_api_key"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔑 *Введите ваш API ключ CheckWX:*\n(Скопируйте его из кабинета на checkwx.com)",
            parse_mode="Markdown"
        )
        return

    if d == "smet_cwx_keys":
        from state import us
        us(update.effective_chat.id)["state"] = "wait_cwx_api_keys"
        cur = get_setting("checkwx_api_keys", "")
        cur_show = cur if cur else "(пусто)"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "🔑 *Список ключей CheckWX*\n"
                "Введите все ключи через запятую.\n"
                "Бот будет перебирать их по кругу и переключаться при 429.\n\n"
                f"Текущее: `{cur_show}`"
            ),
            parse_mode="Markdown"
        )
        return

    # === Уведомления ===
    if d == "ntg_s":
        cur = get_setting("notifications", "1")
        set_setting("notifications", "0" if cur == "1" else "1")
        try:
            return await q.edit_message_text("🔔 *Уведомления*", parse_mode="Markdown", reply_markup=notif_kb())
        except telegram.error.BadRequest:
            return

    if d == "ntg_m":
        cur = get_setting("market_notifications", "1")
        set_setting("market_notifications", "0" if cur == "1" else "1")
        try:
            return await q.edit_message_text("🔔 *Уведомления*", parse_mode="Markdown", reply_markup=notif_kb())
        except telegram.error.BadRequest:
            return

    if d == "ntg_metar_always":
        cur = get_setting("metar_always_notify", "0")
        set_setting("metar_always_notify", "0" if cur == "1" else "1")
        try:
            return await q.edit_message_text("🔔 *Уведомления*", parse_mode="Markdown", reply_markup=notif_kb())
        except telegram.error.BadRequest:
            return

    # === Ручной ввод интервалов ===
    MANUAL = {
        "sman_wu":     ("interval",             "📡 Введите интервал опроса WU в секундах (10–3600):"),
        "sman_metar":  ("metar_interval",       "✈️ Введите интервал опроса METAR (AWC) в секундах (10–3600).\n"
                                                "_AWC рекомендует не чаще 1 запроса в минуту на поток; безопасный минимум — 30с._"),
        "sman_cwx":    ("checkwx_interval",     "⚡ Введите интервал опроса CheckWX в секундах (5–3600):"),
        "sman_mkt":    ("m_interval",           "📊 Введите интервал опроса рынков в секундах (5–3600):"),
        "sman_burst":  ("metar_burst_interval", "⚡ Введите интервал опроса ВНУТРИ турбо-окна в секундах (5–120).\n"
                                                "_Меньше 10с имеет смысл только для одной-двух станций._"),
    }
    if d in MANUAL:
        key, prompt = MANUAL[d]
        from state import us
        st = us(update.effective_chat.id)
        st["state"] = f"wait_interval_{key}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt, parse_mode="Markdown")
        return

    if d == "sman_window":
        from state import us
        us(update.effective_chat.id)["state"] = "wait_burst_window"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=("🕐 Введите окно турбо-опроса как *начало-конец* в минутах часа.\n"
                  "Например `45-10` — с 45-й минуты по 10-ю минуту следующего часа.\n"
                  "_METAR обычно выходит в :50–:56._"),
            parse_mode="Markdown"
        )
        return

    # === Турбо-окно METAR ===
    if d == "sburst_menu":
        from keyboards import burst_kb
        try:
            return await q.edit_message_text(_burst_text(), parse_mode="Markdown", reply_markup=burst_kb())
        except telegram.error.BadRequest:
            return

    if d == "sburst_toggle":
        cur = get_setting("metar_burst", "1")
        set_setting("metar_burst", "0" if cur == "1" else "1")
        schedule_jobs(context)
        from keyboards import burst_kb
        try:
            return await q.edit_message_text(_burst_text(), parse_mode="Markdown", reply_markup=burst_kb())
        except telegram.error.BadRequest:
            return

    if d.startswith("sburst_int_"):
        set_setting("metar_burst_interval", d[len("sburst_int_"):])
        schedule_jobs(context)
        from keyboards import burst_kb
        try:
            return await q.edit_message_text(_burst_text(), parse_mode="Markdown", reply_markup=burst_kb())
        except telegram.error.BadRequest:
            return

    if d.startswith("sburst_rate_"):
        set_setting("awc_rate_per_min", d[len("sburst_rate_"):])
        from keyboards import burst_kb
        try:
            return await q.edit_message_text(_burst_text(), parse_mode="Markdown", reply_markup=burst_kb())
        except telegram.error.BadRequest:
            return

    if d == "sburst_stats":
        from utils import awc_status
        a = awc_status()
        from keyboards import burst_kb
        txt = (
            "📊 *Диагностика AWC*\n\n"
            f"Запросов за последнюю минуту: *{a['used_last_min']}* из *{a['limit_per_min']}*\n"
            f"Отложено лимитером: *{a['rejected']}*\n"
            f"Станций в кэше: *{a['cached_stations']}*\n"
            f"Блокировка: *{('да, ещё ' + str(a['blocked']) + 'с') if a['blocked'] else 'нет'}*\n"
            f"Штрафов подряд: *{a['strikes']}*\n"
            f"Последняя ошибка: `{a['last_error'] or '—'}`\n\n"
            "_Хардлимит AWC — 100 запросов/мин на IP._"
        )
        try:
            return await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=burst_kb())
        except telegram.error.BadRequest:
            return

    # === Настройки бота ===
    if d.startswith("su_"):
        set_setting("units", d[3:])
    elif d.startswith("si_"):
        set_setting("interval", d[3:])
        schedule_jobs(context)
    elif d.startswith("smet_cwx_"):
        # сюда дойдёт только числовое значение интервала CheckWX
        val = d[len("smet_cwx_"):]
        if val.isdigit():
            set_setting("checkwx_interval", val)
            schedule_jobs(context)
    elif d.startswith("sth_"):
        set_setting("threshold", d[4:])
    elif d.startswith("smi_"):
        set_setting("m_interval", d[4:])
        schedule_jobs(context)
    elif d.startswith("smt_"):
        set_setting("m_threshold", d[4:])
    elif d.startswith("smet_pr_"):
        set_setting("metar_pred_window", d[8:])
    elif d == "smet_prman":
        from state import us
        us(update.effective_chat.id)["state"] = "wait_metar_pred_window"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✏️ *Введите размер окна прогноза METAR (в минутах):*",
            parse_mode="Markdown"
        )
        return
    elif d.startswith("smet_"):
        # интервал METAR (AWC)
        val = d[len("smet_"):]
        if val.isdigit():
            set_setting("metar_interval", val)
            schedule_jobs(context)

    # перерисовать меню настроек
    try:
        await q.edit_message_text("⚙️ *Настройки бота:*", parse_mode="Markdown", reply_markup=settings_kb())
    except telegram.error.BadRequest:
        pass

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.message or not update.message.text: return False
    setting_key = context.user_data.get("awaiting_setting")
    if not setting_key: return False
    text = update.message.text.strip()

    if setting_key == "metar_pred_window":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ Введите корректное число минут (больше 0).")
            return True
        set_setting("metar_pred_window", text)
        await update.message.reply_text(f"✅ Окно прогноза METAR установлено на: `{text}` минут.", parse_mode="Markdown")

    elif setting_key == "checkwx_api_key":
        set_setting("checkwx_api_key", text)
        await update.message.reply_text(f"✅ API ключ CheckWX успешно сохранен!", parse_mode="Markdown")

    context.user_data.pop("awaiting_setting", None)
    return True