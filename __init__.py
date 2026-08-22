import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.state import us
from bot.handlers.common import send_internal_error

from bot.handlers import (
    menu,
    stations,
    markets,
    checks,
    trade,
    orders,
    settings as settings_h,
    poly_api,
    plots,
)

log = logging.getLogger("bot")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.callback_query

        try:
            await q.answer()
        except Exception as e:
            if "old" in str(e).lower() or "invalid" in str(e).lower():
                log.warning("Таймаут кнопки Telegram")
            else:
                log.warning(f"Ошибка answer_callback_query: {e}")

        d = q.data

        if d in ("noop", "close_inline", "back_main"):
            return await menu.handle(update, context)

        if d.startswith(("plots_", "chrs_", "chrm_")):
            return await plots.handle(update, context)

        if d.startswith("st_"):
            return await stations.handle(update, context)

        if d.startswith("mk_") or d in ("mk_add_st_metar", "mk_add_st_wu"):
            return await markets.handle(update, context)

        if d.startswith("chk_"):
            return await checks.handle(update, context)

        if d.startswith(("pos_", "trc_")) or d in ("tr_orders", "tr_stats", "tr_clear_stats"):
            return await orders.handle(update, context)

        if (
            d in ("tr_back", "tr_api_menu", "tr_toggle_demo", "toggle_demo_mode",
                  "tr_strategies", "sys_logs", "strat_active",
                  "set_order_timeout", "set_order_retries")
            or d.startswith(("trade_", "strat_", "stbind_"))
        ):
            return await poly_api.handle(update, context)

        if d in ("tr_buy", "tr_sell", "tr_confirm") or d.startswith(("trm_", "try_", "trn_")):
            return await trade.handle(update, context)

        if d.startswith(("su_", "si_", "sth_", "smi_", "smt_", "smet_", "ntg_")):
            return await settings_h.handle(update, context)

    except Exception as e:
        log.exception(f"on_callback error: {e}")
        await send_internal_error(update)