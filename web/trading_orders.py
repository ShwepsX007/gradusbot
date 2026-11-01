"""One-shot CLOB manual orders. An uncertain outcome is never retried automatically."""
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation

import database as db
from web import trading_account
from py_clob_client_v2.clob_types import OrderArgsV2, PartialCreateOrderOptions, OrderPayload, OrderType


class TradeError(Exception):
    pass


def experimental_allowed():
    return os.environ.get('WEB_EXPERIMENTAL_MANUAL_ORDERS') == '1'


def enabled():
    if not experimental_allowed(): return False
    with db.get_db() as conn:
        row=conn.execute('SELECT enabled FROM web_trading_control WHERE id=1').fetchone()
        return bool(row and row['enabled'])


def set_enabled(on):
    if on and not experimental_allowed():
        raise TradeError('Экспериментальная торговля заблокирована на сервере: модуль не завершён и не проверен на реальной бирже.')
    with db.get_db() as conn:
        conn.execute('UPDATE web_trading_control SET enabled=? WHERE id=1',(1 if on else 0,))


def parse_decimal(value):
    if not isinstance(value, (str,int,float)) or isinstance(value,bool) or len(str(value))>24:
        raise TradeError('Неверная цена или количество.')
    try:
        number=Decimal(str(value))
        if not number.is_finite(): raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        raise TradeError('Неверная цена или количество.') from None


def _list(value):
    try: value=json.loads(value) if isinstance(value,str) else value
    except ValueError: return []
    return value if isinstance(value,list) else []


def market_asset(event, asset, selection):
    if not isinstance(event,dict) or event.get('closed') is not False or event.get('active') is not True:
        raise TradeError('Событие закрыто или его статус не подтверждён.')
    for m in event.get('markets',[]):
        if not isinstance(m,dict): continue
        # V2 position identifiers require a different routing path; never trade
        # by guessing from legacy token IDs.
        if str(m.get('version','')).lower() in ('v2','2') or m.get('positionIds'): continue
        if m.get('closed') is not False or m.get('active') is not True or m.get('acceptingOrders') is not True: continue
        outcomes=_list(m.get('outcomes'));ids=_list(m.get('clobTokenIds'))
        if len(outcomes)!=2 or len(ids)!=2 or not all(isinstance(x,str) for x in outcomes): continue
        names=[x.strip().lower() for x in outcomes]
        if sorted(names)!=['no','yes']: continue
        yes,no=str(ids[names.index('yes')]),str(ids[names.index('no')])
        if yes==no or not all(re.fullmatch(r'[0-9]{1,80}',i) for i in (yes,no)): continue
        if {'yes':yes,'no':no}.get(selection)==asset:
            return m
    raise TradeError('Исход не подтверждён правилами открытого рынка или приём заявок остановлен.')


