import os
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from eth_account import Account
from eth_account.messages import encode_typed_data
from py_clob_client_v2.clob_types import OrderArgsV2, MarketOrderArgsV2, CreateOrderOptions
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.order_builder.builder import OrderBuilder
from py_clob_client_v2.order_utils.exchange_order_builder_v2 import ExchangeOrderBuilderV2
from py_clob_client_v2.order_utils.model.order_data_v2 import OrderDataV2
from py_clob_client_v2.order_utils.model.side import Side
from py_clob_client_v2.signer import Signer

from web import app as web, trading_account as account, trading_orders as orders, order_reconciliation as recon

KEY={'private_key':'0x'+'0'*63+'1','api_key':'api-key','api_secret':'secret','api_passphrase':'pass','signature_type':'0','funder':''}
ID='0x'+'1'*64
EVENT={'active':True,'closed':False,'markets':[{'active':True,'closed':False,'acceptingOrders':True,'outcomes':['Yes','No'],'clobTokenIds':['1','2']}]}
BASE={'slug':'test','nonce':'a'*32,'asset':'1','selection':'yes','side':'SELL','order_type':'FAK','execution_mode':'market','price':'0.25','size':'10','confirm':True}
BOOK={'asset_id':'1','tick_size':'0.01','min_order_size':'1','neg_risk':False}

