from telegram import Update, ForceReply
from telegram.ext import ContextTypes
import telegram.error
from database import set_setting, get_setting
from bot.keyboards import settings_kb, notif_kb
from bot.jobs import schedule_jobs

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
        from bot.state import us
        us(update.effective_chat.id)["state"] = "wait_cwx_api_key"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔑 *Введите ваш API ключ CheckWX:*\n(Скопируйте его из кабинета на checkwx.com)",
            parse_mode="Markdown"
        )
        return

    if d == "smet_cwx_keys":
        from bot.state import us
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
        from bot.state import us
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