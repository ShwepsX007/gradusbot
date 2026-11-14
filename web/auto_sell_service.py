"""Opt-in real SELL worker. Never opens BUYs; one-shot durable side effects."""
import os
import secrets
import signal
import threading
import time
from decimal import Decimal, ROUND_DOWN
import requests
import database as db
from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
from web import auto_sell as a, trading_account as account, trading_orders as orders, order_reconciliation as recon

INTERVAL=10


def singleton():
    return a.worker_lock()


def heartbeat():
    with db.get_db() as c:
        c.execute('INSERT OR REPLACE INTO web_auto_sell_heartbeat(id,checked_at) VALUES(1,?)',(time.time(),))


def status(plan, reason, state=None):
    with db.get_db() as c:
        if state:
            c.execute('UPDATE web_auto_sell_plans SET reason=?,state=?,updated=? WHERE buy_nonce=? AND state=\'active\'',(reason,state,time.time(),plan['buy_nonce']))
        else:
            c.execute('UPDATE web_auto_sell_plans SET reason=?,updated=? WHERE buy_nonce=? AND state=\'active\'',(reason,time.time(),plan['buy_nonce']))


def intent(nonce):
    with db.get_db() as c:
        row=c.execute('SELECT * FROM web_order_intents WHERE nonce=?',(nonce,)).fetchone()
        evidence=c.execute('SELECT * FROM web_order_reconciliation WHERE nonce=?',(nonce,)).fetchone()
    return (dict(row) if row else None,dict(evidence) if evidence else None)


def inspect(client, credentials, nonce, *, prices=False):
    row,r=intent(nonce)
    if not row or row['state'] not in ('accepted','unknown','pending') or not r or r['assessment']=='conflict': raise ValueError('intent missing or conflict')
    expected=r['expected_id']
    if not expected or r['credential_fingerprint']!=recon.fingerprint(credentials) or (row['order_id'] and row['order_id'].lower()!=expected.lower()): raise ValueError('identity')
    fd=recon.acquire_order_lock(nonce)
    try:
        evidence=recon.inspect_order(client,row,credentials,expected,include_buy_prices=prices)
        with db.get_db() as c:
            c.execute("UPDATE web_order_intents SET state='accepted',order_id=?,updated=? WHERE nonce=? AND state IN ('pending','unknown')",(expected,time.time(),nonce))
        return row,evidence,expected
    finally:os.close(fd)


def conditional_balance(client, asset):
    params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL,token_id=asset)
    client.update_balance_allowance(params)
    raw=client.get_balance_allowance(params)
    value=raw.get('balance') if isinstance(raw,dict) else None
    if isinstance(value,bool) or not isinstance(value,(str,int)) or not str(value).isdigit() or len(str(value))>40: raise ValueError('balance')
    return Decimal(str(value))/Decimal(1000000)


def event(slug):
    r=requests.get('https://gamma-api.polymarket.com/events/slug/'+slug,timeout=(3,8),headers={'User-Agent':'gradusbot-web/1.0'})
    r.raise_for_status();data=r.json()
    return data if isinstance(data,dict) else None


def action_rows(nonce):
    with db.get_db() as c:
        rows=c.execute('SELECT * FROM web_auto_sell_actions WHERE buy_nonce=? ORDER BY created, rowid',(nonce,)).fetchall()
        retired={r['action_nonce'] for r in c.execute('SELECT action_nonce FROM web_auto_sell_retired WHERE buy_nonce=?',(nonce,))}
    return [dict(x) for x in rows],retired


