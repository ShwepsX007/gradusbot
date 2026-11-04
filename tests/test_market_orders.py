import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from dataclasses import replace
from py_clob_client_v2.clob_types import CreateOrderOptions, MarketOrderArgsV2
from py_clob_client_v2.order_builder.builder import OrderBuilder
from py_clob_client_v2.signer import Signer
from web import app as web, trading_account as account, trading_orders as orders, order_reconciliation as recon

KEY={'private_key':'0x'+'0'*63+'1','api_key':'key','api_secret':'secret','api_passphrase':'pass','signature_type':'0','funder':''}
EVENT={'active':True,'closed':False,'markets':[{'active':True,'closed':False,'acceptingOrders':True,'outcomes':['Yes','No'],'clobTokenIds':['1','2']}]}
BASE={'slug':'test','nonce':'a'*32,'asset':'1','selection':'yes','side':'BUY','order_type':'FAK','execution_mode':'market','price':'0.73','size':'5.25','confirm':True}
BOOK={'asset_id':'1','tick_size':'0.01','min_order_size':'5','neg_risk':False}

class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(KEY);orders.set_enabled(True)
        self.signer=Signer(KEY['private_key'],137);self.builder=OrderBuilder(self.signer);self.c=account.load()
        self.client=Mock();self.client.retry_on_error=False;self.client.signer=self.signer;self.client.get_order_book.return_value=BOOK
        self.client.create_market_order.side_effect=lambda args,options:self.builder.build_market_order(args,CreateOrderOptions(tick_size=options.tick_size,neg_risk=options.neg_risk),version=options.version)
        def post(order,kind):
            with web.db.get_db() as conn:
                row=dict(conn.execute('SELECT i.*,r.expected_id FROM web_order_intents i JOIN web_order_reconciliation r ON r.nonce=i.nonce WHERE i.nonce=?',(BASE['nonce'],)).fetchone())
            self.assertEqual(row['state'],'pending');self.assertTrue(row['signed_size']);self.assertTrue(row['signed_price'])
            self.assertEqual(kind,'FAK');return {'success':True,'orderID':row['expected_id']}
        self.client.post_order.side_effect=post
        self.cp=patch.object(account,'build_client',return_value=self.client);self.cp.start()
    def tearDown(self):
        self.cp.stop();self.p.stop();web.db.DB_FILE=self.old;self.tmp.cleanup()
    def test_legacy_fak_request_is_not_reinterpreted_as_usd(self):
        legacy={k:v for k,v in BASE.items() if k!='execution_mode'}
        with self.assertRaises(orders.TradeError):orders.submit(legacy,EVENT)
        self.client.create_market_order.assert_not_called();self.client.post_order.assert_not_called()

    def test_buy_usd_exact_signed_budget_and_protective_bound(self):
        result=orders.submit(BASE,EVENT);self.assertEqual(result['state'],'accepted')
        order=self.client.post_order.call_args.args[0]
        self.assertEqual(Decimal(order.makerAmount),Decimal('5250000'))
        self.assertLessEqual(Decimal(order.makerAmount),Decimal(order.takerAmount)*Decimal('.73'))
        self.assertEqual(self.client.create_market_order.call_count,2) # 0.73 SDK rounding crosses cap
        with web.db.get_db() as conn: row=dict(conn.execute('SELECT * FROM web_order_intents').fetchone())
        self.assertEqual(row['size'],'5.25');self.assertEqual(row['execution_mode'],'market')
        self.assertEqual(Decimal(row['signed_size']),Decimal(order.takerAmount)/1000000)
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'accepted');self.assertEqual(self.client.post_order.call_count,1)
    def test_sell_shares_and_floor(self):
        body={**BASE,'side':'SELL','price':'0.73','size':'5.25'}
        self.assertEqual(orders.submit(body,EVENT)['state'],'accepted')
        order=self.client.post_order.call_args.args[0]
        self.assertEqual(Decimal(order.makerAmount),Decimal('5250000'))
        self.assertGreaterEqual(Decimal(order.takerAmount),Decimal(order.makerAmount)*Decimal('.73'))
        self.assertEqual(self.client.post_order.call_count,1)
    def test_fails_closed_if_no_stricter_valid_price(self):
        valid=self.builder.build_market_order(MarketOrderArgsV2(token_id='1',side='BUY',price=.01,amount=1.01),CreateOrderOptions(tick_size='0.01',neg_risk=False),version=2)
        self.client.create_market_order.side_effect=None
        self.client.create_market_order.return_value=replace(valid,takerAmount='1')
        with self.assertRaises(orders.TradeError):orders.submit({**BASE,'price':'0.01','size':'1.01'},EVENT)
        self.client.post_order.assert_not_called()
    def test_hash_rejects_tampered_market_amounts_and_bounds(self):
        o=self.builder.build_market_order(MarketOrderArgsV2(token_id='1',side='BUY',price=.72,amount=5.25),CreateOrderOptions(tick_size='0.01',neg_risk=False),version=2)
        args=dict(neg_risk=False,asset='1',side='BUY',price=Decimal('.73'),size=Decimal('5.25'),market=True)
        self.assertRegex(recon.order_hash(o,self.c,self.signer,**args),r'^0x[0-9a-f]{64}$')
        for changed in (replace(o,makerAmount='5250001'),replace(o,takerAmount='1'),replace(o,tokenId='2'),replace(o,side=1),replace(o,metadata='0x'+'f'*64)):
            with self.assertRaises(recon.ReconciliationError):recon.order_hash(changed,self.c,self.signer,**args)
    def test_market_order_supports_deposit_wallet_signature_validation(self):
        from eth_account import Account
        wallet=Account.from_key('0x'+'0'*63+'2').address
        credentials={**self.c,'funder':wallet,'signature_type':3}
        builder=OrderBuilder(self.signer,signature_type=3,funder=wallet)
        order=builder.build_market_order(MarketOrderArgsV2(token_id='1',side='BUY',price=.25,amount=5),CreateOrderOptions(tick_size='0.01',neg_risk=False),version=2)
        self.assertEqual(order.signer,wallet)
        self.assertRegex(recon.order_hash(order,credentials,self.signer,neg_risk=False,asset='1',side='BUY',price=Decimal('.25'),size=Decimal('5'),market=True),r'^0x[0-9a-f]{64}$')
        with self.assertRaises(recon.ReconciliationError):recon.order_hash(order,{**credentials,'funder':'0x'+'3'*40},self.signer,neg_risk=False,asset='1',side='BUY',price=Decimal('.25'),size=Decimal('5'),market=True)

    def test_market_rounding_never_crosses_protection_for_supported_ticks(self):
        # Use fresh nonces and database per case. Check signed integers, not floats.
        cases=[('0.1','0.3'),('0.01','0.73'),('0.005','0.735'),('0.0025','0.7375'),('0.001','0.737'),('0.0001','0.7371')]
        for tick,price in cases:
            for side in ('BUY','SELL'):
                with self.subTest(tick=tick,side=side):
                    self.client.get_order_book.return_value={**BOOK,'tick_size':tick}
                    self.client.post_order.reset_mock();self.client.post_order.side_effect=lambda order,kind:{'success':True,'orderID':recon.order_hash(order,self.c,self.signer,neg_risk=False,asset='1',side=side,price=Decimal(price),size=Decimal('5.25'),market=True)}
                    nonce=('%032x' % (int(Decimal(price)*1000000)+(0 if side=='BUY' else 1000000)))
                    result=orders.submit({**BASE,'nonce':nonce,'side':side,'price':price},EVENT)
                    self.assertEqual(result['state'],'accepted');self.assertEqual(self.client.post_order.call_count,1)
                    signed=self.client.post_order.call_args.args[0]
                    self.assertTrue(recon.market_price_safe(signed,side,Decimal(price)))
                    self.assertEqual(Decimal(signed.makerAmount),Decimal('5250000'))

    def test_market_reconciliation_distinguishes_matched_and_confirmed(self):
        orders.submit(BASE,EVENT)
        with web.db.get_db() as conn:row=dict(conn.execute('SELECT * FROM web_order_intents').fetchone())
        order_id=orders.list_intents(self.c['signer'])[0]['order_id']
        self.client.get_order.return_value={'id':order_id,'asset_id':'1','side':'BUY','maker_address':self.c['signer'],'owner':self.c['api_key'],
          'price':row['signed_price'],'original_size':row['signed_size'],'size_matched':'2','status':'MATCHED','associate_trades':['t']}
        self.client.get_trades.return_value=[{'id':'t','taker_order_id':order_id,'asset_id':'1','side':'BUY','owner':self.c['api_key'],'size':'2','status':'MINED'}]
        result=recon.inspect_order(self.client,row,self.c,order_id)
        self.assertEqual(result['assessment'],'active');self.assertEqual(result['confirmed_size'],'0')
        self.client.get_trades.return_value[0]['status']='CONFIRMED'
        result=recon.inspect_order(self.client,row,self.c,order_id)
        self.assertEqual(result['assessment'],'terminal');self.assertEqual(result['confirmed_size'],'2')

    def test_no_retry_when_market_post_unknown(self):
        self.client.post_order.side_effect=TimeoutError()
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'unknown')
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'unknown')
        self.assertEqual(self.client.post_order.call_count,1)
        with self.assertRaises(orders.UnknownOrders):orders.submit({**BASE,'nonce':'b'*32},EVENT)
        self.assertEqual(self.client.post_order.call_count,1)
