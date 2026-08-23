import atexit
import fcntl
import logging
import os
import sys

from telegram.error import Conflict, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import TOKEN
from database import init_db

from common import cmd_start
from text import on_text
from handlers import on_callback
from settings import handle_text_input  # Импортируем обработчик ручного ввода настроек

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

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.lock")
_lock_file = None


def acquire_single_instance_lock():
    """
    Не даём запустить второй экземпляр бота: иначе Telegram отдаёт
    Conflict: terminated by other getUpdates request.
    """
    global _lock_file
    _lock_file = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        other = ""
        try:
            _lock_file.seek(0)
            other = _lock_file.read().strip()
        except Exception:
            pass
        log.error(
            "❌ Бот уже запущен%s. Второй экземпляр остановлен, чтобы не ловить "
            "Conflict от Telegram. Проверьте: systemctl status <юнит> и ps aux | grep main.py",
            f" (PID {other})" if other else ""
        )
        sys.exit(1)

    _lock_file.seek(0)
    _lock_file.truncate()
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()
    atexit.register(_release_lock)


def _release_lock():
    global _lock_file
    if _lock_file is None:
        return
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()
    except Exception:
        pass
    try:
        os.unlink(LOCK_PATH)
    except Exception:
        pass
    _lock_file = None


async def on_error(update, context):
    err = context.error

    if isinstance(err, Conflict):
        # Где-то поднят второй экземпляр бота — стек трейс тут бесполезен
        log.error(
            "❌ Conflict: getUpdates перехвачен другим экземпляром бота. "
            "Оставьте только один процесс (systemctl stop / pkill -f main.py)."
        )
        return

    if isinstance(err, NetworkError):
        log.warning(f"Сетевая ошибка Telegram: {err}")
        return

    log.exception("Unhandled exception in telegram application", exc_info=err)


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
    acquire_single_instance_lock()
    init_db()

    try:
        import poly_unified as _pu
        if not _pu.python_ok():
            log.warning(
                f"⚠️ Python {_pu.python_version()}: официальный SDK Polymarket "
                f"(polymarket-client) требует 3.11+. Ордера с Deposit Wallet работать не будут. "
                f"Поднимите окружение: bash setup_python311.sh"
            )
        elif not _pu.available():
            log.warning("⚠️ polymarket-client не установлен — Deposit Wallet недоступен")
    except Exception as e:
        log.warning(f"unified backend probe: {e}")

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
    from jobs import schedule_jobs
    schedule_jobs(app, cid=MY_CHAT_ID)

    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        log.info("🛑 Bot stopped by user")
    except Exception:
        log.exception("❌ Fatal error in polling loop")
        raise


if __name__ == "__main__":
    main()