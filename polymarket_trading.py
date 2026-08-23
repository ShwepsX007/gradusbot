import json
import logging
import os
import time
import requests
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct

import config as cfg

log = logging.getLogger("trading")

HOST = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

_client = None


# =========================================================
# HELPERS
# =========================================================

def _get_env(key: str, default="") -> str:
    return os.environ.get(key) or getattr(cfg, key, default) or default


def _get_int_env(key: str, default=0) -> int:
    try:
        return int(_get_env(key, str(default)))
    except:
        return default


def _normalize_pk(pk: str) -> str:
    pk = (pk or "").strip()
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    return pk


def _is_valid_eth_address(addr: str) -> bool:
    return isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42


def _extract_error_text(err) -> str:
    try:
        return str(err)
    except:
        return repr(err)


def _build_creds():
    api_key = _get_env("POLY_API_KEY").strip()
    api_sec = _get_env("POLY_API_SECRET").strip()
    api_pass = _get_env("POLY_API_PASSPHRASE").strip()

    if not (api_key and api_sec and api_pass):
        return None

    try:
        from py_clob_client_v2.clob_types import ApiCreds
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_sec,
            api_passphrase=api_pass,
        )
        log.info("✅ API creds загружены")
        return creds
    except Exception as e:
        log.warning(f"⚠️ Не удалось создать ApiCreds: {e}")
        return None


def _build_client(signature_type: int):
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.constants import POLYGON

    pk = _normalize_pk(_get_env("POLY_PRIVATE_KEY"))
    funder = _get_env("POLY_FUNDER").strip()
    creds = _build_creds()

    if not pk:
        raise Exception("POLY_PRIVATE_KEY пустой")

    kwargs = {
        "host": HOST,
        "chain_id": POLYGON,
        "key": pk,
        "creds": creds,
        "signature_type": signature_type,
    }

    if signature_type in (1, 2, 3) and funder:
        kwargs["funder"] = funder

    client = ClobClient(**kwargs)
    return client


def _make_balance_params():
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        return BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    except Exception as e:
        log.warning(f"⚠️ Не удалось создать BalanceAllowanceParams: {e}")
        return None


def _get_balance_allowance_safe(client):
    params = _make_balance_params()
    if params is not None:
        return client.get_balance_allowance(params)

    try:
        return client.get_balance_allowance()
    except TypeError:
        return client.get_balance_allowance({})


def _update_balance_allowance_safe(client):
    params = _make_balance_params()
    if params is not None:
        return client.update_balance_allowance(params)

    try:
        return client.update_balance_allowance()
    except TypeError:
        return client.update_balance_allowance({})


def _object_to_dict(obj):
    if obj is None:
        return {}

    if isinstance(obj, dict):
        return dict(obj)

    try:
        if hasattr(obj, "__dict__"):
            d = dict(vars(obj))
            if d:
                return d
    except:
        pass

    result = {}
    for key in [
        "id", "orderID", "order_id", "orderId",
        "side", "price",
        "original_size", "size", "remaining_size", "initial_size",
        "status", "asset_id", "token_id"
    ]:
        try:
            val = getattr(obj, key, None)
            if val is not None:
                result[key] = val
        except:
            pass
    return result


def _extract_order_id(order) -> str:
    d = _object_to_dict(order)
    for k in ("id", "orderID", "order_id", "orderId"):
        v = d.get(k)
        if v:
            return str(v)
    return ""


def _normalize_open_order(order) -> dict:
    d = _object_to_dict(order)
    oid = _extract_order_id(d)
    side = d.get("side") or d.get("order_side") or d.get("orderSide") or "?"
    original_size = (
        d.get("original_size")
        or d.get("size")
        or d.get("initial_size")
        or d.get("remaining_size")
        or d.get("amount")
        or "?"
    )

    return {
        **d,
        "id": oid or "?",
        "side": side,
        "original_size": original_size,
    }


def _is_cancel_success(res, order_id: str) -> bool:
    if res is None:
        return False

    if isinstance(res, dict):
        if res.get("error"):
            return False

        if res.get("success") is True:
            return True

        for key in ("canceled", "cancelled", "cancelledOrderIds", "canceled_order_ids", "canceledOrderIds"):
            val = res.get(key)
            if isinstance(val, list) and str(order_id) in [str(x) for x in val]:
                return True

        return True

    if isinstance(res, list):
        return any(str(x) == str(order_id) for x in res) or len(res) > 0

    if isinstance(res, str):
        low = res.lower()
        if "error" in low or "fail" in low:
            return False
        if "cancel" in low:
            return True

    return bool(res)


# =========================================================
# INIT
# =========================================================

