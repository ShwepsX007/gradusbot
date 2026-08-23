"""
Бэкенд исполнения ордеров на официальном унифицированном SDK Polymarket
(пакет `polymarket-client`, класс SecureClient).

Зачем он нужен:
    С мая 2026 все новые кошельки Polymarket — Deposit Wallet (тип 3).
    Старый py-clob-client-v2 подписывает ордер от EOA/прокси, и шлюз CLOB
    отвечает 400 "maker address not allowed, please use the deposit wallet flow".
    Унифицированный SDK сам определяет тип кошелька и подписывает так,
    как ждёт биржа.

Переменные окружения:
    POLY_PRIVATE_KEY               — приватный ключ подписанта (signer)
    POLY_FUNDER                    — адрес кошелька аккаунта (профиль на polymarket.com).
                                     Можно оставить пустым: SDK возьмёт депозит-кошелёк подписанта.
    POLY_RELAYER_API_KEY           — Relayer API key (Settings → API Keys → Relayer)
    POLY_RELAYER_API_KEY_ADDRESS   — Signer Address, выданный вместе с ключом
    POLY_API_KEY / POLY_API_SECRET / POLY_API_PASSPHRASE — L2-креды CLOB (необязательно,
                                     SDK выведет их сам)
"""

import logging
import os
import sys
from typing import Optional

import config as cfg

log = logging.getLogger("trading")

_client = None
_last_error = None


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key) or getattr(cfg, key, "") or ""
    if not val and key.startswith("POLY_"):
        # допускаем имена без префикса: RELAYER_API_KEY и т.п.
        short = key[len("POLY_"):]
        val = os.environ.get(short) or getattr(cfg, short, "") or ""
    return (val or default).strip()


MIN_PY = (3, 11)


def python_ok() -> bool:
    """polymarket-client требует Python 3.11 или новее."""
    return sys.version_info >= MIN_PY


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _looks_like_address(addr: str) -> bool:
    a = (addr or "").strip()
    if not a.startswith("0x") or len(a) != 42:
        return False
    try:
        int(a[2:], 16)
        return True
    except ValueError:
        return False


def available() -> bool:
    """Установлен ли пакет polymarket-client."""
    try:
        import polymarket  # noqa: F401
        return True
    except Exception:
        return False


def install_hint() -> str:
    """Что сказать пользователю, если бэкенд недоступен."""
    if not python_ok():
        return (f"нужен Python {MIN_PY[0]}.{MIN_PY[1]}+, а бот запущен на {python_version()}. "
                f"Поднимите новое окружение: bash setup_python311.sh")
    if not available():
        return "пакет не установлен: pip install polymarket-client"
    return ""


def enabled() -> bool:
    """Режим работы: v2 (старый SDK), unified (только новый), auto (новый как запасной)."""
    return _env("POLY_SDK", "auto").lower() in ("unified", "auto")


def forced() -> bool:
    return _env("POLY_SDK", "auto").lower() == "unified"


def last_error() -> Optional[str]:
    return _last_error


# =========================================================
# КЛИЕНТ
# =========================================================

