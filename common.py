import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import REPLY_KB

log = logging.getLogger("bot")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Бот готов! Выберите действие в меню ниже 👇", reply_markup=REPLY_KB)


async def send_internal_error(update: Update, text="❌ Внутренняя ошибка. Смотрите логи сервера."):
    try:
        if update.callback_query and update.callback_query.message:
            return await update.callback_query.message.reply_text(text)
    except:
        pass
    try:
        if update.message:
            return await update.message.reply_text(text)
    except:
        pass