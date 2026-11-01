"""One-shot CLOB manual orders. An uncertain outcome is never retried automatically."""
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation

import database as db
from web import trading_account, order_reconciliation as reconciliation
from py_clob_client_v2.clob_types import OrderArgsV2, PartialCreateOrderOptions, OrderPayload, OrderType, AssetType, BalanceAllowanceParams


class TradeError(Exception):
    pass


def enabled():
    with db.get_db() as conn:
        row=conn.execute('SELECT enabled FROM web_trading_control WHERE id=1').fetchone()
        return bool(row and row['enabled'])


def set_enabled(on):
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
        control=conn.execute('SELECT enabled FROM web_trading_control WHERE id=1').fetchone()
        if not control or not control['enabled']: raise TradeError('Торговля выключена в настройках.')
        blocked=conn.execute("""SELECT 1 FROM web_order_intents i LEFT JOIN web_order_reconciliation r ON r.nonce=i.nonce
            WHERE lower(i.wallet)=lower(?) AND i.asset=? AND
            (i.state IN ('pending','unknown') OR (i.state='accepted' AND (r.assessment IS NULL OR r.assessment!='terminal'))) LIMIT 1""",(wallet,data['asset'])).fetchone()
        if blocked: raise TradeError('По этому исходу есть незавершённая или несверенная заявка. Новая отправка заблокирована; проверьте журнал и Polymarket.')
        conn.execute('''INSERT INTO web_order_intents(nonce,wallet,slug,asset,side,price,size,order_type,state,created,updated,purpose)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (data['nonce'],wallet,data['slug'],data['asset'],data['side'],str(data['price']),str(data['size']),data['order_type'],'pending',now,now,data['purpose']))
    return None,True


def _finish(nonce,state,order_id=None):
    with db.get_db() as conn:
        conn.execute("UPDATE web_order_intents SET state=?,order_id=?,updated=? WHERE nonce=? AND state='pending'",
                     (state,order_id,time.time(),nonce))


def public_intent(row):
    return {k:row[k] for k in ('nonce','slug','asset','side','price','size','order_type','state','order_id','created','updated','purpose')}


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


def _check_take_profit_balance(client, credentials, asset, size):
    """Refresh and check the conditional-token balance. This is not an atomic reservation."""
    params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL,token_id=asset,signature_type=credentials['signature_type'])
    try:
        client.update_balance_allowance(params)
        result=client.get_balance_allowance(params)
        value=result.get('balance') if isinstance(result,dict) else None
        if isinstance(value,bool) or not isinstance(value,(str,int)) or not re.fullmatch(r'[0-9]{1,40}',str(value)):
            raise ValueError('invalid balance')
        balance=Decimal(value)/Decimal(1000000)
    except Exception:
        raise TradeError('Не удалось проверить баланс долей в CLOB. Тейк не отправлен.') from None
    if size>balance:
        raise TradeError('Запрошено больше долей, чем показывает баланс CLOB. Тейк не отправлен.')


def submit(body, event):
    if not isinstance(body,dict) or body.get('confirm') is not True: raise TradeError('Подтвердите отправку ордера.')
    nonce,slug,asset,selection=body.get('nonce'),body.get('slug'),body.get('asset'),body.get('selection')
    if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce): raise TradeError('Неверный идентификатор заявки.')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{1,200}',slug): raise TradeError('Неверное событие.')
    if not isinstance(asset,str) or not re.fullmatch(r'[0-9]{1,80}',asset) or selection not in ('yes','no'):
        raise TradeError('Неверный исход.')
    side,kind=body.get('side'),body.get('order_type')
    if side not in ('BUY','SELL') or kind not in ('GTC','FAK'): raise TradeError('Неверный тип ордера.')
    purpose=body.get('purpose','manual')
    if purpose not in ('manual','take_profit'): raise TradeError('Неверное назначение заявки.')
    if purpose=='take_profit' and (side!='SELL' or kind!='GTC' or body.get('confirm_take_profit') is not True):
        raise TradeError('Тейк для позиции — только подтверждённый лимитный SELL GTC.')
    if body.get('sl') or body.get('tp'): raise TradeError('SL и автоматическая привязка тейка к BUY не реализованы. Ордер не отправлен.')
    price,size=parse_decimal(body.get('price')),parse_decimal(body.get('size'))
    if not 0<price<1 or not 0<size<=1000000 or size != size.quantize(Decimal('.01')):
        raise TradeError('Цена должна быть от 0 до 1, объём — положительным с точностью до 0,01 контракта.')
    market_asset(event,asset,selection)
    c=trading_account.load()
    if not c: raise TradeError('Сначала настройте аккаунт Polymarket.')
    wallet=c['funder'] or c['signer']
    data={'nonce':nonce,'slug':slug,'asset':asset,'side':side,'price':price,'size':size,'order_type':kind,'purpose':purpose}
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
    if purpose=='take_profit': _check_take_profit_balance(client,c,asset,size)
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
        order=client.create_order(OrderArgsV2(token_id=asset,side=side,price=float(price),size=float(size)),
              PartialCreateOrderOptions(tick_size=format(tick.normalize(),'f'),neg_risk=book['neg_risk'],version=2))
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
