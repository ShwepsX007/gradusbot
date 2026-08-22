from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB, ReplyKeyboardMarkup
from database import get_setting

REPLY_KB = ReplyKeyboardMarkup([
    ["💰 Торговля", "🔍 Проверить"],
    ["🌡 Станции", "📊 Рынки", "📈 Графики"],
    ["⚙️ Настройки", "🔔 Уведомления"]
], resize_keyboard=True)


def back(cb):
    return [Btn("⬅️ Отмена / Назад", callback_data=cb)]


def st_kb():
    return KB([
        [Btn("📡 ➕ WU", callback_data="st_add"),
         Btn("✈️ ➕ METAR", callback_data="st_metar"),
         Btn("⚡ ➕ CheckWX", callback_data="st_cwx")],
        [Btn("📋 Список", callback_data="st_list"),
         Btn("🔍 Найти рынки", callback_data="st_find")],
        [Btn("🔄 Вкл/Выкл", callback_data="st_toggle"),
         Btn("✏️ Переименовать", callback_data="st_rename")],
        [Btn("🗑 Удалить", callback_data="st_delete")],
        [Btn("⚡ Окно CheckWX", callback_data="st_cwx_win")],
        back("back_main")
    ])


def mk_kb():
    return KB([
        [Btn("➕ Добавить", callback_data="mk_add"),
         Btn("📋 Список", callback_data="mk_list")],
        [Btn("🔗 Привязать", callback_data="mk_bind"),
         Btn("🔓 Отвязать", callback_data="mk_unbind")],
        [Btn("🔄 Вкл/Выкл", callback_data="mk_toggle"),
         Btn("✏️ Переименовать", callback_data="mk_rename")],
        [Btn("🔍 Найти станцию", callback_data="mk_find"),
         Btn("🗑 Удалить", callback_data="mk_delete")],
        back("back_main")
    ])


def chk_kb():
    return KB([
        [Btn("📡 Пров. WU", callback_data="chk_wu"),
         Btn("✈️ Пров. METAR", callback_data="chk_metar")],
        [Btn("⚡ Пров. CheckWX", callback_data="chk_cwx"),
         Btn("📊 Пров. рынки", callback_data="chk_market")],
        [Btn("🔮 Проверить прогноз METAR (WU)", callback_data="chk_metar_pred")],
        back("back_main")
    ])


def trade_kb():
    demo = get_setting("demo_mode", "0") == "1"
    mode_text = "🎮 Включить РЕАЛ" if demo else "💰 Включить ДЕМО"
    return KB([
        [Btn("📈 Купить", callback_data="tr_buy"),
         Btn("📉 Продать", callback_data="tr_sell")],
        [Btn("💼 Портфель и Ордера", callback_data="tr_orders"),
         Btn("📊 Стратегии", callback_data="tr_strategies")],
        [Btn("🔑 Настройки API", callback_data="tr_api_menu"),
         Btn("📜 Логи системы", callback_data="sys_logs")],
        [Btn(mode_text, callback_data="toggle_demo_mode")],
        back("back_main")
    ])


def api_settings_kb():
    timeout = get_setting("order_timeout", "20")
    retries = get_setting("order_retries", "3")
    return KB([
        [Btn("📝 Проверить API ключи", callback_data="chk_api")],
        [Btn(f"⏱ Таймаут ордера: {timeout}с", callback_data="set_order_timeout"),
         Btn(f"🔁 Попыток входа: {retries}", callback_data="set_order_retries")],
        back("tr_back")
    ])


def notif_kb():
    sn = get_setting("notifications", "1") == "1"
    mn = get_setting("market_notifications", "1") == "1"
    always = get_setting("metar_always_notify", "0") == "1"
    return KB([
        [Btn(f"🌡 Станции: {'🔔 ВКЛ' if sn else '🔕 ВЫКЛ'}", callback_data="ntg_s")],
        [Btn(f"📊 Рынки: {'🔔 ВКЛ' if mn else '🔕 ВЫКЛ'}", callback_data="ntg_m")],
        [Btn(f"✈️ METAR/CheckWX всегда: {'🔔 ВКЛ' if always else '🔕 ВЫКЛ'}",
             callback_data="ntg_metar_always")],
        back("back_main")
    ])


def settings_kb():
    u = get_setting("units", "C")
    th = get_setting("threshold", "0.5")
    si = get_setting("interval", "60")
    mi = get_setting("m_interval", "30")
    smet = get_setting("metar_interval", "60")
    scwx = get_setting("checkwx_interval", "30")
    m_th = get_setting("m_threshold", "1.0")
    pred_win = get_setting("metar_pred_window", "10")
    has_key = "✅ Установлен" if get_setting("checkwx_api_key", "") else "❌ Отсутствует"

    def m(v, c):
        return f"»{v}«" if str(v) == str(c) else str(v)

    return KB([
        [Btn("— Единицы —", callback_data="noop")],
        [Btn(m("C", u), callback_data="su_C"),
         Btn(m("F", u), callback_data="su_F"),
         Btn(m("C+F", u), callback_data="su_B")],

        [Btn(f"🔑 API Ключ CheckWX: {has_key}", callback_data="smet_cwx_key"),
         Btn("➕ Доп. ключи", callback_data="smet_cwx_keys")],

        [Btn("— Интервал CheckWX ⚡ —", callback_data="noop")],
        [Btn(m(f"{v}с", f"{scwx}с"), callback_data=f"smet_cwx_{v}") for v in (10, 30, 60)],

        [Btn("— Интервал METAR (AWC) ✈️ —", callback_data="noop")],
        [Btn(m(f"{v}с", f"{smet}с"), callback_data=f"smet_{v}") for v in (30, 60, 120)],

        [Btn("— Интервал WU 📡 —", callback_data="noop")],
        [Btn(m(f"{v}с", f"{si}с"), callback_data=f"si_{v}") for v in (30, 60, 120, 240)],

        [Btn(f"— Окно прогноза METAR: {pred_win} мин —", callback_data="noop")],
        [Btn(m(f"{v}м", f"{pred_win}м"), callback_data=f"smet_pr_{v}") for v in (5, 8, 10, 12, 15)],
        [Btn("✏️ Ввести окно вручную", callback_data="smet_prman")],

        [Btn("— Порог станций —", callback_data="noop")],
        [Btn(m(f"{v}°", f"{th}°"), callback_data=f"sth_{v}") for v in ("0.1", "0.5", "1.0", "5.0")],

        [Btn("— Интервал рынков —", callback_data="noop")],
        [Btn(m(f"{v}с", f"{mi}с"), callback_data=f"smi_{v}") for v in (10, 30, 60, 120)],

        [Btn("— Порог рынков —", callback_data="noop")],
        [Btn(m(f"{v}%", f"{m_th}%"), callback_data=f"smt_{v}") for v in ("0.5", "1.0", "2.0", "5.0")],

        back("back_main")
    ])