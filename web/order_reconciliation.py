"""Fail-closed order identity and read-only reconciliation; never submit orders."""
import hashlib
import fcntl
import os
import stat
from importlib.metadata import version
import json
import re
import time
from decimal import Decimal

import database as db
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.constants import POLYGON
from py_clob_client_v2.clob_types import TradeParams
from py_clob_client_v2.order_utils.exchange_order_builder_v2 import ExchangeOrderBuilderV2
from py_clob_client_v2.order_utils.model.order_data_v2 import SignedOrderV2
from web import trading_account


class ReconciliationError(Exception):
    pass


def fingerprint(credentials):
    return hashlib.sha256(json.dumps(credentials,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def acquire_order_lock(nonce):
    """A bounded set of process-shared locks; held through POST and its response.

    Lock files are never unlinked (unlinking a locked inode would split the lock).
    Collisions only serialize unrelated submissions. Crash releases the OS lock.
    """
    if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce): raise ValueError('nonce')
    directory=trading_account._private_directory(create=True)
    fd=os.open(os.path.join(directory,'order-shard-'+nonce[:2]+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1:
            raise OSError('unsafe order lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise


def order_hash(order, credentials, signer, *, neg_risk, asset, side, price, size):
    """Hash the exact signed CTF order locally and validate requested amounts.

    Fail closed for unsupported SDK order types. Only the hash, never the signed
    order or signature, is persisted. This does not prove the exchange accepted it.
    """
    if version('py-clob-client-v2')!='1.2.1' or not isinstance(order,SignedOrderV2) or not isinstance(neg_risk,bool):
        raise ReconciliationError('Не удалось проверить подписанную заявку; она не отправлена.')
    try:
        expected_signer=credentials['funder'] if credentials['signature_type']==3 else credentials['signer']
        wallet=credentials['funder'] or credentials['signer']
        tokens=Decimal(size)*Decimal(1000000)
        funds=Decimal(size)*Decimal(price)*Decimal(1000000)
        maker,taker=(tokens,funds) if side=='SELL' else (funds,tokens)
        if (str(order.tokenId)!=asset or str(order.maker).lower()!=wallet.lower()
            or str(order.signer).lower()!=expected_signer.lower()
            or int(order.signatureType)!=credentials['signature_type']
            or int(order.side)!=(1 if side=='SELL' else 0)
            or int(order.expiration)!=0 or Decimal(order.makerAmount)!=maker
            or Decimal(order.takerAmount)!=taker):
            raise ValueError('signed order parameters do not match')
        cfg=get_contract_config(POLYGON)
        # This route supports only V2 Exchange signatures over CTF token IDs.
        # Protocol V2 position IDs (Exchange V3) are rejected.
        builder=ExchangeOrderBuilderV2(cfg.neg_risk_exchange_v2 if neg_risk else cfg.exchange_v2,POLYGON,signer,domain_version='2')
        value=builder.build_order_hash(builder.build_order_typed_data(order))
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}',value): raise ValueError('invalid hash')
        return value.lower()
    except Exception:
        raise ReconciliationError('Не удалось проверить подписанную заявку; она не отправлена.') from None


def record_hash(nonce, expected_id, credentials):
    if not re.fullmatch(r'0x[0-9a-f]{64}',expected_id): raise ReconciliationError('Неверный хеш заявки.')
    with db.get_db() as conn:
        conn.execute('''INSERT INTO web_order_reconciliation(nonce,expected_id,credential_fingerprint,assessment)
            VALUES(?,?,?,'unverified')''',(nonce,expected_id,fingerprint(credentials)))


def _amount(v):
    if isinstance(v,bool) or not isinstance(v,(str,int,float)) or len(str(v))>40: raise ValueError('amount')
    n=Decimal(str(v))
    if not n.is_finite() or n<0 or n>Decimal('1000000'): raise ValueError('amount')
    return n


def _same(a,b):
    return isinstance(a,str) and isinstance(b,str) and a.lower()==b.lower()


def inspect_order(client, row, credentials, order_id):
    """Return a conservative settlement assessment for an exact order ID.

    Unknown/unavailable data, missing trades, inconsistencies and nonterminal
    settlement never authorize another order. A 404 is not proof of absence.
    """
    raw=client.get_order(order_id)
    if not isinstance(raw,dict) or not _same(raw.get('id'),order_id): raise ValueError('identity')
    if str(raw.get('asset_id',''))!=row['asset'] or str(raw.get('side','')).upper()!=row['side']: raise ValueError('asset/side')
    if not _same(raw.get('maker_address'),row['wallet']) or not _same(raw.get('owner'),credentials['api_key']): raise ValueError('owner')
    orig,matched=_amount(raw.get('original_size')),_amount(raw.get('size_matched'))
    if orig!=_amount(row['size']) or matched>orig or _amount(raw.get('price'))!=_amount(row['price']): raise ValueError('quantity/price')
    status=str(raw.get('status','')).upper()
    if status not in ('LIVE','DELAYED','UNMATCHED','MATCHED','CANCELED','CANCELLED') or (status=='MATCHED' and matched==0): raise ValueError('status')
    ids=raw.get('associate_trades')
    if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(x,str) or not x or len(x)>128 for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('trade list')
    confirmed=failed=accounted=Decimal(0);unsettled=False
    for trade_id in ids:
        trades=client.get_trades(TradeParams(id=trade_id),only_first_page=True)
        if not isinstance(trades,list) or len(trades)!=1 or not isinstance(trades[0],dict): raise ValueError('trade missing')
        trade=trades[0]
        if trade.get('id')!=trade_id: raise ValueError('trade id')
        if _same(trade.get('taker_order_id'),order_id):
            if str(trade.get('asset_id',''))!=row['asset'] or str(trade.get('side','')).upper()!=row['side'] or not _same(trade.get('owner'),credentials['api_key']): raise ValueError('taker identity')
            quantity=_amount(trade.get('size'))
        else:
            makers=trade.get('maker_orders')
            if not isinstance(makers,list): raise ValueError('maker list')
            matches=[m for m in makers if isinstance(m,dict) and _same(m.get('order_id'),order_id)]
            if len(matches)!=1: raise ValueError('maker order')
            maker=matches[0]
            if str(maker.get('asset_id',''))!=row['asset'] or str(maker.get('side','')).upper()!=row['side'] or not _same(maker.get('owner'),credentials['api_key']) or not _same(maker.get('maker_address'),row['wallet']): raise ValueError('maker identity')
            quantity=_amount(maker.get('matched_amount'))
        if quantity<=0: raise ValueError('trade amount')
        state=str(trade.get('status','')).upper().removeprefix('TRADE_STATUS_')
        if state=='CONFIRMED': confirmed+=quantity
        elif state=='FAILED': failed+=quantity
        elif state in ('MATCHED','MATCHED_NOT_BROADCASTED','MINED','RETRYING'): unsettled=True
        else: raise ValueError('trade status')
        accounted+=quantity
    if accounted!=matched: raise ValueError('incomplete trade data')
    terminal_order=status in ('CANCELED','CANCELLED') or (status=='MATCHED' and (row['order_type']=='FAK' or matched==orig))
    final=terminal_order and not unsettled
    return {'assessment':'terminal' if final else 'active', 'order_status':status,
            'size_matched':str(matched),'confirmed_size':str(confirmed),'failed_size':str(failed),
            'reason':'Сделки подтверждены или завершились ошибкой; сверяйте позицию.' if final else 'Ордер открыт или расчёт сделок ещё не завершён.'}


def reconcile_one(row, credentials, client=None):
    """Persist observation only; no retry, cancel or automatic order submission."""
    with db.get_db() as conn:
        existing=conn.execute('SELECT * FROM web_order_reconciliation WHERE nonce=?',(row['nonce'],)).fetchone()
    existing=dict(existing) if existing else {}
    if existing.get('assessment') in ('conflict','terminal'): return
    expected=existing.get('expected_id') or row.get('order_id')
    error=None
    if not isinstance(expected,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',expected):
        error='Нет надёжного идентификатора; требуется ручная сверка.'
    elif existing.get('credential_fingerprint') and existing['credential_fingerprint']!=fingerprint(credentials):
        error='Учётные данные изменены; требуется ручная сверка.'
    elif row.get('order_id') and not _same(row['order_id'],expected):
        error='Идентификаторы заявки не совпадают.'
    if error:
        evidence={'assessment':'unverified','reason':error}
    else:
        try:
            evidence=inspect_order(client or trading_account.build_client(credentials),row,credentials,expected)
        except Exception:
            evidence={'assessment':'unverified','reason':'Нет достоверного ответа об ордере и сделках; повторная отправка заблокирована.'}
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current=conn.execute('SELECT state,order_id,created FROM web_order_intents WHERE nonce=?',(row['nonce'],)).fetchone()
        current_recon=conn.execute('SELECT assessment FROM web_order_reconciliation WHERE nonce=?',(row['nonce'],)).fetchone()
        if not current or current['state'] not in ('pending','accepted','unknown') or (current_recon and current_recon['assessment']=='conflict'): return evidence
        # In-flight pending orders are not released until old enough and positively identified.
        if current['state']=='pending' and time.time()-current['created']<60: return evidence
        conn.execute("""INSERT INTO web_order_reconciliation(nonce,expected_id,credential_fingerprint,assessment,checked_at,order_status,size_matched,confirmed_size,failed_size,reason)
          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(nonce) DO UPDATE SET
          assessment=excluded.assessment,checked_at=excluded.checked_at,order_status=excluded.order_status,
          size_matched=excluded.size_matched,confirmed_size=excluded.confirmed_size,failed_size=excluded.failed_size,reason=excluded.reason""",
          (row['nonce'],expected,existing.get('credential_fingerprint') or fingerprint(credentials),evidence['assessment'],time.time(),
           evidence.get('order_status'),evidence.get('size_matched'),evidence.get('confirmed_size'),evidence.get('failed_size'),evidence['reason']))
        if current['state'] in ('pending','unknown') and evidence['assessment'] in ('terminal','active'):
            conn.execute("UPDATE web_order_intents SET state='accepted',order_id=?,updated=? WHERE nonce=? AND state IN ('pending','unknown')",(expected,time.time(),row['nonce']))
    return evidence


def reconcile_wallet(credentials, limit=3):
    wallet=credentials['funder'] or credentials['signer']
    with db.get_db() as conn:
        rows=conn.execute("""SELECT i.* FROM web_order_intents i LEFT JOIN web_order_reconciliation r ON i.nonce=r.nonce
          WHERE lower(i.wallet)=lower(?) AND (i.state IN ('accepted','unknown') OR (i.state='pending' AND i.created<? AND r.expected_id IS NOT NULL))
          AND (r.assessment IS NULL OR r.assessment NOT IN ('terminal','conflict'))
          ORDER BY COALESCE(r.checked_at,0) ASC,i.created ASC LIMIT ?""",(wallet,time.time()-60,limit)).fetchall()
    if not rows:return
    try: client=trading_account.build_client(credentials)
    except Exception:return
    for row in rows:
        try: fd=acquire_order_lock(row['nonce'])
        except (OSError,ValueError):
            # A long-running POST must not starve other unresolved orders.
            with db.get_db() as conn:
                conn.execute('UPDATE web_order_reconciliation SET checked_at=? WHERE nonce=?',(time.time(),row['nonce']))
            continue
        try: reconcile_one(dict(row),credentials,client)
        finally: os.close(fd)
