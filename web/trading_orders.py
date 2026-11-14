"""One-shot CLOB manual orders. An uncertain outcome is never retried automatically."""
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation

import database as db
from web import trading_account, order_reconciliation as reconciliation
from py_clob_client_v2.clob_types import OrderArgsV2, MarketOrderArgsV2, PartialCreateOrderOptions, OrderPayload, OrderType


class TradeError(Exception):
    pass


class UnknownOrders(TradeError):
    def __init__(self, nonces):
        self.nonces = nonces
        super().__init__('По этому исходу есть заявки с неизвестным результатом или ещё выполняется отправка. Проверьте Polymarket: новая заявка может создать повторную сделку.')


def enabled():
    with db.get_db() as conn:
        row=conn.execute('SELECT enabled FROM web_trading_control WHERE id=1').fetchone()
        return bool(row and row['enabled'])


def set_enabled(on):
    # Wait for any in-flight auto side effect before returning from OFF.
    from web import auto_sell
    fd=auto_sell.execution_lock()
    try:
        with db.get_db() as conn:
            conn.execute('UPDATE web_trading_control SET enabled=? WHERE id=1',(1 if on else 0,))
            if not on:
                conn.execute("UPDATE web_auto_sell_plans SET state='paused',reason='Торговля выключена; требуется ручное возобновление.',updated=? WHERE state='active'",(time.time(),))
    finally: os.close(fd)


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
            if existing['wallet'].lower()!=wallet.lower(): raise TradeError('Идентификатор заявки уже использован другим аккаунтом.')
            return dict(existing),False
        control=conn.execute('SELECT enabled FROM web_trading_control WHERE id=1').fetchone()
        if not control or not control['enabled']: raise TradeError('Торговля выключена в настройках.')
        unknown=[r['nonce'] for r in conn.execute("""SELECT nonce FROM web_order_intents
            WHERE lower(wallet)=lower(?) AND asset=? AND state IN ('pending','unknown') ORDER BY nonce""",(wallet,data['asset'])).fetchall()]
        acknowledged=data.get('acknowledged_unknown',[])
        if unknown and not set(unknown).issubset(set(acknowledged)):
            raise UnknownOrders(unknown)
        if data.get('auto_offset') is not None:
            beat=conn.execute('SELECT checked_at FROM web_auto_sell_heartbeat WHERE id=1').fetchone()
            from web import auto_sell
            if not beat or not -5 <= now-beat['checked_at'] <= 30 or not auto_sell.worker_running():
                raise TradeError('Служба автопродажи не работает или устарела. Покупка не отправлена.')
        conn.execute('''INSERT INTO web_order_intents(nonce,wallet,slug,asset,side,price,size,order_type,state,created,updated,purpose,execution_mode,acknowledged_unknown)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (data['nonce'],wallet,data['slug'],data['asset'],data['side'],str(data['price']),str(data['size']),data['order_type'],'pending',now,now,data['purpose'],data['execution_mode'],json.dumps(unknown)))
        if data.get('auto_offset') is not None:
            conn.execute('''INSERT INTO web_auto_sell_plans(buy_nonce,wallet,asset,offset,created,slug,selection,fingerprint,state,reason,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(data['nonce'],wallet.lower(),data['asset'],str(data['auto_offset']),now,data['slug'],data['selection'],data['fingerprint'],'active','Ожидание исполнения покупки.',now))
    return None,True


def _finish(nonce,state,order_id=None):
    with db.get_db() as conn:
        conn.execute("UPDATE web_order_intents SET state=?,order_id=?,updated=? WHERE nonce=? AND state='pending'",
                     (state,order_id,time.time(),nonce))


def public_intent(row):
    return {k:row[k] for k in ('nonce','slug','asset','side','price','size','order_type','state','order_id','created','updated','purpose','execution_mode','signed_size','signed_price')}


def find_intent(wallet, nonce):
    if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce): return None
    with db.get_db() as conn:
        row=conn.execute('SELECT * FROM web_order_intents WHERE wallet=? AND nonce=?',(wallet,nonce)).fetchone()
    return public_intent(dict(row)) if row else None


def list_intents(wallet):
    with db.get_db() as conn:
        rows=conn.execute("""SELECT i.* FROM web_order_intents i LEFT JOIN web_order_reconciliation r ON r.nonce=i.nonce
            WHERE lower(i.wallet)=lower(?) ORDER BY CASE WHEN i.state IN ('pending','unknown') OR
            (i.state='accepted' AND (r.assessment IS NULL OR r.assessment!='terminal')) THEN 0 ELSE 1 END,
            i.created DESC LIMIT 100""",(wallet,)).fetchall()
        nonces=[r['nonce'] for r in rows]
        evidence={}
        if nonces:
            placeholders=','.join('?' for _ in nonces)
            evidence={r['nonce']:dict(r) for r in conn.execute("SELECT nonce,expected_id,assessment,checked_at,order_status,size_matched,confirmed_size,failed_size,reason FROM web_order_reconciliation WHERE nonce IN ("+placeholders+")",nonces).fetchall()}
    result=[]
    for r in rows:
        item=public_intent(dict(r)); recon=evidence.get(item['nonce'])
        if recon:
            recon.pop('nonce',None);item['reconciliation']=recon
        result.append(item)
    return result


