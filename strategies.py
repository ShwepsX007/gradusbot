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

        # Первая строчка асков — это Best Ask (наилучшая цена, по которой мы купим мгновенно)
        best_ask = book["asks"][0]
        best_ask_price = float(best_ask["price"])  # Например, 0.54
        best_ask_size = float(best_ask["size"])    # Доступный объем на этом уровне цен

        ask_cents = best_ask_price * 100.0

        # Контроль цены: если реальный аск хуже нашего порога — пропускаем сделку
        if ask_cents > entry_threshold:
            log.info(f"Пропуск: Реальный Best Ask ({ask_cents}¢) превышает установленный порог ({entry_threshold}¢)")
            return None

        # Расчет размера позиции
        if budget_mode == "dollars":
            actual_size = round(size / best_ask_price, 1)
            actual_size = max(1.0, actual_size)
        else:
            actual_size = size

        # Логирование нехватки ликвидности
        if best_ask_size < actual_size:
            log.warning(f"Ликвидности на Best Ask недостаточно ({best_ask_size} из {actual_size}). Будет частичное исполнение.")

        # Выставляем ордер с проскальзыванием в +1 цент для защиты от мгновенного прострела, но не выше лимита
        slippage_price = min(best_ask_price + 0.01, entry_threshold / 100.0)

        reason = (
            f"Погодный триггер сработал: {entry_temp}°{unit}. "
            f"Реальный стакан Best Ask: {round(ask_cents, 1)}¢ <= Порога {entry_threshold}¢. "
            f"Доступный объем: {best_ask_size}"
        )

        return {
            'action': 'BUY',
            'token_id': token_id,
            'side': 'BUY',
            'price': slippage_price,
            'expected_fill_cents': int(ask_cents),
            'size': actual_size,
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