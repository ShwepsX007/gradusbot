import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import TOKEN
from database import init_db

from bot.handlers.common import cmd_start
from bot.handlers.text import on_text
from bot.handlers import on_callback
from bot.handlers.settings import handle_text_input  # Импортируем обработчик ручного ввода настроек

import polymarket_trading as pt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

log = logging.getLogger("main")
MY_CHAT_ID = 1617274846


async def on_error(update, context):
    log.exception("Unhandled exception in telegram application", exc_info=context.error)


async def custom_text_handler(update, context):
    """
    Умный перехватчик текстовых сообщений.
    Сначала проверяет, ожидает ли бот числовое значение настройки.
    Если да — обрабатывает его. Если нет — передает управление стандартному on_text.
    """
    # 1. Проверяем, не вводит ли пользователь настройку (например, минуты для METAR)
    is_setting_input = await handle_text_input(update, context)
    if is_setting_input:
        return  # Настройка успешно перехвачена и обработана, останавливаем выполнение

    # 2. Если это обычный текст или текстовая кнопка меню, отправляем в ваш стандартный обработчик
    await on_text(update, context)


def main():
    init_db()

    if pt.init_trading():
        log.info("✅ Trading ready")
    else:
        log.warning("❌ Trading disabled")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    
    # Заменяем прямой вызов on_text на наш умный перехватчик custom_text_handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_text_handler))
    
    app.add_handler(CallbackQueryHandler(on_callback))

    # Подключаем наш умный планировщик из jobs.py
    from bot.jobs import schedule_jobs
    schedule_jobs(app, cid=MY_CHAT_ID)

    try:
        app.run_polling()
    except KeyboardInterrupt:
        log.info("🛑 Bot stopped by user")
    except Exception:
        log.exception("❌ Fatal error in polling loop")
        raise


if __name__ == "__main__":
    main()