def annotate_order_statuses(intents, credentials, limit=3):
    """Best-effort individual order reads; never infer final settlement or mutate intents."""
    selected=[row for row in intents if row['state']=='accepted' and row.get('order_id')][:limit]
    if not selected: return intents
    try: client=trading_account.build_client(credentials)
    except Exception:
        for row in selected: row['exchange']={'available':False}
        return intents
    for row in selected:
        row['exchange']={'available':False}
        try:
            raw=client.get_order(row['order_id'])
            if not isinstance(raw,dict) or str(raw.get('id','')).lower()!=row['order_id'].lower() or str(raw.get('asset_id',''))!=row['asset']:
                continue
            status=raw.get('status')
            if not isinstance(status,str) or not re.fullmatch(r'[A-Za-z_]{1,32}',status): continue
            original=parse_decimal(raw.get('original_size'));matched=parse_decimal(raw.get('size_matched'))
            if original<=0 or matched<0 or matched>original: continue
            row['exchange']={'available':True,'status':status,'original_size':str(original),'size_matched':str(matched)}
        except Exception:
            pass
    return intents


def submit(body, event):
    if not isinstance(body,dict) or body.get('confirm') is not True: raise TradeError('Подтвердите отправку ордера.')
    nonce,slug,asset,selection=body.get('nonce'),body.get('slug'),body.get('asset'),body.get('selection')
    if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce): raise TradeError('Неверный идентификатор заявки.')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{1,200}',slug): raise TradeError('Неверное событие.')
    if not isinstance(asset,str) or not re.fullmatch(r'[0-9]{1,80}',asset) or selection not in ('yes','no'):
        raise TradeError('Неверный исход.')
    side,kind=body.get('side'),body.get('order_type')
    if side not in ('BUY','SELL') or kind not in ('GTC','FAK'): raise TradeError('Неверный тип ордера.')
    if kind=='FAK' and body.get('execution_mode')!='market': raise TradeError('Обновите страницу: рыночный FAK требует нового формата заявки.')
    if kind=='GTC' and body.get('execution_mode','limit')!='limit': raise TradeError('Неверный режим заявки.')
    if body.get('purpose','manual')!='manual' or body.get('sl') or body.get('tp'):
        raise TradeError('Дополнительные автоматические заявки не поддерживаются.')
    acknowledged=body.get('acknowledged_unknown',[])
    if not isinstance(acknowledged,list) or len(acknowledged)>1000 or any(not isinstance(x,str) or not re.fullmatch(r'[0-9a-f]{32}',x) for x in acknowledged):
        raise TradeError('Неверное подтверждение неизвестных заявок.')
    auto_offset=None
    if body.get('auto_sell_cents') not in (None,''):
        if side!='BUY': raise TradeError('Автопродажа задаётся только при покупке.')
        cents=parse_decimal(body['auto_sell_cents'])
        if not 0<cents<100 or cents != cents.quantize(Decimal('.01')): raise TradeError('Автопродажа: укажите от 0 до 100 центов с точностью 0,01.')
        auto_offset=cents/100
    price,size=parse_decimal(body.get('price')),parse_decimal(body.get('size'))
    if not 0<price<1 or not 0<size<=1000000 or size != size.quantize(Decimal('.01')):
        raise TradeError('Цена должна быть от 0 до 1, сумма или количество — положительным с точностью до 0,01.')
    market_asset(event,asset,selection)
    c=trading_account.load()
    if not c: raise TradeError('Сначала настройте аккаунт Polymarket.')
    wallet=c['funder'] or c['signer']
    data={'nonce':nonce,'slug':slug,'asset':asset,'side':side,'price':price,'size':size,'order_type':kind,'purpose':'manual','execution_mode':'market' if kind=='FAK' else 'limit','acknowledged_unknown':acknowledged,'auto_offset':auto_offset,'selection':selection,'fingerprint':reconciliation.fingerprint(c)}
    client=trading_account.build_client(c)
    try:
        book=client.get_order_book(asset)
        if not isinstance(book,dict) or str(book.get('asset_id'))!=asset: raise ValueError()
        tick=parse_decimal(book.get('tick_size'));minimum=parse_decimal(book.get('min_order_size'))
        if tick not in (Decimal('.1'),Decimal('.01'),Decimal('.005'),Decimal('.0025'),Decimal('.001'),Decimal('.0001')) or minimum<=0:
            raise ValueError()
        if price%tick or price<tick or price>1-tick: raise TradeError('Цена не соответствует шагу рынка.')
        if kind=='GTC' and size<minimum: raise TradeError('Количество меньше минимума рынка; оно не увеличивается автоматически.')
        if not isinstance(book.get('neg_risk'),bool): raise ValueError()
    except TradeError: raise
    except Exception: raise TradeError('Не удалось проверить параметры стакана. Ордер не отправлен.') from None
    try:
        lock_fd=reconciliation.acquire_order_lock(nonce)
    except (OSError,ValueError):
        previous=find_intent(wallet,nonce)
        if previous: return previous
        raise TradeError('Другая отправка или сверка ещё выполняется. Проверьте журнал перед новой попыткой.') from None
    try:
        return _submit_locked(data,wallet,client,c,book,tick)
    finally:
        os.close(lock_fd)