class OrderHashTests(unittest.TestCase):
    def test_fixed_v2_hash_matches_signed_eip712_and_known_vector(self):
        signer=Signer(KEY['private_key'],137);c={'signer':signer.address(),'funder':'','signature_type':0}
        builder=ExchangeOrderBuilderV2(get_contract_config(137).exchange_v2,137,signer,generate_salt=lambda:1)
        data=OrderDataV2(maker=signer.address(),signer=signer.address(),tokenId='1',makerAmount='10000000',takerAmount='2500000',side=Side.SELL,timestamp='1700000000000')
        signed=builder.build_signed_order(data)
        typed=builder.build_order_typed_data(signed)
        recovered=Account.recover_message(encode_typed_data(full_message=typed),signature=signed.signature)
        self.assertEqual(recovered,signer.address())
        actual=recon.order_hash(signed,c,signer,neg_risk=False,asset='1',side='SELL',price=Decimal('.25'),size=Decimal('10'))
        self.assertEqual(actual,builder.build_order_hash(typed))
        self.assertEqual(actual,'0x0face636c2cbfb08495a0eb97cb385ce0a58ac5d2dc1a02121d361ef84a03ab4')
        self.assertNotEqual(actual,recon.order_hash(signed,c,signer,neg_risk=True,asset='1',side='SELL',price=Decimal('.25'),size=Decimal('10')))
        for bad in [dict(asset='2'),dict(side='BUY'),dict(price=Decimal('.26')),dict(size=Decimal('9'))]:
            args=dict(neg_risk=False,asset='1',side='SELL',price=Decimal('.25'),size=Decimal('10'));args.update(bad)
            with self.assertRaises(recon.ReconciliationError):recon.order_hash(signed,c,signer,**args)

    def test_actual_sdk_version_and_deposit_wallet(self):
        signer=Signer(KEY['private_key'],137)
        for version in (1,2):
            for neg in (False,True):
                for side in ('BUY','SELL'):
                    signed=OrderBuilder(signer).build_order(OrderArgsV2(token_id='1',price=.25,size=10,side=side),CreateOrderOptions(tick_size='0.01',neg_risk=neg),version=version)
                    c={'signer':signer.address(),'funder':'','signature_type':0}
                    args=dict(neg_risk=neg,asset='1',side=side,price=Decimal('.25'),size=Decimal('10'))
                    if version==1:
                        with self.assertRaises(recon.ReconciliationError):recon.order_hash(signed,c,signer,**args)
                    else:
                        self.assertRegex(recon.order_hash(signed,c,signer,**args),r'^0x[0-9a-f]{64}$')
        wallet=Account.from_key('0x'+'0'*63+'2').address
        c={'signer':signer.address(),'funder':wallet,'signature_type':3}
        signed=OrderBuilder(signer,signature_type=3,funder=wallet).build_order(OrderArgsV2(token_id='1',price=.25,size=10,side='SELL'),CreateOrderOptions(tick_size='0.01',neg_risk=False),version=2)
        self.assertEqual(signed.signer,wallet)
        self.assertRegex(recon.order_hash(signed,c,signer,neg_risk=False,asset='1',side='SELL',price=Decimal('.25'),size=Decimal('10')),r'^0x[0-9a-f]{64}$')

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.ap=patch.object(account.config,'BASE_DIR',self.tmp.name);self.ap.start();account.save(KEY)
        self.c=account.load();self.wallet=self.c['signer'];orders.set_enabled(True)
        self.client=Mock();self.client.retry_on_error=False;self.client.get_order_book.return_value=BOOK;self.client.create_order.return_value='signed';self.client.create_market_order.return_value=OrderBuilder(Signer(KEY['private_key'],137)).build_market_order(MarketOrderArgsV2(token_id='1',side='SELL',price=.25,amount=10),CreateOrderOptions(tick_size='0.01',neg_risk=False),version=2);self.client.post_order.return_value={'success':True,'orderID':ID}
        self.cp=patch.object(account,'build_client',return_value=self.client);self.cp.start()
        self.hp=patch.object(recon,'order_hash',return_value=ID);self.hp.start()

    def tearDown(self):
        self.hp.stop();self.cp.stop();self.ap.stop();web.db.DB_FILE=self.old_db;self.tmp.cleanup()

    def row(self):
        with web.db.get_db() as c:return dict(c.execute('SELECT * FROM web_order_intents WHERE nonce=?',(BASE['nonce'],)).fetchone())
    def evidence(self):
        with web.db.get_db() as c:
            r=c.execute('SELECT * FROM web_order_reconciliation WHERE nonce=?',(BASE['nonce'],)).fetchone()
            return dict(r) if r else None
    def raw(self, status='MATCHED',matched='3',trades=None):
        return {'id':ID,'asset_id':'1','side':'SELL','maker_address':self.wallet,'owner':self.c['api_key'],
                'price':'0.25','original_size':'10','size_matched':matched,'status':status,
                'associate_trades':trades if trades is not None else ['trade-1','trade-2']}
    def taker(self, id, amount, state):
        return {'id':id,'taker_order_id':ID,'asset_id':'1','side':'SELL','owner':self.c['api_key'],'size':amount,'status':state}
    def maker(self, id, amount, state):
        return {'id':id,'taker_order_id':'0x'+'2'*64,'asset_id':'2','side':'BUY','owner':'other', 'size':'99','status':state,
                'maker_orders':[{'order_id':ID,'asset_id':'1','side':'SELL','owner':self.c['api_key'], 'maker_address':self.wallet,'matched_amount':amount}]}
    def no_second_order(self):
        # Reconciliation itself never submits. Unknown/pending require separate acknowledgement.
        if self.row()['state'] in ('pending','unknown'):
            with self.assertRaises(orders.UnknownOrders):orders.submit({**BASE,'nonce':'b'*32},EVENT)
        self.assertEqual(self.client.post_order.call_count,1)
    def set_trades(self,trades):
        self.client.get_trades.side_effect=lambda params,only_first_page: [trades[params.id]]

    def test_hash_is_durable_before_only_post_and_timeout_reconciles_without_resend(self):
        def post(*args):
            self.assertEqual(self.row()['state'],'pending');self.assertEqual(self.evidence()['expected_id'],ID)
            raise TimeoutError()
        self.client.post_order.side_effect=post
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'unknown');self.no_second_order()
        self.client.get_order.return_value=self.raw('LIVE','3')
        self.set_trades({'trade-1':self.taker('trade-1','2','CONFIRMED'),'trade-2':self.maker('trade-2','1','MINED')})
        recon.reconcile_wallet(self.c);self.assertEqual(self.row()['state'],'accepted');self.assertEqual(self.evidence()['assessment'],'active');self.no_second_order()
        self.client.get_order.return_value=self.raw('MATCHED','3')
        self.set_trades({'trade-1':self.taker('trade-1','2','CONFIRMED'),'trade-2':self.maker('trade-2','1','FAILED')})
        recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'terminal');self.assertEqual(self.evidence()['confirmed_size'],'2');self.assertEqual(self.evidence()['failed_size'],'1')
        self.client.post_order.side_effect=None;self.client.post_order.return_value={'success':True,'orderID':ID}
        # Only a fresh, explicitly confirmed user request can produce another POST.
        self.assertEqual(orders.submit({**BASE,'nonce':'b'*32},EVENT)['state'],'accepted');self.assertEqual(self.client.post_order.call_count,2)

    def test_each_nonterminal_status_stays_blocked(self):
        orders.submit(BASE,EVENT)
        for state in ('MATCHED','MATCHED_NOT_BROADCASTED','MINED','RETRYING'):
            self.client.get_order.return_value=self.raw('MATCHED','3')
            self.set_trades({'trade-1':self.taker('trade-1','2','CONFIRMED'),'trade-2':self.maker('trade-2','1',state)})
            recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'active');self.no_second_order()

    def test_open_cancelled_and_gtc_partial(self):
        orders.submit({**BASE,'order_type':'GTC','execution_mode':'limit'},EVENT)
        self.client.get_order.return_value=self.raw('MATCHED','3')
        self.set_trades({'trade-1':self.taker('trade-1','2','CONFIRMED'),'trade-2':self.maker('trade-2','1','CONFIRMED')})
        recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'active');self.no_second_order()
        self.client.get_order.return_value=self.raw('CANCELED','3')
        recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'terminal')

    def test_no_match_cancelled_is_proof_only_with_explicit_empty_trade_list(self):
        orders.submit(BASE,EVENT)
        raw=self.raw('CANCELED','0',[]);del raw['associate_trades'];self.client.get_order.return_value=raw
        recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'unverified');self.no_second_order()
        self.client.get_order.return_value=self.raw('CANCELED','0',[])
        recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'terminal')

    def test_missing_or_inconsistent_trade_and_identity_fail_closed(self):
        orders.submit(BASE,EVENT)
        good=self.raw();good_trades={'trade-1':self.taker('trade-1','2','CONFIRMED'),'trade-2':self.maker('trade-2','1','CONFIRMED')}
        for field,value in [('id','wrong'),('asset_id','2'),('side','BUY'),('maker_address','bad'),('owner','other'),('original_size','11'),('size_matched','11'),('price','0.26'),('status','MYSTERY'),('associate_trades',['trade-1','trade-1'])]:
            with self.subTest(field=field):
                self.client.get_order.return_value={**good,field:value};self.set_trades(good_trades);recon.reconcile_wallet(self.c)
                self.assertEqual(self.evidence()['assessment'],'unverified');self.no_second_order()
        for changed in [self.taker('trade-1','1','CONFIRMED'),{**self.taker('trade-1','2','CONFIRMED'),'owner':'other'},
                        {**self.taker('trade-1','2','CONFIRMED'),'status':'MYSTERY'}, {**self.taker('trade-1','2','CONFIRMED'),'asset_id':'2'}]:
            self.client.get_order.return_value=good;self.set_trades({**good_trades,'trade-1':changed});recon.reconcile_wallet(self.c)
            self.assertEqual(self.evidence()['assessment'],'unverified');self.no_second_order()
        self.client.get_trades.side_effect=TimeoutError();recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'unverified');self.no_second_order()

    def test_404_absence_old_unknown_and_credentials_change_stay_blocked(self):
        self.client.post_order.side_effect=TimeoutError();orders.submit(BASE,EVENT)
        self.client.get_order.side_effect=RuntimeError('404');recon.reconcile_wallet(self.c)
        self.assertEqual(self.row()['state'],'unknown');self.no_second_order()
        with web.db.get_db() as c:c.execute('DELETE FROM web_order_reconciliation')
        recon.reconcile_wallet(self.c);self.assertEqual(self.evidence()['assessment'],'unverified');self.assertIsNone(self.evidence()['expected_id']);self.no_second_order()
        with web.db.get_db() as c:c.execute('UPDATE web_order_reconciliation SET expected_id=?,credential_fingerprint=?',(ID,'old'))
        recon.reconcile_wallet(self.c);self.assertIn('изменены',self.evidence()['reason']);self.no_second_order()

    def test_monitor_cannot_release_a_submission_waiting_for_its_response(self):
        started=threading.Event();release=threading.Event();result=[]
        def post(*args):
            with web.db.get_db() as c:c.execute('UPDATE web_order_intents SET created=?',(time.time()-120,))
            started.set();self.assertTrue(release.wait(5))
            return {'success':True,'orderID':'0x'+'2'*64}
        self.client.post_order.side_effect=post
        worker=threading.Thread(target=lambda:result.append(orders.submit(BASE,EVENT)));worker.start()
        self.assertTrue(started.wait(5));self.client.get_order.return_value=self.raw('CANCELED','0',[])
        recon.reconcile_wallet(self.c);self.client.get_order.assert_not_called();self.assertEqual(self.row()['state'],'pending')
        self.no_second_order();release.set();worker.join(5);self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['state'],'unknown');self.assertEqual(self.evidence()['assessment'],'conflict')

    def test_unsafe_lock_symlink_fails_closed(self):
        path=os.path.join(self.tmp.name,'.gradus-private','order-shard-aa.lock')
        os.symlink('/etc/passwd',path)
        with self.assertRaises(orders.TradeError):orders.submit(BASE,EVENT)
        self.client.create_order.assert_not_called();self.client.post_order.assert_not_called()

    def test_pending_after_crash_recovers_only_with_positive_proof(self):
        self.client.post_order.side_effect=TimeoutError();orders.submit(BASE,EVENT)
        with web.db.get_db() as c:c.execute("UPDATE web_order_intents SET state='pending',created=?",(time.time(),))
        self.client.get_order.return_value=self.raw('CANCELED','0',[])
        recon.reconcile_wallet(self.c);self.assertEqual(self.row()['state'],'pending');self.no_second_order()
        with web.db.get_db() as c:c.execute('UPDATE web_order_intents SET created=?',(time.time()-90,))
        recon.reconcile_wallet(self.c);self.assertEqual(self.row()['state'],'accepted');self.assertEqual(self.evidence()['assessment'],'terminal')
        orders._finish(BASE['nonce'],'unknown');self.assertEqual(self.row()['state'],'accepted')

    def test_contradictory_response_permanently_blocks_automatic_reconciliation(self):
        self.client.post_order.return_value={'success':True,'orderID':'0x'+'2'*64}
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'unknown');self.assertEqual(self.evidence()['assessment'],'conflict')
        self.client.get_order.return_value=self.raw('CANCELED','0',[]);recon.reconcile_wallet(self.c)
        self.assertEqual(self.row()['state'],'unknown');self.assertEqual(self.evidence()['assessment'],'conflict');self.no_second_order()

    def test_real_sdk_signed_order_flows_through_submit_without_network(self):
        self.hp.stop()
        signer=Signer(KEY['private_key'],137);self.client.signer=signer
        builder=OrderBuilder(signer)
        def create(args,options):
            self.assertEqual(options.version,2)
            return builder.build_market_order(args,CreateOrderOptions(tick_size=options.tick_size,neg_risk=options.neg_risk),version=options.version)
        self.client.create_market_order.side_effect=create
        def post(order,kind):
            stored=self.evidence()
            self.assertEqual(stored['expected_id'],recon.order_hash(order,self.c,signer,neg_risk=False,asset='1',side='SELL',price=Decimal('.25'),size=Decimal('10'),market=True))
            return {'success':True,'orderID':stored['expected_id']}
        self.client.post_order.side_effect=post
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'accepted')
        self.hp.start()

    def test_retry_enabled_fails_before_post(self):
        self.client.retry_on_error=True
        with self.assertRaises(orders.TradeError):orders.submit(BASE,EVENT)
        self.client.create_order.assert_not_called();self.client.post_order.assert_not_called()
        self.assertEqual(self.row()['state'],'not_sent')

    def test_hash_failure_never_posts_and_switch_upgrade_off(self):
        self.hp.stop()
        with patch.object(recon,'order_hash',side_effect=recon.ReconciliationError('bad')):
            with self.assertRaises(orders.TradeError):orders.submit(BASE,EVENT)
        self.hp.start();self.client.post_order.assert_not_called();self.assertEqual(self.row()['state'],'not_sent')
        with web.db.get_db() as c:c.execute("DELETE FROM web_trading_migrations WHERE name='reconciliation-opt-in-v1'")
        web.init_web_db();self.assertFalse(orders.enabled())

if __name__=='__main__':unittest.main()
