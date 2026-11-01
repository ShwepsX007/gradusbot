import os
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from werkzeug.security import generate_password_hash
from web import app as web, trading_account as account, trading_orders as orders

KEY={'private_key':'0x'+'0'*63+'1','api_key':'key','api_secret':'secret','api_passphrase':'pass','signature_type':'0','funder':''}
EVENT={'title':'Test','slug':'test-event','active':True,'closed':False,'markets':[{'active':True,'closed':False,'acceptingOrders':True,'outcomes':'["No", "Yes"]','clobTokenIds':'["2", "1"]'}]}
BASE={'slug':'test-event','nonce':'a'*32,'asset':'1','selection':'yes','side':'BUY','order_type':'GTC','price':'0.25','size':'10','confirm':True}
BOOK={'asset_id':'1','tick_size':'0.01','min_order_size':'5','neg_risk':False}

class OrderTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'WEB_EXPERIMENTAL_MANUAL_ORDERS':'1'});self.env.start()
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(KEY)
        self.client=Mock();self.client.get_order_book.return_value=BOOK.copy();self.client.create_order.return_value='signed';self.client.post_order.return_value={'success':True,'orderID':'0x'+'1'*64}
        self.cp=patch.object(account,'build_client',return_value=self.client);self.cp.start()
    def tearDown(self):
        self.cp.stop();self.p.stop();self.env.stop();web.db.DB_FILE=self.old_db;self.tmp.cleanup()

    def test_server_gate_fails_closed(self):
        orders.set_enabled(True)
        with patch.dict(os.environ,{'WEB_EXPERIMENTAL_MANUAL_ORDERS':'0'}):
            self.assertFalse(orders.enabled())
            with self.assertRaises(orders.TradeError):orders.set_enabled(True)
            with self.assertRaises(orders.TradeError):orders.submit(BASE,EVENT)
            self.client.post_order.assert_not_called()

    def test_disabled_invalid_and_no_autosizing(self):
        with self.assertRaises(orders.TradeError): orders.submit(BASE,EVENT)
        self.client.post_order.assert_not_called();orders.set_enabled(True)
        variants=[{**BASE,'confirm':False},{**BASE,'asset':'2'},{**BASE,'price':'0.251'},
                  {**BASE,'size':'4.99'},{**BASE,'size':'1e999'},{**BASE,'size':'10.001'},
                  {**BASE,'side':'BAD'},{**BASE,'tp':'20'}, {**BASE,'nonce':'bad'}]
        for data in variants:
            with self.subTest(data=data):
                with self.assertRaises(orders.TradeError):orders.submit(data,EVENT)
        self.client.create_order.assert_not_called();self.client.post_order.assert_not_called()
        for changed in ({**EVENT,'closed':True},{**EVENT,'active':False}, {**EVENT,'markets':[{**EVENT['markets'][0],'acceptingOrders':False}]}):
            with self.assertRaises(orders.TradeError): orders.submit(BASE,changed)

    def test_one_shot_and_idempotent(self):
        orders.set_enabled(True);result=orders.submit(BASE,EVENT)
        self.assertEqual(result['state'],'accepted');self.assertEqual(self.client.post_order.call_count,1)
        result=orders.submit(BASE,EVENT);self.assertEqual(result['state'],'accepted');self.assertEqual(self.client.post_order.call_count,1)
        args=self.client.create_order.call_args.args[0];self.assertEqual(args.size,10);self.assertEqual(args.price,.25)
        self.assertEqual(orders.list_intents(account.load()['signer'])[0]['order_id'],'0x'+'1'*64)

    def test_concurrent_requests_only_post_once(self):
        orders.set_enabled(True);started=threading.Event();release=threading.Event()
        def create(*args,**kwargs):
            started.set();self.assertTrue(release.wait(5));return 'signed'
        self.client.create_order.side_effect=create
        result=[];thread=threading.Thread(target=lambda:result.append(orders.submit(BASE,EVENT)))
        thread.start();self.assertTrue(started.wait(5))
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'pending')
        with self.assertRaises(orders.TradeError):orders.submit({**BASE,'nonce':'b'*32},EVENT)
        release.set();thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(result[0]['state'],'accepted')
        self.assertEqual(self.client.post_order.call_count,1)

    def test_ticks_are_normalized_and_protocol_v2_blocked(self):
        orders.set_enabled(True);self.client.get_order_book.return_value={**BOOK,'tick_size':'0.010'}
        orders.submit(BASE,EVENT)
        self.assertEqual(self.client.create_order.call_args.args[1].tick_size,'0.01')
        event={**EVENT,'markets':[{**EVENT['markets'][0],'version':'v2'}]}
        with self.assertRaises(orders.TradeError):orders.submit({**BASE,'nonce':'b'*32},event)
        self.assertEqual(self.client.post_order.call_count,1)

    def test_unknown_timeout_blocks_retries_and_other_nonces(self):
        orders.set_enabled(True);self.client.post_order.side_effect=TimeoutError('maybe accepted')
        result=orders.submit(BASE,EVENT);self.assertEqual(result['state'],'unknown')
        self.assertEqual(orders.submit(BASE,EVENT)['state'],'unknown');self.assertEqual(self.client.post_order.call_count,1)
        with self.assertRaises(orders.TradeError):orders.submit({**BASE,'nonce':'b'*32},EVENT)
        self.assertEqual(self.client.post_order.call_count,1)
        self.assertEqual(orders.list_intents(account.load()['signer'])[0]['state'],'unknown')

    def test_sign_failure_not_sent_and_cancel(self):
        orders.set_enabled(True);self.client.create_order.side_effect=Exception('private')
        with self.assertRaises(orders.TradeError):orders.submit(BASE,EVENT)
        self.client.post_order.assert_not_called();self.assertEqual(orders.list_intents(account.load()['signer'])[0]['state'],'not_sent')
        oid='0x'+'f'*64;self.client.get_order.return_value={'id':oid};self.client.cancel_order.return_value={'canceled':[oid]}
        orders.set_enabled(False)  # stopping new orders must not block emergency cancellation
        self.assertEqual(orders.cancel(oid)['state'],'canceled');self.assertEqual(self.client.cancel_order.call_count,1)
        self.client.cancel_order.side_effect=TimeoutError();self.assertEqual(orders.cancel(oid)['state'],'unknown');self.assertEqual(self.client.cancel_order.call_count,2)
        self.client.get_order.return_value={'id':'other'}
        with self.assertRaises(orders.TradeError):orders.cancel(oid)
        self.assertEqual(self.client.cancel_order.call_count,2)

    def test_auto_keys_dont_escape(self):
        derived=Mock(api_key='new-key',api_secret='new-secret',api_passphrase='new-pass')
        client=Mock();client.create_or_derive_api_key.return_value=derived
        with patch.object(account,'ClobClient',return_value=client):
            saved=account.save({**KEY,'api_key':'','api_secret':'','api_passphrase':'','auto_api':True})
        self.assertTrue(saved['configured']);self.assertNotIn('new-secret',str(saved));self.assertEqual(account.load()['api_secret'],'new-secret')
        client.create_or_derive_api_key.side_effect=RuntimeError('new-secret')
        with patch.object(account,'ClobClient',return_value=client):
            with self.assertRaises(account.AccountError) as e:account.save({**KEY,'auto_api':True})
        self.assertNotIn('new-secret',str(e.exception));self.assertEqual(account.load()['api_secret'],'new-secret')

class OrderEndpointTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'WEB_EXPERIMENTAL_MANUAL_ORDERS':'1'});self.env.start()
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE;self.config=dict(web.app.config)
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(KEY)
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password long enough'),SECRET_KEY='x'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False,TESTING=True)
        web._login_attempts.clear();self.client=web.app.test_client();self.base='http://127.0.0.1:8108'
        self.client.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
        with self.client.session_transaction(base_url=self.base) as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
    def tearDown(self):
        self.p.stop();self.env.stop();web.db.DB_FILE=self.old_db;web.app.config.clear();web.app.config.update(self.config);self.tmp.cleanup()
    def post(self,path,data,**kw):return self.client.post(path,base_url=self.base,json=data,**kw)
    def test_control_auth_and_fail_closed(self):
        self.assertEqual(self.post('/api/trading/orders',BASE,headers={'X-CSRF-Token':'token'}).status_code,403)
        payload={'enabled':True,'confirm':True,'current_password':'password long enough'}
        self.assertEqual(self.post('/api/trading/control',payload).status_code,403)
        self.assertEqual(self.post('/api/trading/control',payload,headers={'X-CSRF-Token':'token'},environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
        self.assertEqual(self.post('/api/trading/control',{**payload,'current_password':'wrong'},headers={'X-CSRF-Token':'token'}).status_code,400)
        self.assertEqual(self.post('/api/trading/control',payload,headers={'X-CSRF-Token':'token'}).status_code,200)
        fake=Mock();fake.get_order_book.return_value=BOOK;fake.create_order.return_value='signed';fake.post_order.return_value={'success':True,'orderID':'0x'+'1'*64}
        with patch.object(web,'event_for_station',return_value=EVENT),patch.object(account,'build_client',return_value=fake):
            self.assertEqual(self.post('/api/trading/orders',BASE,headers={'X-CSRF-Token':'token'}).get_json()['state'],'accepted')
        self.assertEqual(fake.post_order.call_count,1)
        with patch.object(web,'event_for_station',side_effect=RuntimeError('offline')):
            replay=self.post('/api/trading/orders',BASE,headers={'X-CSRF-Token':'token'})
        self.assertEqual(replay.get_json()['state'],'accepted');self.assertEqual(fake.post_order.call_count,1)
        self.assertEqual(self.post('/api/trading/control',{**payload,'enabled':False},headers={'X-CSRF-Token':'token'}).status_code,200)
        self.assertEqual(self.post('/api/trading/orders',{**BASE,'nonce':'b'*32},headers={'X-CSRF-Token':'token'}).status_code,403)

if __name__=='__main__':unittest.main()