def _submit_locked(data,wallet,client,c,book,tick):
    nonce,asset,side,kind,price,size=(data[k] for k in ('nonce','asset','side','order_type','price','size'))
    prior,created=_reserve(data,wallet)
    if not created: return public_intent(prior)
    try:
        if getattr(client,'retry_on_error',None) is not False:
            raise ValueError('SDK retry must be disabled')
        options=PartialCreateOrderOptions(tick_size=format(tick.normalize(),'f'),neg_risk=book['neg_risk'],version=2)
        if data['execution_mode']=='market':
            # SDK rounds the derived amounts. If that rounding crosses the user's
            # protective bound, sign a one-tick stricter order, then verify again.
            sign_price=price
            order=client.create_market_order(MarketOrderArgsV2(token_id=asset,side=side,price=float(sign_price),amount=float(size),order_type=OrderType.FAK),options)
            if not reconciliation.market_price_safe(order,side,price):
                sign_price=price-tick if side=='BUY' else price+tick
                if sign_price<tick or sign_price>1-tick: raise ValueError('cannot preserve protective price')
                order=client.create_market_order(MarketOrderArgsV2(token_id=asset,side=side,price=float(sign_price),amount=float(size),order_type=OrderType.FAK),options)
            expected_id=reconciliation.order_hash(order,c,client.signer,neg_risk=book['neg_risk'],asset=asset,side=side,price=price,size=size,market=True)
            maker,taker=Decimal(order.makerAmount),Decimal(order.takerAmount)
            shares=(taker if side=='BUY' else maker)/Decimal(1000000)
            signed_price=(maker/taker if side=='BUY' else taker/maker)
            with db.get_db() as conn:
                conn.execute("UPDATE web_order_intents SET signed_size=?,signed_price=? WHERE nonce=? AND state='pending'",(str(shares),str(signed_price),nonce))
        else:
            order=client.create_order(OrderArgsV2(token_id=asset,side=side,price=float(price),size=float(size)),options)
            expected_id=reconciliation.order_hash(order,c,client.signer,neg_risk=book['neg_risk'],asset=asset,side=side,price=price,size=size)
        reconciliation.record_hash(nonce,expected_id,c)
        if trading_account.load()!=c: raise ValueError('account changed')
    except Exception:
        _finish(nonce,'not_sent')
        raise TradeError('Не удалось проверить подпись, параметры или версию SDK 1.2.1. Ордер не отправлен.') from None
    # From this point even a network exception is an unknown outcome. Never retry.
    try:
        result=client.post_order(order,OrderType.GTC if kind=='GTC' else OrderType.FAK)
    except Exception:
        _finish(nonce,'unknown')
        return {'nonce':nonce,'state':'unknown','order_id':None}
    if isinstance(result,dict) and result.get('success') is True and isinstance(result.get('orderID'),str) and result['orderID'].lower()==expected_id:
        _finish(nonce,'accepted',expected_id)
        return {'nonce':nonce,'state':'accepted','order_id':expected_id}
    if isinstance(result,dict) and result.get('success') is True and isinstance(result.get('orderID'),str) and result['orderID']:
        # A contradictory exchange ID is not safely recoverable by looking up either ID alone.
        with db.get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE web_order_intents SET state='unknown',order_id=NULL,updated=? WHERE nonce=?",(time.time(),nonce))
            conn.execute("UPDATE web_order_reconciliation SET assessment='conflict',reason=? WHERE nonce=?",('Идентификатор CLOB не совпал с локальным хешем. Требуется ручная проверка.',nonce))
        return {'nonce':nonce,'state':'unknown','order_id':None}
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
