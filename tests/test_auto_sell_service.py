import os
import tempfile
import threading
import time
from werkzeug.security import generate_password_hash
import unittest
from unittest.mock import patch, Mock
from decimal import Decimal
from web import app as web, auto_sell as a, auto_sell_service as worker, trading_account as account, trading_orders as orders

KEY={'private_key':'0x'+'0'*63+'1','api_key':'key','api_secret':'secret','api_passphrase':'pass','signature_type':'0','funder':''}
EVENT={'title':'Test','slug':'test-event','active':True,'closed':False,'markets':[{'active':True,'closed':False,'acceptingOrders':True,'outcomes':'["No", "Yes"]','clobTokenIds':'["2", "1"]'}]}
BOOK={'asset_id':'1','tick_size':'0.01','min_order_size':'5','neg_risk':False}
BUY={'slug':'test-event','nonce':'a'*32,'asset':'1','selection':'yes','side':'BUY','order_type':'GTC','price':'0.90','size':'10','confirm':True,'auto_sell_cents':'5'}
BUY_ID='0x'+'1'*64
SELL_ID='0x'+'2'*64

class AutoSellWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(KEY)
        self.creds=account.load();self.client=Mock();self.client.retry_on_error=False;self.client.get_order_book.return_value=BOOK.copy();self.client.create_order.return_value='signed'
        self.client.post_order.side_effect=lambda order,kind: {'success':True,'orderID':BUY_ID if self.client.post_order.call_count==1 else SELL_ID}
        self.cp=patch.object(account,'build_client',return_value=self.client);self.cp.start()
        self.hp=patch.object(orders.reconciliation,'order_hash',side_effect=lambda *args,**kw:BUY_ID if kw['side']=='BUY' else SELL_ID);self.hp.start()
        self.ev=patch.object(worker,'event',return_value=EVENT);self.ev.start()
        self.bal=patch.object(worker,'conditional_balance',return_value=Decimal('10'));self.bal.start()
        self.buy={'assessment':'active','order_status':'LIVE','size_matched':'5','confirmed_size':'5','failed_size':'0','confirmed_notional':'4.5'}
        self.sell={'assessment':'active','order_status':'LIVE','size_matched':'0','confirmed_size':'0','failed_size':'0'}
        self.ip=patch.object(worker.recon,'inspect_order',side_effect=lambda client,row,cred,oid,**kw: dict(self.buy if row['side']=='BUY' else self.sell));self.ip.start()
        self.worker_lock=worker.singleton()
        orders.set_enabled(True);worker.heartbeat()
    def tearDown(self):
        os.close(self.worker_lock);self.ip.stop();self.bal.stop();self.ev.stop();self.hp.stop();self.cp.stop();self.p.stop();web.db.DB_FILE=self.old;self.tmp.cleanup()
    def plan(self):
        with web.db.get_db() as c:return dict(c.execute('SELECT * FROM web_auto_sell_plans WHERE buy_nonce=?',(BUY['nonce'],)).fetchone())
    def actions(self):
        with web.db.get_db() as c:return [dict(r) for r in c.execute('SELECT * FROM web_auto_sell_actions ORDER BY rowid')]
    def runplan(self):worker.run_plan(self.plan(),self.creds,self.client)
    def test_stale_heartbeat_cannot_substitute_for_worker_lock(self):
        os.close(self.worker_lock);self.worker_lock=None
        with self.assertRaises(orders.TradeError):orders.submit(BUY,EVENT)
        self.client.post_order.assert_not_called()
        self.worker_lock=worker.singleton()
    def test_buy_requires_healthy_worker_and_journals_atomically(self):
        with web.db.get_db() as c:c.execute('DELETE FROM web_auto_sell_heartbeat')
        with self.assertRaises(orders.TradeError):orders.submit(BUY,EVENT)
        self.client.post_order.assert_not_called();self.assertIsNone(orders.find_intent(self.creds['signer'],BUY['nonce']))
        worker.heartbeat();self.assertEqual(orders.submit(BUY,EVENT)['state'],'accepted')
        self.assertEqual(self.plan()['offset'],'0.05')
    def test_partial_fill_then_reprice_cancel_and_sell_remainder(self):
        orders.submit(BUY,EVENT);self.runplan()
        acts=self.actions();self.assertEqual(len(acts),1);self.assertEqual(acts[0]['state'],'proved')
        self.assertEqual((acts[0]['price'],acts[0]['size']),('0.95','5'))
        self.buy.update(size_matched='10',confirmed_size='10',confirmed_notional='8.8')
        self.sell.update(size_matched='2',confirmed_size='2')
        with patch.object(orders,'cancel',return_value={'state':'unknown'}) as cancel:
            self.runplan();cancel.assert_called_once_with(SELL_ID)
            self.runplan();self.assertEqual(cancel.call_count,1)
        self.assertEqual(self.actions()[1]['state'],'unknown')
        self.sell.update(assessment='terminal',order_status='CANCELED',size_matched='3',confirmed_size='3')
        self.runplan();self.assertEqual(a.confirmed_sold(BUY['nonce']),Decimal('3'))
        # New child has a different order hash in real SDK; mock a third one.
        third='0x'+'3'*64
        self.client.post_order.side_effect=lambda order,kind:{'success':True,'orderID':third}
        self.hp.stop();self.hp=patch.object(orders.reconciliation,'order_hash',return_value=third);self.hp.start()
        self.runplan();last=self.actions()[-1]
        self.assertEqual((last['kind'],last['price'],last['size']),('submit','0.93','7'))
    def test_unknown_sell_is_never_blindly_repeated(self):
        orders.submit(BUY,EVENT)
        self.client.post_order.side_effect=TimeoutError()
        self.runplan();self.assertEqual(self.actions()[0]['state'],'unknown')
        self.ip.stop();self.ip=patch.object(worker.recon,'inspect_order',side_effect=lambda client,row,cred,oid,**kw: dict(self.buy) if row['side']=='BUY' else (_ for _ in ()).throw(ValueError()));self.ip.start()
        for _ in range(3):self.runplan()
        self.assertEqual(len(self.actions()),1);self.assertEqual(self.client.post_order.call_count,2)
    def test_switch_off_pauses_without_cancel_and_requires_resume(self):
        orders.submit(BUY,EVENT);self.runplan()
        orders.set_enabled(False);self.assertEqual(self.plan()['state'],'paused')
        with patch.object(orders,'cancel') as cancel:
            worker.poll_once();cancel.assert_not_called()
        orders.set_enabled(True);self.assertEqual(self.plan()['state'],'paused')
    def test_recover_unknown_after_positive_exchange_identification(self):
        orders.submit(BUY,EVENT);self.client.post_order.side_effect=TimeoutError()
        self.runplan();self.assertEqual(self.actions()[0]['state'],'unknown')
        # If the exact expected hash later appears with verified identity, recover
        # the original SELL rather than posting another one.
        self.runplan();self.assertEqual(self.actions()[0]['state'],'proved')
        self.assertEqual(self.client.post_order.call_count,2)
    def test_recovery_after_crash_never_reposts_unresolved_action(self):
        orders.submit(BUY,EVENT);self.runplan()
        # Crash after the POST but before persisting the action result.
        with web.db.get_db() as c:c.execute("UPDATE web_auto_sell_actions SET state='pending'")
        a.recover_interrupted_actions();self.assertEqual(self.actions()[0]['state'],'unknown')
        self.runplan();self.assertEqual(self.actions()[0]['state'],'proved')
        self.assertEqual(self.client.post_order.call_count,2)

    def test_partial_balance_does_not_cancel_same_sell_forever(self):
        self.buy.update(size_matched='10',confirmed_size='10',confirmed_notional='9')
        self.bal.stop();self.bal=patch.object(worker,'conditional_balance',return_value=Decimal('6'));self.bal.start()
        orders.submit(BUY,EVENT);self.runplan()
        self.assertEqual(self.actions()[0]['size'],'6')
        with patch.object(orders,'cancel') as cancel:
            for _ in range(3):self.runplan()
            cancel.assert_not_called()
        self.assertEqual(len(self.actions()),1)
    def test_reduced_confirmed_buy_never_keeps_an_oversized_sell(self):
        self.buy.update(size_matched='10',confirmed_size='10',confirmed_notional='9')
        orders.submit(BUY,EVENT);self.runplan()
        self.buy.update(confirmed_size='6',confirmed_notional='5.4',failed_size='4')
        self.bal.stop();self.bal=patch.object(worker,'conditional_balance',return_value=Decimal('6'));self.bal.start()
        with patch.object(orders,'cancel',return_value={'state':'unknown'}) as cancel:
            self.runplan();cancel.assert_called_once_with(SELL_ID)

    def test_changed_account_is_halted(self):
        orders.submit(BUY,EVENT)
        worker.run_plan(self.plan(),{**self.creds,'api_key':'different'},self.client)
        self.assertEqual(self.plan()['state'],'halt');self.assertEqual(self.client.post_order.call_count,1)
    def test_terminal_sold_plan_finishes(self):
        orders.submit(BUY,EVENT);self.runplan()
        self.buy.update(assessment='terminal',order_status='MATCHED',size_matched='5',confirmed_size='5',confirmed_notional='4.5')
        self.sell.update(assessment='terminal',order_status='MATCHED',size_matched='5',confirmed_size='5')
        self.runplan();self.runplan();self.assertEqual(self.plan()['state'],'done')

    def test_empty_setting_leaves_buy_manual(self):
        with web.db.get_db() as c:c.execute('DELETE FROM web_auto_sell_heartbeat')
        self.assertEqual(orders.submit({**BUY,'auto_sell_cents':''},EVENT)['state'],'accepted')
        with web.db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_auto_sell_plans').fetchone()[0],0)
    def test_conditional_balance_is_token_specific_and_uses_base_units(self):
        self.bal.stop()
        self.client.get_balance_allowance.return_value={'balance':'6250000'}
        self.assertEqual(worker.conditional_balance(self.client,'1'),Decimal('6.25'))
        params=self.client.get_balance_allowance.call_args.args[0]
        self.assertEqual(params.asset_type,'CONDITIONAL');self.assertEqual(params.token_id,'1')
        for invalid in (None,True,'-1','NaN','1.5'):
            self.client.get_balance_allowance.return_value={'balance':invalid}
            with self.assertRaises(ValueError):worker.conditional_balance(self.client,'1')
        self.bal=patch.object(worker,'conditional_balance',return_value=Decimal('10'));self.bal.start()
    def test_off_waits_for_inflight_auto_operation(self):
        orders.submit(BUY,EVENT)
        started=threading.Event();release=threading.Event();disabled=threading.Event()
        def balance(client,asset):
            started.set();self.assertTrue(release.wait(5));return Decimal('10')
        self.bal.stop();self.bal=patch.object(worker,'conditional_balance',side_effect=balance);self.bal.start()
        processing=threading.Thread(target=self.runplan);processing.start();self.assertTrue(started.wait(5))
        off=threading.Thread(target=lambda:(orders.set_enabled(False),disabled.set()));off.start()
        self.assertFalse(disabled.wait(.1));release.set();processing.join(5);off.join(5)
        self.assertTrue(disabled.is_set());self.assertFalse(orders.enabled());self.assertEqual(self.plan()['state'],'paused')
        count=self.client.post_order.call_count;worker.poll_once();self.assertEqual(self.client.post_order.call_count,count)
    def test_ssh_api_pause_resume_and_no_auto_resume_on_enable(self):
        orders.submit(BUY,EVENT)
        config=dict(web.app.config)
        try:
            web.app.config.update(TESTING=True,WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('long password'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False)
            client=web.app.test_client()
            with client.session_transaction() as session:
                session['auth_user']='test';session['auth_version']=web.auth_version();session['csrf']='csrf'
            with patch.object(web,'trusted_ssh_tunnel',return_value=False):
                self.assertEqual(client.get('/api/trading/auto-sell').status_code,403)
            with patch.object(web,'trusted_ssh_tunnel',return_value=True):
                response=client.get('/api/trading/auto-sell');self.assertTrue(response.json['running'])
                headers={'X-CSRF-Token':'csrf'}
                response=client.post('/api/trading/auto-sell/pause',json={'nonce':BUY['nonce'],'confirm':True},headers=headers)
                self.assertEqual(response.status_code,200);self.assertEqual(self.plan()['state'],'paused')
                orders.set_enabled(False);orders.set_enabled(True);self.assertEqual(self.plan()['state'],'paused')
                response=client.post('/api/trading/auto-sell/resume',json={'nonce':BUY['nonce'],'confirm':True},headers=headers)
                self.assertEqual(response.status_code,200);self.assertEqual(self.plan()['state'],'active')
        finally:
            web.app.config.clear();web.app.config.update(config)

    def test_preflight_failure_is_marked_not_sent_and_halted(self):
        orders.submit(BUY,EVENT)
        with patch.object(worker.orders,'submit',side_effect=orders.TradeError('preflight')):
            self.runplan()
        self.assertEqual(self.actions()[0]['state'],'not_sent')
        self.assertEqual(self.plan()['state'],'halt');self.assertEqual(self.client.post_order.call_count,1)

    def test_invalid_auto_sell_not_sent(self):
        for value in ('0','-5','100','5.001'):
            with self.subTest(value=value):
                with self.assertRaises(orders.TradeError):orders.submit({**BUY,'auto_sell_cents':value},EVENT)
        self.client.post_order.assert_not_called()

if __name__=='__main__':unittest.main()