def run_plan(plan, credentials, client):
    nonce=plan['buy_nonce']
    if plan['fingerprint']!=recon.fingerprint(credentials) or plan['wallet'].lower()!=(credentials['funder'] or credentials['signer']).lower():
        status(plan,'Учётные данные изменились. Проверьте ордера вручную.','halt');return
    row,r=intent(nonce)
    if row and row['state']=='not_sent': status(plan,'Покупка не отправлена; автопродажа остановлена.','done');return
    try:buyrow,buy,buy_id=inspect(client,credentials,nonce,prices=True)
    except Exception:status(plan,'Ждём достоверную сверку покупки и её сделок.');return
    actions,retired=action_rows(nonce)
    unresolved=next((x for x in actions if x['state'] in ('pending','unknown')),None)
    if unresolved:
        if unresolved['kind']=='submit':
            try:
                child,_,_=inspect(client,credentials,unresolved['action_nonce'])
                if child['asset']!=plan['asset'] or child['side']!='SELL': raise ValueError('child identity')
                a.record_resolution(unresolved['action_nonce'],'proved')
            except Exception: status(plan,'Результат отправки SELL неизвестен; повтор запрещён до подтверждения CLOB.');return
        else:
            child=next((x for x in actions if x['kind']=='submit' and x['state']=='proved' and intent(x['action_nonce'])[0] and intent(x['action_nonce'])[0].get('order_id')==unresolved['target_order']),None)
            if not child:status(plan,'Отмена SELL не сопоставлена; проверьте вручную.','halt');return
            try:
                _,ev,_=inspect(client,credentials,child['action_nonce'])
                if ev['assessment']!='terminal': raise ValueError('not terminal')
                a.record_resolution(unresolved['action_nonce'],'proved')
            except Exception:status(plan,'Ожидаем подтверждения отмены и расчёта SELL; повтор отмены запрещён.');return
        actions,retired=action_rows(nonce)
    open_children=[x for x in actions if x['kind']=='submit' and x['state']=='proved' and x['action_nonce'] not in retired]
    if len(open_children)>1:status(plan,'Несколько SELL в журнале; нужна ручная проверка.','halt');return
    current=None;child=None;child_evidence=None;child_id=None
    if open_children:
        child=open_children[0]
        try:
            child_row,child_evidence,child_id=inspect(client,credentials,child['action_nonce'])
            if child_row['asset']!=plan['asset'] or child_row['side']!='SELL':raise ValueError('identity')
        except Exception:status(plan,'Не удалось сверить выставленный SELL и сделки.');return
        cancel=any(x['kind']=='cancel' and x['target_order'].lower()==child_id.lower() and x['state']=='proved' for x in actions)
        current={'state':'accepted','size':child['size'],'price':child['price'],'cancel_state':'requested' if cancel else 'none','evidence':child_evidence}
    try:
        # A terminal BUY with all its confirmed shares already sold is finished.
        total_bought=a.number(buy['confirmed_size']);total_sold=a.confirmed_sold(nonce)
        if total_sold>total_bought:
            status(plan,'Проданный объём больше подтверждённых покупок; нужна ручная проверка.','halt');return
        if current is None and buy['assessment']=='terminal' and total_bought-total_sold<Decimal('.01'):
            status(plan,'Покупка завершена; остаток подтверждённых долей менее 0,01.','done');return
        book=client.get_order_book(plan['asset'])
        if not isinstance(book,dict) or str(book.get('asset_id'))!=plan['asset']:raise ValueError()
        tick=a.number(book['tick_size']);minimum=a.number(book['min_order_size'])
        decision=a.decide(buy=buy,prior_sold=a.confirmed_sold(nonce),current_sell=current,offset=plan['offset'],tick=tick,minimum=minimum,enabled=orders.enabled())
    except Exception:status(plan,'Не удалось проверить стакан или параметры рынка.');return
    what=decision['action']
    if what in ('wait','halt','paused'):
        status(plan,decision['reason'],'halt' if what=='halt' else None);return
    if what=='cancel' and current and child_evidence:
        # If a previous SELL was capped by the current balance, do not churn it
        # just because the original BUY size is larger than the wallet balance.
        try:
            bought=a.number(buy['confirmed_size']);cost=a.number(buy['confirmed_notional'])
            target=a.target_price(cost,bought,a.number(plan['offset']),tick)
            remaining=a.number(child['size'])-a.number(child_evidence['confirmed_size'])
            desired=((bought-a.confirmed_sold(nonce)-a.number(child_evidence['confirmed_size']))*100).to_integral_value(rounding=ROUND_DOWN)/100
            if target==a.number(child['price']) and desired>remaining:
                balance=conditional_balance(client,plan['asset'])
                possible=(balance*100).to_integral_value(rounding=ROUND_DOWN)/100
                if possible<=remaining:
                    status(plan,'SELL уже выставлен на доступное количество; ждём новые доли.');return
        except Exception:status(plan,'Не удалось проверить доступные доли перед изменением SELL.');return
    if what=='retire':
        try:a.retire_sale(child['action_nonce'],child_evidence)
        except Exception:status(plan,'Не удалось записать подтверждённый SELL.','halt');return
        status(plan,'SELL сверен; проверяем остаток.');return
    # Serialize against the OFF switch, then recheck the switch and credentials.
    guard=a.execution_lock()
    try:
        with db.get_db() as c:nowplan=c.execute('SELECT state FROM web_auto_sell_plans WHERE buy_nonce=?',(nonce,)).fetchone()
        if not nowplan or nowplan['state']!='active' or not orders.enabled() or account.load()!=credentials:return
        action_nonce=secrets.token_hex(16)
        if what=='cancel':
            a.reserve_action(nonce,action_nonce,decision,child_id)
            try:orders.cancel(child_id)
            except Exception:pass
            a.record_resolution(action_nonce,'unknown')
            status(plan,'Отмена отправлена; ждём её подтверждения и расчёта сделок.');return
        if what=='submit':
            balance=conditional_balance(client,plan['asset'])
            size=min(a.number(decision['size']),(balance*100).to_integral_value(rounding=ROUND_DOWN)/100)
            if size<minimum:
                status(plan,'Долей в снимке баланса меньше минимума рынка; повторим проверку.');return
            decision={**decision,'size':str(size)}
            e=event(plan['slug'])
            if not e:status(plan,'Не удалось проверить правила события.');return
            a.reserve_action(nonce,action_nonce,decision)
            body={'slug':plan['slug'],'asset':plan['asset'],'selection':plan['selection'],'side':'SELL','order_type':'GTC','price':decision['price'],'size':decision['size'],'nonce':action_nonce,'confirm':True}
            try:result=orders.submit(body,e)
            except Exception:
                # Only the intent journal can prove no POST occurred.
                child_row,_=intent(action_nonce)
                if child_row is None or child_row['state']=='not_sent':
                    a.record_resolution(action_nonce,'not_sent')
                    status(plan,'SELL не отправлен: проверка или подпись не прошла. Нужна ручная проверка.','halt')
                else:
                    a.record_resolution(action_nonce,'unknown')
                    status(plan,'SELL не подтверждён; проверяем журнал перед повтором.')
                return
            if result.get('state')=='accepted':a.record_resolution(action_nonce,'proved')
            else:a.record_resolution(action_nonce,'unknown')
            status(plan,'SELL отправлен; сверяем исполнение.' if result.get('state')=='accepted' else 'Результат SELL неизвестен; повтор запрещён.');return
    except Exception:
        status(plan,'Ошибка проверки или отправки; проверьте журнал и Polymarket.')
    finally:os.close(guard)


def poll_once():
    heartbeat()
    if not orders.enabled():return
    try:
        credentials=account.load()
        if not credentials:return
        client=account.build_client(credentials)
        with db.get_db() as c:
            plans=[dict(x) for x in c.execute("SELECT * FROM web_auto_sell_plans WHERE state='active' ORDER BY COALESCE(updated,0) LIMIT 20")]
        for plan in plans:
            try:run_plan(plan,credentials,client)
            except Exception:status(plan,'Ошибка обработки; проверка будет повторена.')
    except Exception:pass


def main():
    # Reuse the same database migrations as the web app, without starting a server.
    from web import app as webapp
    webapp.init_web_db()
    try:lock=singleton()
    except Exception:return 1
    stopped=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopped.set());signal.signal(signal.SIGINT,lambda *_:stopped.set())
    try:
        a.recover_interrupted_actions()
        while not stopped.is_set():
            started=time.monotonic();poll_once();stopped.wait(max(1,INTERVAL-(time.monotonic()-started)))
    finally:
        with db.get_db() as c:c.execute('DELETE FROM web_auto_sell_heartbeat WHERE id=1')
        os.close(lock)
    return 0

if __name__=='__main__':raise SystemExit(main())