def _reserve(data, wallet):
    now=time.time()
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT * FROM web_order_intents WHERE nonce=?',(data['nonce'],)).fetchone()
        if existing:
            # Replays return the recorded status and can never submit a second order.
            return dict(existing),False
        if not experimental_allowed(): raise TradeError('Экспериментальная торговля заблокирована на сервере.')
        control=conn.execute('SELECT enabled FROM web_trading_control WHERE id=1').fetchone()
        if not control or not control['enabled']: raise TradeError('Торговля выключена в настройках.')
        unknown=conn.execute("SELECT 1 FROM web_order_intents WHERE wallet=? AND asset=? AND state IN ('pending','unknown')",(wallet,data['asset'])).fetchone()
        if unknown: raise TradeError('По этому исходу есть заявка с неизвестным результатом. Сверьте её на Polymarket; повторная отправка заблокирована.')
        conn.execute('''INSERT INTO web_order_intents(nonce,wallet,slug,asset,side,price,size,order_type,state,created,updated)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                     (data['nonce'],wallet,data['slug'],data['asset'],data['side'],str(data['price']),str(data['size']),data['order_type'],'pending',now,now))
    return None,True


def _finish(nonce,state,order_id=None):
    with db.get_db() as conn:
        conn.execute('UPDATE web_order_intents SET state=?,order_id=?,updated=? WHERE nonce=?',
                     (state,order_id,time.time(),nonce))


def public_intent(row):
    return {k:row[k] for k in ('nonce','slug','asset','side','price','size','order_type','state','order_id','created','updated')}


def find_intent(wallet, nonce):
    if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce): return None
    with db.get_db() as conn:
        row=conn.execute('SELECT * FROM web_order_intents WHERE wallet=? AND nonce=?',(wallet,nonce)).fetchone()
    return public_intent(dict(row)) if row else None


def list_intents(wallet):
    with db.get_db() as conn:
        rows=conn.execute("SELECT * FROM web_order_intents WHERE wallet=? ORDER BY CASE WHEN state IN ('pending','unknown') THEN 0 ELSE 1 END, created DESC LIMIT 100",(wallet,)).fetchall()
    return [public_intent(dict(r)) for r in rows]


def submit(body, event):
    if not isinstance(body,dict) or body.get('confirm') is not True: raise TradeError('Подтвердите отправку ордера.')
    nonce,slug,asset,selection=body.get('nonce'),body.get('slug'),body.get('asset'),body.get('selection')
    if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce): raise TradeError('Неверный идентификатор заявки.')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{1,200}',slug): raise TradeError('Неверное событие.')
    if not isinstance(asset,str) or not re.fullmatch(r'[0-9]{1,80}',asset) or selection not in ('yes','no'):
        raise TradeError('Неверный исход.')
    side,kind=body.get('side'),body.get('order_type')
    if side not in ('BUY','SELL') or kind not in ('GTC','FAK'): raise TradeError('Неверный тип ордера.')
    if body.get('sl') or body.get('tp'): raise TradeError('SL/TP ещё не реализованы. Ордер не отправлен.')
    price,size=parse_decimal(body.get('price')),parse_decimal(body.get('size'))
    if not 0<price<1 or not 0<size<=1000000 or size != size.quantize(Decimal('.01')):
        raise TradeError('Цена должна быть от 0 до 1, объём — положительным с точностью до 0,01 контракта.')
    market_asset(event,asset,selection)
    c=trading_account.load()
    if not c: raise TradeError('Сначала настройте аккаунт Polymarket.')
    wallet=c['funder'] or c['signer']
    data={'nonce':nonce,'slug':slug,'asset':asset,'side':side,'price':price,'size':size,'order_type':kind}
    client=trading_account.build_client(c)
    try:
        book=client.get_order_book(asset)
        if not isinstance(book,dict) or str(book.get('asset_id'))!=asset: raise ValueError()
        tick=parse_decimal(book.get('tick_size'));minimum=parse_decimal(book.get('min_order_size'))
        if tick not in (Decimal('.1'),Decimal('.01'),Decimal('.005'),Decimal('.0025'),Decimal('.001'),Decimal('.0001')) or minimum<=0:
            raise ValueError()
        if price%tick or price<tick or price>1-tick: raise TradeError('Цена не соответствует шагу рынка.')
        if size<minimum: raise TradeError('Количество меньше минимума рынка; оно не увеличивается автоматически.')
        if not isinstance(book.get('neg_risk'),bool): raise ValueError()
    except TradeError: raise
    except Exception: raise TradeError('Не удалось проверить параметры стакана. Ордер не отправлен.') from None
    prior,created=_reserve(data,wallet)
    if not created: return public_intent(prior)
    try:
        order=client.create_order(OrderArgsV2(token_id=asset,side=side,price=float(price),size=float(size)),
              PartialCreateOrderOptions(tick_size=format(tick.normalize(),'f'),neg_risk=book['neg_risk']))
    except Exception:
        _finish(nonce,'not_sent')
        raise TradeError('Не удалось подготовить подпись. Ордер не отправлен.') from None
    # From this point even a network exception is an unknown outcome. Never retry.
    try:
        result=client.post_order(order,OrderType.GTC if kind=='GTC' else OrderType.FAK)
    except Exception:
        _finish(nonce,'unknown')
        return {'nonce':nonce,'state':'unknown','order_id':None}
    if isinstance(result,dict) and result.get('success') is True and isinstance(result.get('orderID'),str) and result['orderID']:
        _finish(nonce,'accepted',result['orderID'])
        return {'nonce':nonce,'state':'accepted','order_id':result['orderID']}
    # Even rejected/malformed results are conservative: do not infer no submission.
    _finish(nonce,'unknown')
    return {'nonce':nonce,'state':'unknown','order_id':None}


def cancel(order_id):
    # Emergency cancellation stays available even when new orders are disabled.
    if not isinstance(order_id,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',order_id): raise TradeError('Неверный идентификатор ордера.')
    c=trading_account.load()
    if not c: raise TradeError('Аккаунт не настроен.')
    client=trading_account.build_client(c)
    try:
        order=client.get_order(order_id)
        if not isinstance(order,dict) or str(order.get('id','')).lower()!=order_id.lower():
            raise TradeError('Ордер не подтверждён CLOB для этого аккаунта.')
    except TradeError: raise
    except Exception: raise TradeError('Не удалось проверить ордер. Отмена не отправлена.') from None
    try:
        response=client.cancel_order(OrderPayload(orderID=order_id))
        canceled=response.get('canceled',[]) if isinstance(response,dict) else []
        if isinstance(canceled,list) and order_id.lower() in [str(x).lower() for x in canceled]:
            return {'state':'canceled'}
    except Exception: pass
    return {'state':'unknown'}
