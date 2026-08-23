import logging
import math
import re
from typing import Optional, Dict, Any

log = logging.getLogger("strategies")


class BaseStrategy:
    name = "Базовая Стратегия со стаканом"
    description = "Шаблон стратегии, имеющей постоянный доступ к стаканам токенов."

    def __init__(self, **kwargs):
        self.params = kwargs

    def analyze_market(self, market_data: dict, station_data: dict, order_books: dict) -> Optional[dict]:
        """
        market_data: Данные рынка из Gamma API (структура, названия)
        station_data: Текущие показатели метеостанции
        order_books: Словарь снимков стаканов вида { token_id: {"bids": [...], "asks": [...]} }
        """
        return None


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def convert_station_temp(station_temp_c, unit):
    if station_temp_c is None:
        return None
    if unit == "F":
        return c_to_f(station_temp_c)
    return station_temp_c


NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')


def extract_temp_from_title(text):
    if not text:
        return None
    m = NUM_RE.search(text)
    return float(m.group(0)) if m else None


def sort_asks(asks):
    """Аски по возрастанию цены (best ask первый)."""
    out = []
    for a in asks or []:
        try:
            out.append({"price": float(a["price"]), "size": float(a["size"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x["price"])


def sort_bids(bids):
    """Биды по убыванию цены (best bid первый)."""
    out = []
    for b in bids or []:
        try:
            out.append({"price": float(b["price"]), "size": float(b["size"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x["price"], reverse=True)


def walk_book_by_cash(asks, budget_usd, worst_price):
    """Сколько шар реально возьмём на budget_usd, не выходя за worst_price."""
    shares = 0.0
    cash = 0.0
    for lvl in asks:
        if worst_price is not None and lvl["price"] > worst_price + 1e-9:
            break
        lvl_cash = lvl["price"] * lvl["size"]
        remaining = budget_usd - cash
        if remaining <= 1e-9:
            break
        if lvl_cash <= remaining:
            cash += lvl_cash
            shares += lvl["size"]
        else:
            shares += remaining / lvl["price"]
            cash += remaining
            break
    vwap = (cash / shares) if shares > 0 else 0.0
    return {"shares": round(shares, 4), "cash": round(cash, 4), "vwap": round(vwap, 4)}


def walk_book_by_shares(asks, want_shares, worst_price):
    """Сколько шар доступно в пределах worst_price и по какой средней цене."""
    shares = 0.0
    cash = 0.0
    for lvl in asks:
        if worst_price is not None and lvl["price"] > worst_price + 1e-9:
            break
        take = min(lvl["size"], want_shares - shares)
        if take <= 0:
            break
        shares += take
        cash += take * lvl["price"]
        if shares >= want_shares - 1e-9:
            break
    vwap = (cash / shares) if shares > 0 else 0.0
    return {"shares": round(shares, 4), "cash": round(cash, 4), "vwap": round(vwap, 4)}


RANGE_RE = re.compile(
    r'(-?\d+(?:\.\d+)?)\s*(?:°|deg)?\s*[cf]?\s*(?:-|–|—|to)\s*(-?\d+(?:\.\d+)?)',
    re.IGNORECASE
)
UNIT_RE = re.compile(r'°?\s*(?<![a-z])([cf])(?![a-z])', re.IGNORECASE)

BELOW_WORDS = ("or below", "or lower", "or less", "below", "under", "and below", "or colder")
ABOVE_WORDS = ("or higher", "or above", "or more", "above", "over", "and above", "or warmer")


def detect_label_unit(text):
    """Единица измерения из подписи исхода: 'F', 'C' или None."""
    if not text:
        return None
    low = text.lower()
    if "°f" in low or "fahrenheit" in low:
        return "F"
    if "°c" in low or "celsius" in low:
        return "C"
    m = UNIT_RE.search(low)
    if m:
        return m.group(1).upper()
    return None


def detect_market_unit(options, default=None):
    """Единица рынка по большинству подписей исходов (рынки Polymarket по погоде обычно в °F)."""
    votes = {"F": 0, "C": 0}
    for o in options or []:
        u = detect_label_unit(o.get("label") or o.get("question") or "")
        if u in votes:
            votes[u] += 1
    if votes["F"] or votes["C"]:
        return "F" if votes["F"] >= votes["C"] else "C"
    return default


def parse_option_bucket(text):
    """
    Разбирает подпись исхода в интервал температур.
      '65°F or below' -> (-inf, 65)
      '66-67°F'       -> (66, 67)
      '84°F or higher'-> (84, +inf)
      'Above 30C'     -> (30, +inf)
      '72°F'          -> (72, 72)
    Возвращает (lo, hi, unit) или None.
    """
    if not text:
        return None
    low = text.strip().lower()
    unit = detect_label_unit(low)

    m = RANGE_RE.search(low)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi, unit)

    nums = NUM_RE.findall(low)
    if not nums:
        return None
    val = float(nums[0])

    if any(w in low for w in BELOW_WORDS):
        return (float("-inf"), val, unit)
    if any(w in low for w in ABOVE_WORDS):
        return (val, float("inf"), unit)

    return (val, val, unit)


def bucket_contains(bucket, temp):
    """
    Рынки погоды резолвятся по целым градусам, поэтому сравниваем округлённое значение.
    '66-67°F' покрывает 66 и 67 градусов.
    """
    if bucket is None or temp is None:
        return False
    lo, hi, _ = bucket
    t = round(float(temp))
    return lo - 1e-9 <= t <= hi + 1e-9


def bucket_label(bucket, unit):
    lo, hi, _ = bucket
    u = f"°{unit}" if unit else ""
    if lo == float("-inf"):
        return f"{_num(hi)}{u} и ниже"
    if hi == float("inf"):
        return f"{_num(lo)}{u} и выше"
    if lo == hi:
        return f"{_num(lo)}{u}"
    return f"{_num(lo)}–{_num(hi)}{u}"


def _num(v):
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except (TypeError, ValueError):
        return str(v)


class TemperatureSniperStrategy(BaseStrategy):
    name = "Метео Снайпер (Стакан)"
    description = "Осуществляет вход по реальному аску из стакана, если погодные условия удовлетворены."

    def analyze_market(self, market_data: dict, station_data: dict, order_books: dict) -> Optional[dict]:
        # Параметры конкретной связки
        direction = self.params.get("direction", "up")  # up / down
        target_temp = float(self.params.get("target", 0.0))
        entry_threshold = float(self.params.get("thresh", 55.0))  # Максимальная цена в центах
        budget_mode = self.params.get("budget_mode", "dollars")  # dollars / shares
        size = float(self.params.get("size", 10.0))
        unit = (self.params.get("unit", "C") or "C").upper()   # единица ЦЕЛИ, заданной пользователем

        current_temp_c = station_data.get("temp")
        if current_temp_c is None:
            return None

        # Температура в единицах цели — для проверки триггера.
        # Округляем до 0.1, иначе 21.1°C = 69.98°F не пройдёт цель «70°F».
        entry_temp = round(convert_station_temp(current_temp_c, unit), 1)

        trigger_fired = (
            (direction == "up" and entry_temp >= target_temp) or
            (direction == "down" and entry_temp <= target_temp)
        )
        if not trigger_fired:
            return None

        options = market_data.get("options", [])
        if not options:
            return None

        # Единица РЫНКА определяется по его же подписям: '72-73°F' -> F, 'Above 30C' -> C
        market_unit = detect_market_unit(options, default=unit)
        market_temp = convert_station_temp(current_temp_c, market_unit)

        # 1) Диапазонные рынки: ищем корзину, в которую попадает текущая температура
        best_option = None
        best_bucket = None
        for o in options:
            label = o.get("label") or o.get("question") or ""
            bucket = parse_option_bucket(label)
            if bucket and bucket_contains(bucket, market_temp):
                best_option, best_bucket = o, bucket
                break

        # 2) Фолбэк для рынков вида 'Above X' / 'Below X' без корзин
        if best_option is None:
            for o in options:
                label = (o.get("label") or o.get("question") or "").lower()
                t = extract_temp_from_title(label)
                if t is None:
                    continue
                if direction == "up" and any(w in label for w in ABOVE_WORDS) and market_temp >= t:
                    best_option, best_bucket = o, (t, float("inf"), market_unit)
                    break
                if direction == "down" and any(w in label for w in BELOW_WORDS) and market_temp <= t:
                    best_option, best_bucket = o, (float("-inf"), t, market_unit)
                    break

        if not best_option:
            log.info(
                f"Нет подходящего исхода: температура {round(market_temp, 1)}°{market_unit} "
                f"не попала ни в одну корзину рынка."
            )
            return None

        token_id = best_option.get("token_yes")
        if not token_id:
            log.warning(
                f"У исхода «{best_option.get('label', '?')}» нет CLOB token_yes — "
                f"вход невозможен (стакана по condition_id не существует)."
            )
            return None

        # --- НЕПРЕРЫВНАЯ РАБОТА СО СТАКАНОМ ---
        book = order_books.get(token_id)
        if not book or not book.get("asks"):
            log.warning(f"Стакан для токена {token_id} пуст или временно недоступен.")
            return None

        asks = sort_asks(book["asks"])
        if not asks:
            return None

        best_ask_price = float(asks[0]["price"])
        best_ask_size = float(asks[0]["size"])
        ask_cents = best_ask_price * 100.0

        # Худшая допустимая цена = порог входа. Это slippage guard для FOK/FAK.
        worst_price = entry_threshold / 100.0

        # Контроль цены: если даже лучший аск хуже порога — сделки нет
        if ask_cents > entry_threshold + 1e-9:
            log.info(f"Пропуск: Best Ask ({round(ask_cents, 2)}¢) превышает порог ({entry_threshold}¢)")
            return None

        # Считаем исполнение ПО ВСЕЙ ГЛУБИНЕ стакана до порога, а не только по первому уровню
        if budget_mode == "dollars":
            budget_usd = float(size)
            fill = walk_book_by_cash(asks, budget_usd, worst_price)
            if fill["shares"] <= 0:
                log.info("Пропуск: нет ликвидности в пределах порога цены.")
                return None

            # Ликвидности не хватает на весь бюджет — FOK гарантированно отклонится,
            # поэтому уменьшаем сумму до реально доступной.
            amount_usd = round(min(budget_usd, fill["cash"]), 2)
            if amount_usd < 1.0:
                log.info(f"Пропуск: доступный объём в пределах порога слишком мал ({amount_usd}$).")
                return None

            order_style = "MARKET"          # MarketOrderArgs, amount в долларах
            order_type = "FOK"
            est_shares = round(fill["shares"] * (amount_usd / fill["cash"]), 2) if fill["cash"] else 0.0
            est_price = fill["vwap"]
            amount = amount_usd
        else:
            want_shares = float(size)
            fill = walk_book_by_shares(asks, want_shares, worst_price)
            if fill["shares"] + 1e-9 < want_shares:
                log.info(
                    f"Пропуск: в пределах порога {entry_threshold}¢ доступно только "
                    f"{fill['shares']} шар из {want_shares} — FOK не исполнится."
                )
                return None

            order_style = "LIMIT_FOK"       # ровно N шар по цене не хуже порога
            order_type = "FOK"
            est_shares = want_shares
            est_price = fill["vwap"]
            amount = want_shares

        est_cents = round(est_price * 100.0, 1)

        if best_ask_size < est_shares:
            log.info(
                f"Первый уровень тоньше заявки ({best_ask_size} из {est_shares}) — "
                f"исполнение уйдёт глубже в стакан, расчётный VWAP {est_cents}¢."
            )

        temp_c = float(current_temp_c)
        temp_both = f"{temp_c:.1f}°C / {c_to_f(temp_c):.1f}°F"

        reason = (
            f"Температура {temp_both} попала в исход «{best_option.get('label', '?')}» "
            f"({bucket_label(best_bucket, market_unit)}). "
            f"Цель {_num(target_temp)}°{unit} пройдена. "
            f"Best Ask {round(ask_cents, 1)}¢, VWAP по стакану {est_cents}¢ "
            f"<= порога {entry_threshold}¢. Вход рыночным {order_type}."
        )

        return {
            'action': 'BUY',
            'token_id': token_id,
            'side': 'BUY',
            'order_style': order_style,       # MARKET (в $) или LIMIT_FOK (в шарах)
            'order_type': order_type,         # FOK — всё или ничего
            'amount': amount,                 # $ для MARKET, шары для LIMIT_FOK
            'worst_price': worst_price,       # slippage guard, не целевая цена
            'price': worst_price,             # обратная совместимость
            'expected_fill_cents': int(round(est_cents)),
            'expected_vwap_cents': est_cents,
            'size': est_shares,
            'question': best_option.get("question", market_data.get("name", "")),
            'unit': unit,
            'market_unit': market_unit,
            'outcome_label': best_option.get('label', ''),
            'reason': reason,
        }


class StationStopStrategy(BaseStrategy):
    """
    Метео Стоп: вход в рынок вручную (кнопкой), выход — по сигналу станции.

    Смысл: мы сидим в температурной корзине (например 16°C). Если станция показала,
    что температура ушла в сторону, при которой наша корзина уже не сыграет,
    надо выскочить из стакана раньше, чем это сделают остальные.
    """

    name = "Метео Стоп (выход по станции)"
    description = (
        "Вход в выбранную корзину вручную (по рынку или отложником). "
        "Выход: тейк-профит, стоп-лосс или срочная продажа по сигналу станции."
    )

    def analyze_market(self, market_data: dict, station_data: dict, order_books: dict) -> Optional[dict]:
        # Автовхода нет — вход выполняется кнопкой «Войти в рынок»
        return None


def auto_stop_temp(bucket, direction):
    """
    Температура стопа из корзины, в которую мы вошли (в единицах рынка).
      корзина 70-71°F, direction=up   -> стоп 72 (стало теплее — корзина не сыграет)
      корзина 70-71°F, direction=down -> стоп 69
      корзина '84°F or higher', up    -> стопа сверху нет
    Возвращает None, если в эту сторону корзина не может «сломаться».
    """
    if not bucket:
        return None
    lo, hi, _ = bucket
    if direction == "up":
        return None if hi == float("inf") else hi + 1.0
    return None if lo == float("-inf") else lo - 1.0


def stop_triggered(temp_market, stop_temp, direction):
    """
    Сработал ли стоп. Сравниваем по округлённому градусу — рынки резолвятся по целым.
    """
    if temp_market is None or stop_temp is None:
        return False
    t = round(float(temp_market))
    if direction == "up":
        return t >= float(stop_temp) - 1e-9
    return t <= float(stop_temp) + 1e-9


# Регистрация стратегий
STRATEGIES = {
    "temperature_sniper": TemperatureSniperStrategy,
    "front_runner": TemperatureSniperStrategy,   # алиас для обратной совместимости
    "station_stop": StationStopStrategy,
    "market_maker": StationStopStrategy,         # старый id заменённой заготовки ММ
}


def get_strategies_list():
    """Возвращает список стратегий для отображения в Telegram."""
    # Используем set для удаления дубликатов из-за алиасов, но сохраняем уникальные классы
    unique_strats = []
    seen = set()
    for k, v in STRATEGIES.items():
        if v not in seen:
            seen.add(v)
            unique_strats.append({"id": k, "name": v.name, "desc": v.description})
    return unique_strats