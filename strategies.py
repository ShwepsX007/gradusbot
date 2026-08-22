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
        unit = self.params.get("unit", "C").upper()

        current_temp_c = station_data.get("temp")
        if current_temp_c is None:
            return None

        entry_temp = convert_station_temp(current_temp_c, unit)
        
        # Проверка триггера погоды
        trigger_fired = False
        if direction == "up" and entry_temp >= target_temp:
            trigger_fired = True
        elif direction == "down" and entry_temp <= target_temp:
            trigger_fired = True

        if not trigger_fired:
            return None

        # Поиск нужной опции рынка (YES токена под наше условие)
        options = market_data.get("options", [])
        best_option = None
        for o in options:
            q = o.get("label", "").lower() or o.get("question", "").lower()
            t = extract_temp_from_title(q)
            if t is not None:
                if direction == "up" and ("above" in q or "higher" in q) and entry_temp >= t:
                    best_option = o
                    break
                if direction == "down" and ("below" in q or "lower" in q) and entry_temp <= t:
                    best_option = o
                    break

        if not best_option:
            return None

        token_id = best_option.get("token_yes") or best_option.get("condition_id")
        if not token_id:
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

        reason = (
            f"Погодный триггер сработал: {entry_temp}°{unit}. "
            f"Best Ask {round(ask_cents, 1)}¢, расчётный VWAP по стакану {est_cents}¢ "
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
            'reason': reason,
        }


class SimpleMarketMakerStrategy(BaseStrategy):
    name = "Базовый Маркет-Мейкер (Стакан)"
    description = "Заготовка под ММ. Позволяет работать со спредом, выставляя bids и asks на основе стакана."

    def analyze_market(self, market_data: dict, station_data: dict, order_books: dict) -> Optional[dict]:
        """
        Сюда вы можете зашить логику маркет-мейкера.
        Вам доступны:
        - order_books[token_id]['bids'] -> список уровней покупки
        - order_books[token_id]['asks'] -> список уровней продажи
        """
        options = market_data.get("options", [])
        if not options:
            return None
        
        token_id = options[0].get("token_yes")
        book = order_books.get(token_id)
        
        if not book or not book.get("bids") or not book.get("asks"):
            return None
            
        best_bid = float(book["bids"][0]["price"])
        best_ask = float(book["asks"][0]["price"])
        spread = best_ask - best_bid
        
        # Ваша будущая ММ логика расчёта сеток лимитных ордеров:
        if spread >= 0.02:
            pass
            
        return None


# Регистрация стратегий
STRATEGIES = {
    "front_runner": TemperatureSniperStrategy,
    "temperature_sniper": TemperatureSniperStrategy,
    "market_maker": SimpleMarketMakerStrategy,
    "station_stop": TemperatureSniperStrategy # Добавлен алиас для обратной совместимости
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