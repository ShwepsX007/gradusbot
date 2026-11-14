"""Conservative planning for a future auto-sell service; this module never trades.

All decisions are pure and are made from already identity-checked CLOB order and
trade observations. Unknown/missing observations are not evidence of zero fills.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from fractions import Fraction
import re
import time
import database as db


class EvidenceError(ValueError):
    pass


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 50:
        raise EvidenceError('invalid amount')
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n < 0 or n > Decimal('10000000000') or n.as_tuple().exponent < -18:
            raise EvidenceError('invalid amount')
        return n
    except InvalidOperation:
        raise EvidenceError('invalid amount') from None


def fill(evidence, prices=False):
    if not isinstance(evidence, dict) or evidence.get('assessment') not in ('active', 'terminal'):
        raise EvidenceError('order not verified')
    matched=number(evidence.get('size_matched'))
    confirmed=number(evidence.get('confirmed_size'))
    failed=number(evidence.get('failed_size'))
    if confirmed+failed>matched:
        raise EvidenceError('inconsistent settlement')
    cost=number(evidence.get('confirmed_notional')) if prices else None
    if prices and ((confirmed==0 and cost!=0) or (confirmed>0 and not 0<cost<confirmed)):
        raise EvidenceError('inconsistent prices')
    return matched, confirmed, failed, cost


def target_price(cost, bought, offset, tick):
    # Integer arithmetic makes ceiling to tick exact, even just above a tick.
    q=(Fraction(cost)+Fraction(bought)*Fraction(offset))/(Fraction(bought)*Fraction(tick))
    return Decimal((q.numerator+q.denominator-1)//q.denominator)*tick


def decide(*, buy, prior_sold, current_sell, offset, tick, minimum, enabled=True):
    """Return one action, NEVER perform one.

    current_sell is None or contains the size and price sent, evidence from
    inspect_order, and a durable cancel_state: none, requested, or unknown.
    prior_sold is confirmed shares of all previous terminal auto-sells. The caller
    must independently check token balance, orders, credentials, durable journal,
    lock, and the trading switch immediately before any side effect.
    """
    if not enabled:
        return {'action':'paused', 'reason':'Торговля выключена; уже выставленный SELL не отменяется.'}
    try:
        bmatched, bought, bfailed, cost=fill(buy, True)
        sold=number(prior_sold)
        offset,tick,minimum=number(offset),number(tick),number(minimum)
        if not 0<offset<1 or tick not in tuple(map(Decimal, ('.1','.01','.005','.0025','.001','.0001'))) or minimum<=0 or sold>bought:
            raise EvidenceError('invalid parameters or sold exceeds bought')
        if current_sell is not None:
            if not isinstance(current_sell, dict) or current_sell.get('state') not in ('accepted','pending','unknown'):
                raise EvidenceError('invalid sell state')
            if current_sell['state']!='accepted':
                return {'action':'wait', 'reason':'Результат отправки SELL неизвестен; повторная отправка запрещена.'}
            smatched,sconfirmed,sfailed,_=fill(current_sell.get('evidence'))
            ssize,sprice=number(current_sell.get('size')),number(current_sell.get('price'))
            if ssize<=0 or not 0<sprice<1 or smatched>ssize or sold+sconfirmed>bought:
                raise EvidenceError('invalid sell amounts')
            cancel_state=current_sell.get('cancel_state','none')
            if cancel_state not in ('none','requested','unknown'):
                raise EvidenceError('invalid cancellation state')
            status=current_sell['evidence'].get('order_status')
            if status not in ('LIVE','DELAYED','UNMATCHED','MATCHED','CANCELED','CANCELLED'):
                raise EvidenceError('unknown sell status')
            terminal=current_sell['evidence']['assessment']=='terminal'
            if terminal:
                if smatched!=sconfirmed+sfailed:
                    raise EvidenceError('terminal sell unsettled')
                if status in ('CANCELED','CANCELLED') and cancel_state=='none':
                    return {'action':'halt','reason':'SELL отменён извне; автоматическое восстановление запрещено.'}
                return {'action':'retire','sold':str(sconfirmed)}
            if cancel_state!='none':
                return {'action':'wait','reason':'Отмена отправлена или её результат неизвестен; ждём подтверждения и расчёта сделок.'}
            if status not in ('LIVE','DELAYED','UNMATCHED','MATCHED'):
                raise EvidenceError('nonterminal status')
            if smatched != sconfirmed+sfailed or sfailed:
                return {'action':'wait','reason':'Ожидаем окончательный расчёт сопоставленных продаж.'}
            if bought==0: raise EvidenceError('sell without a confirmed buy')
            target=target_price(cost,bought,offset,tick)
            # A changed basis or new fills require cancel/reconcile before replacing.
            desired=((bought-sold-sconfirmed)*100).to_integral_value(rounding=ROUND_DOWN)/100
            remaining=ssize-sconfirmed
            if target!=sprice or desired!=remaining:
                return {'action':'cancel','reason':'Средний вход или объём изменился; сначала подтвердить отмену.'}
            return {'action':'wait','reason':'SELL уже выставлен; контролируем исполнение.'}
        if bought==0:
            return {'action':'wait','reason':'Подтверждённых покупок пока нет.'}
        target=target_price(cost,bought,offset,tick)
        if target>1-tick:
            return {'action':'halt','reason':'Цель превышает допустимую цену рынка; продажа не выставлена.'}
        remaining=((bought-sold)*100).to_integral_value(rounding=ROUND_DOWN)/100
        if remaining<minimum:
            return {'action':'wait','reason':'Подтверждённый остаток меньше минимума или точности биржи.'}
        return {'action':'submit','price':str(target),'size':str(remaining)}
    except (EvidenceError, InvalidOperation, ZeroDivisionError):
        return {'action':'halt', 'reason':'Данные ордеров или сделок неполные/противоречивые; проверьте вручную.'}


def init_journal():
    """Create a prototype journal; it is not attached to any live service."""
    with db.get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS web_auto_sell_plans (
            buy_nonce TEXT PRIMARY KEY, wallet TEXT NOT NULL, asset TEXT NOT NULL,
            offset TEXT NOT NULL, created REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_auto_sell_actions (
            action_nonce TEXT PRIMARY KEY, buy_nonce TEXT NOT NULL, kind TEXT NOT NULL,
            target_order TEXT, price TEXT, size TEXT, state TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL,
            FOREIGN KEY(buy_nonce) REFERENCES web_auto_sell_plans(buy_nonce))''')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS web_auto_sell_unresolved
            ON web_auto_sell_actions(buy_nonce) WHERE state IN ('pending','unknown')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS web_auto_sell_retired (
            action_nonce TEXT PRIMARY KEY, buy_nonce TEXT NOT NULL, confirmed_sold TEXT NOT NULL,
            recorded REAL NOT NULL, FOREIGN KEY(action_nonce) REFERENCES web_auto_sell_actions(action_nonce))''')


def add_plan(buy_nonce, wallet, asset, offset):
    if not isinstance(buy_nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',buy_nonce):
        raise EvidenceError('invalid buy nonce')
    if not isinstance(wallet,str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',wallet):
        raise EvidenceError('invalid wallet')
    if not isinstance(asset,str) or not re.fullmatch(r'[0-9]{1,80}',asset):
        raise EvidenceError('invalid token')
    offset=number(offset)
    if not 0<offset<1: raise EvidenceError('invalid offset')
    with db.get_db() as conn:
        conn.execute('INSERT INTO web_auto_sell_plans VALUES(?,?,?,?,?)',
                     (buy_nonce,wallet.lower(),asset,str(offset),time.time()))


def reserve_action(buy_nonce, action_nonce, decision, target_order=None):
    """Persist exactly one unresolved action BEFORE any eventual side effect.

    The journal is only groundwork: calling this function never sends an order
    or cancellation, and it alone does not authorize one. A real adapter must
    also serialize workers and inspect credentials, balances, and exchange state.
    """
    if not isinstance(action_nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',action_nonce):
        raise EvidenceError('invalid action nonce')
    kind=decision.get('action') if isinstance(decision,dict) else None
    if kind not in ('submit','cancel'): raise EvidenceError('not a side effect')
    price=size=None
    if kind=='submit':
        price,size=number(decision.get('price')),number(decision.get('size'))
        if not 0<price<1 or size<=0 or target_order is not None: raise EvidenceError('invalid order')
        price,size=str(price),str(size)
    elif not isinstance(target_order,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',target_order):
        raise EvidenceError('invalid cancellation target')
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        plan=conn.execute('SELECT buy_nonce FROM web_auto_sell_plans WHERE buy_nonce=?',(buy_nonce,)).fetchone()
        if not plan: raise EvidenceError('unknown plan')
        if conn.execute("SELECT 1 FROM web_auto_sell_actions WHERE buy_nonce=? AND state IN ('pending','unknown')",(buy_nonce,)).fetchone():
            raise EvidenceError('unresolved action')
        now=time.time()
        conn.execute('INSERT INTO web_auto_sell_actions VALUES(?,?,?,?,?,?,?,?,?)',
                     (action_nonce,buy_nonce,kind,target_order,price,size,'pending',now,now))


def recover_interrupted_actions():
    """After acquiring an exclusive worker lock, conservatively mark in-flight actions unknown.

    This function must not be run concurrently with a sender. No resend is made.
    """
    with db.get_db() as conn:
        conn.execute("UPDATE web_auto_sell_actions SET state='unknown',updated=? WHERE state='pending'",(time.time(),))


def record_resolution(action_nonce, resolution):
    """Persist a trusted adapter's result; this function does not verify CLOB evidence.

    'proved' means the caller has separately verified the exact order or cancel;
    'not_sent' requires positive local proof that no network submission occurred.
    A future live adapter must never infer either outcome from a 404 or timeout.
    """
    if resolution not in ('unknown','proved','not_sent'): raise EvidenceError('invalid resolution')
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT state FROM web_auto_sell_actions WHERE action_nonce=?',(action_nonce,)).fetchone()
        if not row or row['state'] not in ('pending','unknown'):
            raise EvidenceError('action already resolved or missing')
        if row['state']=='unknown' and resolution=='not_sent':
            raise EvidenceError('unknown result is not proof of non-submission')
        conn.execute('UPDATE web_auto_sell_actions SET state=?,updated=? WHERE action_nonce=?',
                     (resolution,time.time(),action_nonce))


def retire_sale(action_nonce, evidence):
    """Record a terminal SELL exactly once; no network calls or independent proof.

    The caller must supply fresh identity-checked order and trade evidence and
    distinguish expected cancellation from an external/manual cancellation.
    """
    matched,confirmed,failed,_=fill(evidence)
    if evidence.get('assessment')!='terminal' or matched != confirmed+failed:
        raise EvidenceError('sale not terminal')
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        action=conn.execute('SELECT * FROM web_auto_sell_actions WHERE action_nonce=?',(action_nonce,)).fetchone()
        if not action or action['kind']!='submit' or action['state']!='proved' or matched>number(action['size']):
            raise EvidenceError('unproven sale')
        previous=conn.execute('SELECT confirmed_sold FROM web_auto_sell_retired WHERE action_nonce=?',(action_nonce,)).fetchone()
        if previous:
            if number(previous['confirmed_sold'])!=confirmed: raise EvidenceError('conflicting terminal evidence')
            return
        conn.execute('INSERT INTO web_auto_sell_retired VALUES(?,?,?,?)',
                     (action_nonce,action['buy_nonce'],str(confirmed),time.time()))


def confirmed_sold(buy_nonce):
    """Sum immutable retired sells as Decimal, never SQLite floating point."""
    with db.get_db() as conn:
        rows=conn.execute('SELECT confirmed_sold FROM web_auto_sell_retired WHERE buy_nonce=?',(buy_nonce,)).fetchall()
    return sum((number(row['confirmed_sold']) for row in rows),Decimal(0))