def init_trading() -> bool:
    global _client
    _client = None

    try:
        pu = _unified()
        if pu.available() and pu.forced():
            log.info("🔧 POLY_SDK=unified — использую официальный SDK polymarket-client")
            if pu.init():
                return True
            log.error(f"❌ unified SDK не поднялся: {pu.last_error()}")
    except Exception as e:
        log.warning(f"unified probe failed: {e}")

    pk = _normalize_pk(_get_env("POLY_PRIVATE_KEY"))
    funder = _get_env("POLY_FUNDER").strip()
    configured_sig_type = _get_int_env("POLY_SIGNATURE_TYPE", 1)

    log.info(
        f"🔧 init_trading | sig_type={configured_sig_type} | "
        f"funder={funder} | api_key={'yes' if _get_env('POLY_API_KEY') else 'no'}"
    )

    if not pk:
        log.error("❌ POLY_PRIVATE_KEY пустой")
        return False

    try:
        eoa_address = Account.from_key(pk).address
        log.info(f"✅ EOA адрес: {eoa_address}")
    except Exception as e:
        log.error(f"❌ Неверный приватный ключ: {e}")
        return False

    if funder and not _is_valid_eth_address(funder):
        log.error(f"❌ POLY_FUNDER некорректен: {funder}")
        return False

    sig_types_to_try = []
    for st in [configured_sig_type, 1, 2, 3, 0]:
        if st not in sig_types_to_try:
            sig_types_to_try.append(st)

    for st in sig_types_to_try:
        try:
            if st in (1, 2, 3) and not funder:
                log.warning(f"⚠️ Пропускаю sig_type={st}, потому что POLY_FUNDER пустой")
                continue

            log.info(f"🔄 Пробую signature_type={st}...")
            client = _build_client(st)

            try:
                ok = client.get_ok()
                log.info(f"✅ CLOB ping OK sig_type={st}: {ok}")
            except Exception as e:
                log.warning(f"⚠️ CLOB ping failed sig_type={st}: {e}")

            try:
                addr = client.get_address()
                log.info(f"✅ Client address sig_type={st}: {addr}")
            except Exception as e:
                log.warning(f"⚠️ get_address failed sig_type={st}: {e}")

            try:
                bal = _get_balance_allowance_safe(client)
                log.info(f"✅ Баланс sig_type={st}: {bal}")
                _client = client
                update_env_and_config({"POLY_SIGNATURE_TYPE": str(st)})
                log.info(f"✅ Клиент инициализирован sig_type={st}")
                return True
            except Exception as e:
                err = _extract_error_text(e)
                log.warning(f"⚠️ Баланс sig_type={st}: {err}")

                lowered = err.lower()
                if (
                    "unauthorized" not in lowered
                    and "forbidden" not in lowered
                    and "maker address not allowed" not in lowered
                ):
                    _client = client
                    update_env_and_config({"POLY_SIGNATURE_TYPE": str(st)})
                    log.info(f"✅ Клиент принят sig_type={st}")
                    return True

        except Exception as e:
            log.error(f"❌ sig_type={st} failed: {e}")
            continue

    log.error("❌ Ни один sig_type не сработал")

    try:
        pu = _unified()
        if pu.available() and pu.enabled() and pu.init():
            _switch_to_unified("py-clob-client-v2 не смог подключиться")
            return True
    except Exception as e:
        log.warning(f"unified init failed: {e}")

    return False


def is_ready() -> bool:
    return _client is not None


def get_wallet_address() -> Optional[str]:
    try:
        funder = _get_env("POLY_FUNDER").strip()
        sig_type = _get_int_env("POLY_SIGNATURE_TYPE", 1)
        pk = _normalize_pk(_get_env("POLY_PRIVATE_KEY"))

        if sig_type in (1, 2, 3) and funder:
            return funder

        if pk:
            return Account.from_key(pk).address

        return None
    except Exception as e:
        log.error(f"get_wallet_address error: {e}")
        return None


def get_eoa_address() -> Optional[str]:
    try:
        pk = _normalize_pk(_get_env("POLY_PRIVATE_KEY"))
        if not pk:
            return None
        return Account.from_key(pk).address
    except:
        return None


# =========================================================
# MARKET INFO
# =========================================================

def get_market_info(token_id) -> Optional[dict]:
    try:
        r = requests.get(
            f"{GAMMA}/markets",
            params={"clob_token_ids": str(token_id)},
            timeout=10
        )
        if r.status_code != 200:
            return None

        data = r.json()
        if isinstance(data, list) and data:
            m = data[0]
            return {
                "neg_risk": m.get("negRisk", False),
                "accepting_orders": m.get("acceptingOrders", False),
                "closed": m.get("closed", True),
                "min_size": float(m.get("orderMinSize", 5)),
            }
        return None
    except Exception as e:
        log.warning(f"get_market_info error: {e}")
        return None