def init() -> bool:
    global _client, _last_error

    if not python_ok() or not available():
        _last_error = install_hint() or "пакет polymarket-client не установлен"
        return False

    pk = _env("POLY_PRIVATE_KEY")
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    if not pk:
        _last_error = "POLY_PRIVATE_KEY пустой"
        return False

    wallet = _env("POLY_FUNDER") or None
    relayer_key = _env("POLY_RELAYER_API_KEY")
    relayer_addr = _env("POLY_RELAYER_API_KEY_ADDRESS")

    try:
        from polymarket import SecureClient

        kwargs = {"private_key": pk}
        if wallet:
            kwargs["wallet"] = wallet

        if relayer_key and relayer_addr:
            # Битый адрес не должен ронять подключение: без relayer-ключа
            # торговля работает, недоступны только газлесс-операции с кошельком.
            if not _looks_like_address(relayer_addr):
                log.warning(
                    f"⚠️ POLY_RELAYER_API_KEY_ADDRESS не похож на адрес "
                    f"({relayer_addr!r}) — нужен 0x и 40 hex-символов. Ключ пропущен."
                )
            else:
                try:
                    from polymarket import RelayerApiKey
                    kwargs["api_key"] = RelayerApiKey(key=relayer_key, address=relayer_addr)
                except Exception as e:
                    log.warning(f"⚠️ Relayer-ключ не применён: {e}")

        creds_key = _env("POLY_API_KEY")
        creds_secret = _env("POLY_API_SECRET")
        creds_pass = _env("POLY_API_PASSPHRASE")
        if creds_key and creds_secret and creds_pass:
            try:
                from polymarket import ApiKeyCreds
                kwargs["credentials"] = ApiKeyCreds(
                    apiKey=creds_key, secret=creds_secret, passphrase=creds_pass
                )
            except Exception as e:
                log.warning(f"[unified] не удалось применить сохранённые креды, выведу новые: {e}")

        _client = SecureClient.create(**kwargs)
        _last_error = None
        log.info(f"✅ [unified] Кошелёк {_client.wallet} тип {_client.wallet_type}")
        return True

    except Exception as e:
        _client = None
        _last_error = str(e)
        log.error(f"❌ [unified] инициализация не удалась: {e}")
        return False


def client():
    if _client is None:
        init()
    return _client


def is_ready() -> bool:
    return _client is not None


def get_wallet_address() -> Optional[str]:
    c = client()
    try:
        return str(c.wallet) if c else None
    except Exception:
        return None


def wallet_type() -> Optional[str]:
    c = client()
    try:
        return str(c.wallet_type) if c else None
    except Exception:
        return None


def get_balance() -> Optional[float]:
    c = client()
    if not c:
        return None
    try:
        ba = c.get_balance_allowance(asset_type="COLLATERAL")
        return round(float(ba.balance) / 1_000_000, 2)
    except Exception as e:
        log.warning(f"[unified] balance error: {e}")
        return None


# =========================================================
# ОРДЕРА
# =========================================================

def _fill_from_response(resp, side: str) -> dict:
    """Приводим ответ SDK к формату, который уже понимает остальной бот."""
    ok = bool(getattr(resp, "ok", False))
    if not ok:
        code = getattr(resp, "code", "unknown")
        msg = getattr(resp, "message", "order rejected")
        return {"success": False, "error": f"{code}: {msg}", "status": code}

    making = float(getattr(resp, "making_amount", 0) or 0)
    taking = float(getattr(resp, "taking_amount", 0) or 0)

    # BUY: отдаём USDC (making), получаем шары (taking). SELL — наоборот.
    if side == "BUY":
        filled_size, filled_cash = taking, making
    else:
        filled_size, filled_cash = making, taking

    avg_price = round(filled_cash / filled_size, 4) if filled_size else None

    return {
        "success": True,
        "orderID": str(getattr(resp, "order_id", "")),
        "status": str(getattr(resp, "status", "")),
        "matched": filled_size > 0,
        "filled": filled_size > 0,
        "filled_size": round(filled_size, 4),
        "filled_cash": round(filled_cash, 4),
        "avg_price": avg_price,
        "avg_price_cents": round(avg_price * 100, 1) if avg_price else None,
        "backend": "unified",
    }


def place_order(token_id, side: str, price: float, size: float,
                order_type: str = "GTC") -> dict:
    """
    Лимитный ордер. GTC кладётся в стакан, FOK/FAK исполняются немедленно
    (в унифицированном SDK это рыночный ордер с ограничением цены).
    """
    c = client()
    if not c:
        return {"error": f"unified client not ready: {_last_error}"}

    side = "BUY" if str(side).upper() == "BUY" else "SELL"
    price = float(price)
    if price > 1:
        price = round(price / 100, 4)
    size = float(size)
    ot = str(order_type).upper()

    try:
        if ot in ("FOK", "FAK"):
            if side == "BUY":
                resp = c.place_market_order(
                    token_id=str(token_id), side="BUY",
                    amount=round(size * price, 2), max_price=price, order_type=ot,
                )
            else:
                resp = c.place_market_order(
                    token_id=str(token_id), side="SELL",
                    shares=size, min_price=price, order_type=ot,
                )
        else:
            resp = c.place_limit_order(
                token_id=str(token_id), price=price, size=size, side=side,
            )

        res = _fill_from_response(resp, side)
        res["order_type"] = ot
        res["requested_price"] = price
        res["requested_size"] = size
        return res

    except Exception as e:
        log.error(f"[unified] place_order error: {e}")
        return {"error": str(e), "backend": "unified"}


