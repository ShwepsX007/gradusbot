from telegram import Update
from telegram.ext import ContextTypes

from state import us


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    s = us(q.message.chat_id)

    if d == "noop":
        return

    if d == "close_inline":
        return await q.delete_message()

    if d == "back_main":
        s["state"] = None
        return await q.edit_message_text("📋 Используйте кнопки нижнего меню 👇", parse_mode="Markdown")