def get_event_markets(slug: str) -> Optional[dict]:
    def _as_list(val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else []
            except:
                return []
        return []

    def _to_cents(val):
        try:
            x = float(val)
            if x <= 1:
                x *= 100
            return max(0, min(100, round(x)))
        except:
            return 0

    try:
        r = requests.get(f"{GAMMA}/events/slug/{slug}", timeout=15)
        if r.status_code != 200:
            log.warning(f"get_event_markets bad status {r.status_code} for slug={slug}")
            return None

        data = r.json()
        if isinstance(data, list):
            data = data[0] if data else {}

        if not isinstance(data, dict):
            log.warning(f"get_event_markets invalid response type for slug={slug}")
            return None

        markets = []

        for m in data.get("markets", []) or []:
            tids = _as_list(m.get("clobTokenIds"))
            prices = _as_list(m.get("outcomePrices"))
            outcomes = _as_list(m.get("outcomes"))

            if len(tids) < 2:
                continue

            yes_idx, no_idx = 0, 1

            if len(outcomes) >= 2:
                lowered = [str(x).strip().lower() for x in outcomes]
                try:
                    yes_idx = lowered.index("yes")
                    no_idx = lowered.index("no")
                except:
                    yes_idx, no_idx = 0, 1

            if yes_idx >= len(tids) or no_idx >= len(tids):
                yes_idx, no_idx = 0, 1

            price_yes = _to_cents(prices[yes_idx]) if len(prices) > yes_idx else 0
            price_no = _to_cents(prices[no_idx]) if len(prices) > no_idx else 0

            active = m.get("active")
            if active is None:
                active = not bool(m.get("closed", False))

            markets.append({
                "question": m.get("groupItemTitle") or m.get("question") or "Без названия",
                "token_yes": str(tids[yes_idx]),
                "token_no": str(tids[no_idx]),
                "price_yes": price_yes,
                "price_no": price_no,
                "active": bool(active),
                "neg_risk": bool(m.get("negRisk", False)),
                "accepting_orders": bool(m.get("acceptingOrders", active)),
            })

        if not markets:
            log.warning(f"get_event_markets: empty markets for slug={slug}")

        return {
            "title": data.get("title") or slug,
            "markets": markets
        }

    except Exception as e:
        log.warning(f"get_event_markets error for slug={slug}: {e}")
        return None


# Токены без стакана (закрытый/неразмещённый рынок) — не долбим API каждые 20 секунд
_NO_BOOK_CACHE = {}
_NO_BOOK_TTL = 600  # секунд


def _is_no_orderbook_error(err) -> bool:
    txt = _extract_error_text(err).lower()
    return "no orderbook" in txt or "orderbook exists" in txt or "404" in txt


def has_orderbook(token_id: str) -> bool:
    """False, если по токену заведомо нет стакана (запомнено ранее)."""
    ts = _NO_BOOK_CACHE.get(str(token_id))
    if ts is None:
        return True
    if time.time() - ts > _NO_BOOK_TTL:
        _NO_BOOK_CACHE.pop(str(token_id), None)
        return True
    return False


def _mark_no_orderbook(token_id: str, quiet: bool):
    tid = str(token_id)
    if not quiet:
        log.info(f"ℹ️ Для токена {tid[:14]}… стакана нет (рынок закрыт или не размещён на CLOB). "
                 f"Пропускаю запросы на {_NO_BOOK_TTL // 60} мин.")
    _NO_BOOK_CACHE[tid] = time.time()


def _parse_book_payload(resp) -> Optional[dict]:
    if hasattr(resp, "bids") and hasattr(resp, "asks"):
        return {
            "bids": [{"price": float(b.price), "size": float(b.size)} for b in resp.bids],
            "asks": [{"price": float(a.price), "size": float(a.size)} for a in resp.asks]
        }
    if isinstance(resp, dict):
        return {
            "bids": [{"price": float(b["price"]), "size": float(b["size"])} for b in resp.get("bids", [])],
            "asks": [{"price": float(a["price"]), "size": float(a["size"])} for a in resp.get("asks", [])]
        }
    return None


def get_order_book(token_id: str) -> Optional[dict]:
    """
    Реальный стакан (bids/asks) по токену.
    Возвращает None, если стакана нет (404) или запрос не удался.
    """
    global _client

    tid = str(token_id or "").strip()
    if not tid:
        return None

    # id токена CLOB — это длинное десятичное число. condition_id (0x...) стаканов не имеет.
    if tid.startswith("0x") or not tid.isdigit():
        if has_orderbook(tid):
            log.info(f"ℹ️ {tid[:14]}… не является CLOB token_id (похоже на condition_id) — стакан не запрашиваю.")
        _NO_BOOK_CACHE[tid] = time.time()
        return None

    if not has_orderbook(tid):
        return None

    if _client is None:
        log.error("ClobClient не инициализирован для получения стакана")
        return None

    try:
        book = _parse_book_payload(_client.get_order_book(tid))
        if book is not None:
            return book
    except Exception as e:
        if _is_no_orderbook_error(e):
            # Штатная ситуация: рынок закрыт/разрешён. HTTP-фолбэк даст тот же 404 — не дублируем.
            _mark_no_orderbook(tid, quiet=False)
            return None
        log.warning(f"Ошибка получения стакана через SDK: {e}. Пробую через HTTP...")

    try:
        r = requests.get(f"{HOST}/book", params={"token_id": tid}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {
                "bids": [{"price": float(b["price"]), "size": float(b["size"])} for b in data.get("bids", [])],
                "asks": [{"price": float(a["price"]), "size": float(a["size"])} for a in data.get("asks", [])]
            }
        if r.status_code == 404:
            _mark_no_orderbook(tid, quiet=False)
            return None
        log.warning(f"HTTP /book вернул {r.status_code} для {tid[:14]}…")
    except Exception as ex:
        log.error(f"HTTP ошибка получения стакана: {ex}")

    return None


# =========================================================
# BALANCE
# =========================================================

def get_balance() -> Optional[float]:
    try:
        if _client is None:
            return None

        data = _get_balance_allowance_safe(_client)

        if isinstance(data, dict):
            bal = data.get("balance")
            if bal is None:
                bal = data.get("collateral_token_balance", 0)
            return round(float(bal) / 1_000_000, 2)

        return None

    except Exception as e:
        log.warning(f"get_balance error: {e}")
        return None


# =========================================================
# ORDER PLACEMENT CORE
# =========================================================

def _resolve_order_type(order_type):
    """
    Возвращает (enum_or_none, "FOK"/"FAK"/"GTC"/"GTD").
    Работает даже если в установленной версии SDK нет OrderType.
    """
    name = str(order_type or "GTC").upper()
    if name not in ("GTC", "GTD", "FOK", "FAK"):
        name = "GTC"
    try:
        from py_clob_client_v2.clob_types import OrderType
        return getattr(OrderType, name), name
    except Exception:
        return None, name


def _get_tick_size(token_id) -> str:
    """Тик рынка. Нужен для корректной подписи ордера (FOK/FAK особенно чувствительны)."""
    global _client
    try:
        if _client is not None:
            ts = _client.get_tick_size(str(token_id))
            if ts:
                return str(ts)
    except Exception as e:
        log.debug(f"get_tick_size via SDK failed: {e}")
    try:
        r = requests.get(f"{HOST}/tick-size", params={"token_id": str(token_id)}, timeout=8)
        if r.status_code == 200:
            ts = r.json().get("minimum_tick_size")
            if ts:
                return str(ts)
    except Exception as e:
        log.debug(f"get_tick_size via HTTP failed: {e}")
    return "0.01"


def _build_partial_options(token_id, neg_risk=None):
    """PartialCreateOrderOptions(tick_size, neg_risk) — без него neg-risk рынки часто отбивают ордер."""
    try:
        from py_clob_client_v2.clob_types import PartialCreateOrderOptions
    except Exception as e:
        log.debug(f"PartialCreateOrderOptions unavailable: {e}")
        return None

    if neg_risk is None:
        info = get_market_info(token_id)
        neg_risk = bool(info.get("neg_risk")) if info else False

    try:
        return PartialCreateOrderOptions(tick_size=_get_tick_size(token_id), neg_risk=bool(neg_risk))
    except TypeError:
        try:
            return PartialCreateOrderOptions(tick_size=_get_tick_size(token_id))
        except Exception:
            return None


def _call_with_optional_kwargs(fn, *args, **kwargs):
    """Вызывает метод SDK, отбрасывая kwargs, которых нет в конкретной версии клиента."""
    try:
        return fn(*args, **kwargs)
    except TypeError as e:
        msg = str(e)
        dropped = False
        for key in ("options", "order_type"):
            if key in kwargs and key in msg:
                kwargs.pop(key)
                dropped = True
        if not dropped and kwargs:
            kwargs = {}
            dropped = True
        if not dropped:
            raise
        return fn(*args, **kwargs)


def _post_order_with_client(client, token_id, side: str, price: float, size: float, order_type="GTC"):
    """Лимитный ордер (шары по цене). order_type: GTC (в стакан) / FAK / FOK."""
    from py_clob_client_v2.clob_types import OrderArgsV2

    ot_enum, ot_name = _resolve_order_type(order_type)
    options = _build_partial_options(token_id)

    order_args = OrderArgsV2(
        token_id=str(token_id),
        price=price,
        size=size,
        side=side,
    )

    kwargs = {}
    if options is not None:
        kwargs["options"] = options
    if ot_enum is not None:
        kwargs["order_type"] = ot_enum

    try:
        result = _call_with_optional_kwargs(client.create_and_post_order, order_args, **kwargs)
        log.info(f"✅ Order placed (create_and_post_order, {ot_name}): {result}")
        return result
    except Exception as e:
        err1 = e
        log.warning(f"create_and_post_order failed: {e}")

    try:
        create_kwargs = {"options": options} if options is not None else {}
        order = _call_with_optional_kwargs(client.create_order, order_args, **create_kwargs)
        log.info(f"create_order result type: {type(order)}")
        try:
            maker = getattr(order, "maker", None)
            if maker:
                log.info(f"order.maker = {maker}")
        except:
            pass

        if ot_enum is not None:
            result = _call_with_optional_kwargs(client.post_order, order, order_type=ot_enum)
        else:
            result = client.post_order(order)
        log.info(f"✅ Order placed (create_order + post_order, {ot_name}): {result}")
        return result
    except Exception as e:
        err2 = e
        log.warning(f"create_order + post_order failed: {e}")

    raise err2 if 'err2' in locals() else err1


def _post_market_order_with_client(client, token_id, side: str, amount: float,
                                   worst_price: Optional[float], order_type="FOK"):
    """
    Настоящий рыночный ордер.
    amount: для BUY — сумма в USDC, для SELL — количество шар.
    worst_price: worst-price limit (защита от проскальзывания), НЕ целевая цена.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgs

    ot_enum, ot_name = _resolve_order_type(order_type)
    if ot_name not in ("FOK", "FAK"):
        ot_enum, ot_name = _resolve_order_type("FOK")

    options = _build_partial_options(token_id)

    args_kwargs = {
        "token_id": str(token_id),
        "amount": float(amount),
        "side": side,
    }
    if worst_price is not None:
        args_kwargs["price"] = float(worst_price)

    try:
        order_args = MarketOrderArgs(**args_kwargs)
    except TypeError:
        args_kwargs.pop("price", None)
        order_args = MarketOrderArgs(**args_kwargs)

    # Часть версий SDK требует order_type прямо в MarketOrderArgs
    try:
        if ot_enum is not None and hasattr(order_args, "order_type") and getattr(order_args, "order_type", None) is None:
            setattr(order_args, "order_type", ot_enum)
    except Exception:
        pass

    kwargs = {}
    if options is not None:
        kwargs["options"] = options
    if ot_enum is not None:
        kwargs["order_type"] = ot_enum

    try:
        result = _call_with_optional_kwargs(client.create_and_post_market_order, order_args, **kwargs)
        log.info(f"✅ Market order placed (create_and_post_market_order, {ot_name}): {result}")
        return result
    except Exception as e:
        err1 = e
        log.warning(f"create_and_post_market_order failed: {e}")

    try:
        create_kwargs = {"options": options} if options is not None else {}
        order = _call_with_optional_kwargs(client.create_market_order, order_args, **create_kwargs)
        if ot_enum is not None:
            result = _call_with_optional_kwargs(client.post_order, order, order_type=ot_enum)
        else:
            result = client.post_order(order)
        log.info(f"✅ Market order placed (create_market_order + post_order, {ot_name}): {result}")
        return result
    except Exception as e:
        err2 = e
        log.warning(f"create_market_order + post_order failed: {e}")

    raise err2 if 'err2' in locals() else err1


def _parse_fill(resp: dict, side: str) -> dict:
    """
    Разбирает ответ CLOB и определяет, был ли РЕАЛЬНЫЙ филл.
    makingAmount / takingAmount: для BUY making=USDC, taking=шары; для SELL наоборот.
    """
    order_id = (
        resp.get("orderID") or resp.get("orderId")
        or resp.get("order_id") or resp.get("id") or ""
    )
    status = str(resp.get("status") or resp.get("state") or "").lower()

    making = resp.get("makingAmount", resp.get("making_amount"))
    taking = resp.get("takingAmount", resp.get("taking_amount"))

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    making_f, taking_f = _f(making), _f(taking)

    filled_size = 0.0     # шары
    filled_cash = 0.0     # USDC
    avg_price = None

    if side.upper() == "BUY":
        filled_cash, filled_size = making_f, taking_f
    else:
        filled_size, filled_cash = making_f, taking_f

    if filled_size > 0:
        avg_price = round(filled_cash / filled_size, 4)

    filled = filled_size > 0 or status in ("matched", "filled", "complete", "completed")

    return {
        "orderID": order_id,
        "status": status,
        "matched": filled,
        "filled": filled,
        "filled_size": round(filled_size, 4),
        "filled_cash": round(filled_cash, 4),
        "avg_price": avg_price,
        "avg_price_cents": round(avg_price * 100, 1) if avg_price else None,
        "making": making,
        "taking": taking,
    }


# =========================================================
# ДИАГНОСТИКА КОШЕЛЬКА (CLOB V2 deposit wallet)
# =========================================================

WALLET_ERRORS = {
    "maker address not allowed": (
        "Кошелёк не допущен к торговле через API.\n"
        "CLOB V2 принимает ордера только от *депозит-кошелька* Polymarket. "
        "Голый EOA (signature_type=0) сервер отклоняет всегда.\n\n"
        "Что сделать:\n"
        "1. На polymarket.com → Deposit скопируйте адрес депозит-кошелька.\n"
        "2. Пропишите его в `POLY_FUNDER`.\n"
        "3. Поставьте `POLY_SIGNATURE_TYPE=3` (депозит-кошелёк) или `=2` (Gnosis Safe / MetaMask-прокси).\n"
        "4. Пересоздайте API-ключи (кнопка «Проверить API ключи»).\n"
        "5. Убедитесь, что залог лежит в *pUSD*, а не в USDC.e."
    ),
    "order signer address has to be": (
        "API-ключ выписан на подписанта (EOA), а ордер подписывается от имени "
        "депозит-кошелька — сервер требует, чтобы это был один адрес.\n\n"
        "Это известный баг `py-clob-client-v2` при signature_type=3. "
        "Пересоздайте API-ключи для того же кошелька; если не помогает — "
        "переключитесь на signature_type=2 с адресом прокси-кошелька в `POLY_FUNDER`."
    ),
    "not enough balance": (
        "Недостаточно средств или не выданы разрешения (allowance).\n"
        "В V2 залог должен быть в *pUSD*, а не в USDC.e, "
        "и должны быть одобрены контракты V2-биржи."
    ),
    "invalid signature": (
        "Подпись не принята. Обычно это рассинхрон часов сервера (>60с) "
        "или устаревший клиент. Проверьте NTP и версию py-clob-client-v2."
    ),
}


def classify_order_error(err_text: str):
    """Понятное объяснение для типовых отказов CLOB. None — если не распознали."""
    low = (err_text or "").lower()
    for needle, explain in WALLET_ERRORS.items():
        if needle in low:
            return explain
    return None


def _is_fatal_wallet_error(err_text: str) -> bool:
    """
    Такие ошибки не зависят от signature_type: перебирать варианты бессмысленно,
    только теряем секунды (критично при аварийном выходе из позиции).
    """
    low = (err_text or "").lower()
    return ("maker address not allowed" in low
            or "order signer address has to be" in low)


def wallet_diagnostics() -> dict:
    """Сводка по конфигурации кошелька для меню «Диагностика»."""
    pk = _normalize_pk(_get_env("POLY_PRIVATE_KEY"))
    funder = _get_env("POLY_FUNDER").strip()
    sig = _get_int_env("POLY_SIGNATURE_TYPE", 1)

    eoa = None
    if pk:
        try:
            eoa = Account.from_key(pk).address
        except Exception:
            eoa = None

    problems = []
    if not pk:
        problems.append("не задан POLY_PRIVATE_KEY")
    if not funder:
        problems.append("не задан POLY_FUNDER — без него бот подписывает от голого EOA, "
                        "а CLOB V2 такие ордера отклоняет")
    elif not _is_valid_eth_address(funder):
        problems.append("POLY_FUNDER не похож на адрес (0x + 40 символов)")
    elif eoa and funder.lower() == eoa.lower():
        problems.append("POLY_FUNDER совпадает с адресом подписанта — проверьте, что это "
                        "именно адрес кошелька аккаунта из профиля polymarket.com")
    if sig == 0 and funder:
        problems.append("POLY_SIGNATURE_TYPE=0 (голый EOA) при заданном funder — "
                        "поставьте 3 (депозит-кошелёк) или 2 (Gnosis Safe)")
    if sig in (1, 2, 3) and not funder:
        problems.append(f"POLY_SIGNATURE_TYPE={sig} требует POLY_FUNDER")
    if not (_get_env("POLY_API_KEY") and _get_env("POLY_API_SECRET") and _get_env("POLY_API_PASSPHRASE")):
        problems.append("не заполнены API-ключи (key / secret / passphrase)")

    balance = None
    try:
        balance = get_balance()
    except Exception:
        balance = None

    try:
        unified = _unified().status()
    except Exception as e:
        unified = {"installed": False, "error": str(e)}

    if unified.get("installed") and unified.get("wallet_type") == "DEPOSIT_WALLET":
        problems = [p for p in problems if "funder" not in p.lower()]

    return {
        "unified": unified,
        "eoa": eoa,
        "funder": funder or None,
        "signature_type": sig,
        "sig_name": {0: "EOA", 1: "Magic/email прокси", 2: "Gnosis Safe",
                     3: "депозит-кошелёк (1271)"}.get(sig, str(sig)),
        "has_creds": bool(_get_env("POLY_API_KEY")),
        "ready": is_ready(),
        "balance": balance,
        "problems": problems,
    }



# =========================================================
# ВЫБОР БЭКЕНДА ИСПОЛНЕНИЯ (py-clob-client-v2 / унифицированный SDK)
# =========================================================

_UNIFIED_FALLBACK = False   # включается автоматически после отказа кошелька


def _unified():
    import poly_unified as pu
    return pu


def _use_unified_first() -> bool:
    """Сразу идти через новый SDK: режим unified или уже был отказ кошелька."""
    try:
        pu = _unified()
    except Exception:
        return False
    if not pu.available():
        return False
    return pu.forced() or _UNIFIED_FALLBACK


def _unified_retry_allowed() -> bool:
    """Можно ли после отказа кошелька повторить через новый SDK."""
    try:
        pu = _unified()
    except Exception:
        return False
    return pu.available() and pu.enabled()


def _switch_to_unified(reason: str):
    global _UNIFIED_FALLBACK
    if not _UNIFIED_FALLBACK:
        _UNIFIED_FALLBACK = True
        log.warning(f"🔁 Переключаюсь на унифицированный SDK Polymarket: {reason}")


def unified_active() -> bool:
    return _UNIFIED_FALLBACK or _use_unified_first()


# =========================================================
# TRADING
# =========================================================

def _execute_with_sig_fallback(sender, side: str, log_label: str) -> dict:
    """
    Общий каркас отправки: перебор signature_type, обновление allowance,
    парсинг филла. sender(client) -> raw response.
    """
    global _client

    current_sig = _get_int_env("POLY_SIGNATURE_TYPE", 1)
    funder = _get_env("POLY_FUNDER").strip()

    sig_candidates = []
    for st in [current_sig, 3, 2, 1, 0]:
        if st in sig_candidates:
            continue
        if st in (1, 2, 3) and not funder:
            continue          # прокси-типы без funder бессмысленны
        sig_candidates.append(st)
    if not sig_candidates:
        sig_candidates = [0]

    last_error = None

    for st in sig_candidates:
        try:
            log.info(f"\U0001f504 [{log_label}] Пробую отправить ордер через sig_type={st}...")

            client = _build_client(st)

            try:
                _update_balance_allowance_safe(client)
            except Exception as e:
                log.warning(f"allowance update sig_type={st} failed: {e}")

            raw = sender(client)

            try:
                log.info(f"\U0001f4e9 RAW order response: {raw}")
            except Exception:
                pass

            _client = client
            update_env_and_config({"POLY_SIGNATURE_TYPE": str(st)})
            log.info(f"✅ Рабочий sig_type для ордера: {st}")

            resp = raw if isinstance(raw, dict) else _object_to_dict(raw)
            result = {"success": True, "raw": resp}
            result.update(_parse_fill(resp, side))
            return result

        except Exception as e:
            last_error = e
            err = _extract_error_text(e)
            log.warning(f"sig_type={st} order failed: {err}")

            if _is_fatal_wallet_error(err):
                # Отказ на уровне кошелька: другие signature_type дадут то же самое.
                # Не тратим секунды на перебор — это критично при аварийном выходе.
                log.error("⛔ Отказ на уровне кошелька — перебор sig_type прекращён")
                return {
                    "error": err,
                    "wallet_error": True,
                    "explain": classify_order_error(err),
                }
            continue

    err_text = _extract_error_text(last_error)
    return {"error": err_text, "explain": classify_order_error(err_text)}


def place_order(token_id, side: str, price: float, size: float, order_type: str = "GTC") -> dict:
    """
    Лимитный ордер: size шар по цене price.
    order_type="GTC" — кладётся в стакан (отложник),
    "FAK"/"FOK" — агрессивный лимитник, исполняется немедленно или отменяется.
    """
    if _use_unified_first():
        return _unified().place_order(token_id, side, price, size, order_type)

    try:
        if _client is None:
            return {"error": "Trading client not initialized"}

        side = "BUY" if side.upper() == "BUY" else "SELL"
        price = float(price)
        size = float(size)

        if price > 1:
            price = round(price / 100, 4)

        market_info = get_market_info(token_id)
        if market_info:
            if not market_info["accepting_orders"]:
                return {"error": "Market is closed or resolved"}
            if size < market_info["min_size"]:
                size = market_info["min_size"]
                log.info(f"Size adjusted to minimum: {size}")

        log.info(f"Placing {order_type} order: {side} {size}@{price} | token={token_id}")

        try:
            _update_balance_allowance_safe(_client)
            log.info("✅ Balance allowance updated")
        except Exception as e:
            log.warning(f"⚠️ Could not update allowance: {e}")

        res = _execute_with_sig_fallback(
            lambda client: _post_order_with_client(client, token_id, side, price, size, order_type),
            side,
            f"LIMIT-{str(order_type).upper()}",
        )
        if res.get("wallet_error") and _unified_retry_allowed():
            _switch_to_unified(res.get("error", "maker address not allowed"))
            return _unified().place_order(token_id, side, price, size, order_type)

        res["order_type"] = str(order_type).upper()
        res["requested_price"] = price
        res["requested_size"] = size
        return res

    except Exception as e:
        log.error(f"place_order error: {e}")
        return {"error": str(e)}


def place_market_order(token_id, side: str, amount: float,
                       worst_price: Optional[float] = None,
                       order_type: str = "FOK") -> dict:
    """
    НАСТОЯЩИЙ рыночный ордер по стакану.

    side="BUY"  -> amount в USDC (сколько долларов потратить)
    side="SELL" -> amount в шарах (сколько штук продать)
    worst_price -> худшая допустимая цена (slippage guard), 0..1 или центы
    order_type  -> "FOK" (всё или ничего) / "FAK" (сколько есть, остаток отменить)
    """
    if _use_unified_first():
        return _unified().place_market_order(token_id, side, amount, worst_price, order_type)

    try:
        if _client is None:
            return {"error": "Trading client not initialized"}

        side = "BUY" if side.upper() == "BUY" else "SELL"
        amount = float(amount)

        if amount <= 0:
            return {"error": "Amount must be > 0"}

        if worst_price is not None:
            worst_price = float(worst_price)
            if worst_price > 1:
                worst_price = round(worst_price / 100, 4)
            worst_price = max(0.001, min(0.999, worst_price))

        market_info = get_market_info(token_id)
        neg_risk = False
        if market_info:
            if not market_info["accepting_orders"]:
                return {"error": "Market is closed or resolved"}
            neg_risk = bool(market_info.get("neg_risk"))
            if side == "SELL" and amount < market_info["min_size"]:
                log.info(f"SELL amount {amount} меньше min_size {market_info['min_size']}")

        unit = "USDC" if side == "BUY" else "shares"
        log.info(
            f"Placing MARKET {order_type} order: {side} {amount} {unit} "
            f"| worst_price={worst_price} | neg_risk={neg_risk} | token={token_id}"
        )

        try:
            _update_balance_allowance_safe(_client)
        except Exception as e:
            log.warning(f"⚠️ Could not update allowance: {e}")

        res = _execute_with_sig_fallback(
            lambda client: _post_market_order_with_client(
                client, token_id, side, amount, worst_price, order_type
            ),
            side,
            f"MARKET-{str(order_type).upper()}",
        )
        if res.get("wallet_error") and _unified_retry_allowed():
            _switch_to_unified(res.get("error", "maker address not allowed"))
            return _unified().place_market_order(token_id, side, amount, worst_price, order_type)

        res["order_type"] = str(order_type).upper()
        res["requested_amount"] = amount
        res["worst_price"] = worst_price

        # FOK/FAK не должны оставлять висящий ордер: нет филла = сделки не было
        if res.get("success") and not res.get("filled"):
            res["success"] = False
            res["error"] = (
                f"{res['order_type']} не исполнен (нет ликвидности по цене не хуже "
                f"{worst_price if worst_price is not None else '—'}). Статус: {res.get('status') or 'unknown'}"
            )

        return res

    except Exception as e:
        log.error(f"place_market_order error: {e}")
        return {"error": str(e)}


# =========================================================
# ORDERS
# =========================================================

def get_open_orders() -> list:
    global _client

    def _normalize_collection(raw):
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in ("data", "orders", "open_orders", "results"):
                if key in raw:
                    val = raw[key]
                    if isinstance(val, list):
                        return val
                    if isinstance(val, dict):
                        return [val]
            return []
        return []

    try:
        if _client is None:
            return []

        try:
            raw = _client.get_open_orders()
        except Exception as e:
            log.warning(f"get_open_orders current client failed: {e}")
            current_sig = _get_int_env("POLY_SIGNATURE_TYPE", 1)
            _client = _build_client(current_sig)
            raw = _client.get_open_orders()

        items = _normalize_collection(raw)
        normalized = [_normalize_open_order(o) for o in items]
        normalized = [o for o in normalized if o.get("id")]
        return normalized

    except Exception as e:
        log.error(f"get_open_orders error: {e}")
        return []


def cancel_order(order_id: str) -> dict:
    global _client

    try:
        order_id = str(order_id or "").strip()
        if not order_id or order_id == "?":
            return {"error": "Invalid order id"}

        current_sig = _get_int_env("POLY_SIGNATURE_TYPE", 1)
        sig_candidates = []
        for st in [current_sig, 1, 2, 3, 0]:
            if st not in sig_candidates:
                sig_candidates.append(st)

        last_error = None

        for st in sig_candidates:
            try:
                log.info(f"🔄 Пробую отменить ордер {order_id} через sig_type={st}...")
                client = _build_client(st)

                attempts = []

                if hasattr(client, "cancel_order"):
                    attempts.append(("cancel_order(order_id)", lambda: client.cancel_order(order_id)))

                if hasattr(client, "cancel"):
                    attempts.append(("cancel(order_id)", lambda: client.cancel(order_id)))

                if hasattr(client, "cancel_orders"):
                    attempts.append(("cancel_orders([order_id])", lambda: client.cancel_orders([order_id])))
                    attempts.append(("cancel_orders({'order_ids':[order_id]})", lambda: client.cancel_orders({"order_ids": [order_id]})))
                    attempts.append(("cancel_orders(order_ids=[order_id])", lambda: client.cancel_orders(order_ids=[order_id])))

                for name, fn in attempts:
                    try:
                        res = fn()
                        log.info(f"cancel attempt {name} => {res}")

                        if _is_cancel_success(res, order_id):
                            _client = client
                            update_env_and_config({"POLY_SIGNATURE_TYPE": str(st)})
                            return {"success": True, "order_id": order_id, "result": res}

                    except Exception as e:
                        last_error = e
                        log.warning(f"{name} failed on sig_type={st}: {e}")

            except Exception as e:
                last_error = e
                log.warning(f"cancel build_client failed on sig_type={st}: {e}")

        return {"error": str(last_error) if last_error else "Cancel failed"}

    except Exception as e:
        return {"error": str(e)}


def cancel_all() -> dict:
    try:
        if _client is None:
            return {"error": "Not initialized"}
        return _client.cancel_all()
    except Exception as e:
        return {"error": str(e)}


# =========================================================
# CONFIG UPDATE
# =========================================================

def update_env_and_config(updates: dict) -> bool:
    try:
        for key, value in updates.items():
            setattr(cfg, key, str(value))
            os.environ[key] = str(value)

        env_path = os.path.join(cfg.BASE_DIR, ".env")
        env_dict = {}

        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        env_dict[k.strip()] = v.strip()

        for k, v in updates.items():
            env_dict[k] = str(v)

        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in env_dict.items():
                f.write(f"{k}={v}\n")

        log.info(f"✅ Config updated: {list(updates.keys())}")
        return True

    except Exception as e:
        log.error(f"update_env_and_config error: {e}")
        return False


# =========================================================
# AUTO GENERATE API KEYS
# =========================================================

def auto_generate_polymarket_keys(private_key: str) -> Optional[dict]:
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    try:
        account = Account.from_key(private_key)
        address = account.address
        log.info(f"🔑 Генерирую API ключи для EOA: {address}")

        def build_headers(method: str, path: str, body: str = "") -> dict:
            timestamp = str(int(time.time()))
            nonce = "0"
            message = timestamp + method.upper() + path + (body or "")
            msg = encode_defunct(text=message)
            signed = account.sign_message(msg)
            sig = signed.signature.hex()
            if not sig.startswith("0x"):
                sig = "0x" + sig
            log.info(f"L1 header: addr={address}, ts={timestamp}, method={method}, path={path}")
            return {
                "POLY_ADDRESS": address,
                "POLY_SIGNATURE": sig,
                "POLY_TIMESTAMP": timestamp,
                "POLY_NONCE": nonce,
                "Content-Type": "application/json",
            }

        def extract_creds(resp) -> Optional[dict]:
            if resp is None:
                return None
            if isinstance(resp, dict):
                k = resp.get("apiKey") or resp.get("api_key")
                s = resp.get("secret") or resp.get("api_secret")
                p = resp.get("passphrase") or resp.get("api_passphrase")
            else:
                k = getattr(resp, "api_key", None) or getattr(resp, "apiKey", None)
                s = getattr(resp, "api_secret", None) or getattr(resp, "secret", None)
                p = getattr(resp, "api_passphrase", None) or getattr(resp, "passphrase", None)

            if k and s and p:
                return {
                    "POLY_API_KEY": str(k),
                    "POLY_API_SECRET": str(s),
                    "POLY_API_PASSPHRASE": str(p),
                }
            return None

        body = json.dumps({"nonce": 0})
        headers = build_headers("POST", "/auth/api-key", body)
        r = requests.post(f"{HOST}/auth/api-key", headers=headers, data=body, timeout=15)
        log.info(f"POST /auth/api-key → {r.status_code}: {r.text[:400]}")
        if r.status_code in (200, 201):
            result = extract_creds(r.json())
            if result:
                log.info("✅ Способ 1: новые ключи созданы!")
                return result

        headers = build_headers("GET", "/auth/derive-api-key")
        r = requests.get(
            f"{HOST}/auth/derive-api-key",
            headers=headers,
            params={"nonce": 0},
            timeout=15,
        )
        log.info(f"GET /auth/derive-api-key → {r.status_code}: {r.text[:400]}")
        if r.status_code == 200:
            result = extract_creds(r.json())
            if result:
                log.info("✅ Способ 2: существующие ключи получены!")
                return result

        log.info("Пробую способ 3: ClobClient.create_api_key()...")
        try:
            temp_client = _build_client(0)
            resp = temp_client.create_api_key(nonce=0)
            log.info(f"create_api_key → {resp}")
            result = extract_creds(resp)
            if result:
                log.info("✅ Способ 3: ключи через create_api_key()")
                return result
        except Exception as e3:
            log.error(f"Способ 3 failed: {e3}")

        log.info("Пробую способ 4: ClobClient.create_or_derive_api_key()...")
        try:
            temp_client = _build_client(0)
            resp = temp_client.create_or_derive_api_key(nonce=0)
            log.info(f"create_or_derive_api_key → {resp}")
            result = extract_creds(resp)
            if result:
                log.info("✅ Способ 4: ключи через create_or_derive_api_key()")
                return result
        except Exception as e4:
            log.error(f"Способ 4 failed: {e4}")

        log.info("Пробую способ 5: ClobClient.derive_api_key()...")
        try:
            temp_client = _build_client(0)
            resp = temp_client.derive_api_key(nonce=0)
            log.info(f"derive_api_key → {resp}")
            result = extract_creds(resp)
            if result:
                log.info("✅ Способ 5: ключи через derive_api_key()")
                return result
        except Exception as e5:
            log.error(f"Способ 5 failed: {e5}")

        log.error("❌ Все способы не сработали")
        return None

    except Exception as e:
        log.error(f"auto_generate_polymarket_keys fatal: {e}")
        return None