def place_market_order(token_id, side: str, amount: float,
                       worst_price: Optional[float] = None,
                       order_type: str = "FOK") -> dict:
    """
    Рыночный ордер. BUY: amount в USDC. SELL: amount в шарах.
    worst_price — худшая допустимая цена (защита от проскальзывания).
    """
    c = client()
    if not c:
        return {"error": f"unified client not ready: {_last_error}"}

    side = "BUY" if str(side).upper() == "BUY" else "SELL"
    amount = float(amount)
    ot = str(order_type).upper()
    if ot not in ("FOK", "FAK"):
        ot = "FOK"

    if worst_price is not None:
        worst_price = float(worst_price)
        if worst_price > 1:
            worst_price = round(worst_price / 100, 4)
        worst_price = max(0.001, min(0.999, worst_price))

    try:
        if side == "BUY":
            kw = {"token_id": str(token_id), "side": "BUY", "amount": amount, "order_type": ot}
            if worst_price is not None:
                kw["max_price"] = worst_price
        else:
            kw = {"token_id": str(token_id), "side": "SELL", "shares": amount, "order_type": ot}
            if worst_price is not None:
                kw["min_price"] = worst_price

        resp = c.place_market_order(**kw)

        res = _fill_from_response(resp, side)
        res["order_type"] = ot
        res["requested_amount"] = amount
        res["worst_price"] = worst_price

        if res.get("success") and not res.get("filled"):
            res["success"] = False
            res["error"] = f"{ot} не исполнен (нет ликвидности по цене не хуже {worst_price})"
        return res

    except Exception as e:
        log.error(f"[unified] place_market_order error: {e}")
        return {"error": str(e), "backend": "unified"}


def cancel_order(order_id: str) -> dict:
    c = client()
    if not c:
        return {"error": f"unified client not ready: {_last_error}"}
    try:
        c.cancel_order(order_id=str(order_id))
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


def status() -> dict:
    """Короткая сводка для меню диагностики."""
    return {
        "installed": available(),
        "python": python_version(),
        "python_ok": python_ok(),
        "hint": install_hint(),
        "mode": _env("POLY_SDK", "auto").lower(),
        "ready": is_ready(),
        "wallet": get_wallet_address() if is_ready() else None,
        "wallet_type": wallet_type() if is_ready() else None,
        "relayer_key": bool(_env("POLY_RELAYER_API_KEY")),
        "error": _last_error,
    }
def get_open_orders() -> list:
    """Открытые ордера в формате, который уже понимает бот."""
    c = client()
    if not c:
        return []
    try:
        out = []
        for o in c.list_open_orders().iter_items():
            out.append({
                "id": str(getattr(o, "id", "") or ""),
                "side": str(getattr(o, "side", "?")),
                "price": float(getattr(o, "price", 0) or 0),
                "original_size": float(getattr(o, "original_size", 0) or 0),
                "size_matched": float(getattr(o, "size_matched", 0) or 0),
                "token_id": str(getattr(o, "token_id", "") or ""),
                "market": str(getattr(o, "condition_id", "") or ""),
                "outcome": str(getattr(o, "outcome", "") or ""),
                "order_type": str(getattr(o, "order_type", "") or ""),
                "status": str(getattr(o, "status", "") or ""),
            })
        return out
    except Exception as e:
        log.warning(f"[unified] get_open_orders error: {e}")
        return []


def cancel_all() -> dict:
    c = client()
    if not c:
        return {"error": f"unified client not ready: {_last_error}"}
    try:
        c.cancel_all()
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}
