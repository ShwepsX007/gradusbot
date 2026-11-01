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
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(KEY)
        self.client=Mock();self.client.retry_on_error=False;self.client.get_order_book.return_value=BOOK.copy();self.client.create_order.return_value='signed';self.client.post_order.return_value={'success':True,'orderID':'0x'+'1'*64}
        self.cp=patch.object(account,'build_client',return_value=self.client);self.cp.start()
        self.hp=patch.object(orders.reconciliation,'order_hash',return_value='0x'+'1'*64);self.hp.start()
    def tearDown(self):
        self.hp.stop();self.cp.stop();self.p.stop();web.db.DB_FILE=self.old_db;self.tmp.cleanup()

    def test_upgrade_requires_fresh_opt_in_only_once(self):
        orders.set_enabled(True)
        with web.db.get_db() as conn: conn.execute("DELETE FROM web_trading_migrations WHERE name='manual-opt-in-v1'")
        web.init_web_db();self.assertFalse(orders.enabled())
        orders.set_enabled(True);web.init_web_db();self.assertTrue(orders.enabled())

    def test_switch_fails_closed_and_persists(self):
        self.assertFalse(orders.enabled())
        orders.set_enabled(True);self.assertTrue(orders.enabled())
        orders.set_enabled(False);self.assertFalse(orders.enabled())
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

    def test_take_profit_is_explicit_single_gtc_sell_for_held_tokens(self):
        orders.set_enabled(True)
        self.client.get_balance_allowance.return_value={'balance':'10000000'}
        body={**BASE,'side':'SELL','purpose':'take_profit','confirm_take_profit':True}
        result=orders.submit(body,EVENT)
        self.assertEqual(result['state'],'accepted');self.assertEqual(self.client.post_order.call_count,1)
        params=self.client.get_balance_allowance.call_args.args[0]
        self.assertEqual(params.asset_type,'CONDITIONAL');self.assertEqual(params.token_id,'1');self.assertEqual(params.signature_type,0)
        self.assertEqual(self.client.update_balance_allowance.call_args.args[0],params)
        row=orders.list_intents(account.load()['signer'])[0]
        self.assertEqual(row['purpose'],'take_profit');self.assertEqual(row['side'],'SELL');self.assertEqual(row['order_type'],'GTC')
        self.assertEqual(str(row['price']),'0.25');self.assertEqual(str(row['size']),'10')
        self.assertEqual(orders.submit(body,EVENT)['state'],'accepted');self.assertEqual(self.client.post_order.call_count,1)
        with self.assertRaises(orders.TradeError):orders.submit({**body,'nonce':'b'*32},EVENT)
        self.assertEqual(self.client.post_order.call_count,1)

    def test_take_profit_refuses_invalid_quantity_market_and_balance(self):
        orders.set_enabled(True);self.client.get_balance_allowance.return_value={'balance':'10000000'}
        body={**BASE,'side':'SELL','purpose':'take_profit','confirm_take_profit':True}
        for changes in ({'confirm_take_profit':False},{'side':'BUY'},{'order_type':'FAK'},{'size':'4.99'},
                        {'purpose':'other'},{'price':'0.251'},{'confirm':False}):
            with self.subTest(changes=changes):
                with self.assertRaises(orders.TradeError):orders.submit({**body,**changes},EVENT)
        with self.assertRaises(orders.TradeError):orders.submit(body,{**EVENT,'closed':True})
        for balance in [None,{}, {'balance':None},{'balance':True},{'balance':'9.0'},{'balance':'NaN'},{'balance':'9999999'}]:
            self.client.get_balance_allowance.return_value=balance
            with self.subTest(balance=balance):
                with self.assertRaises(orders.TradeError):orders.submit(body,EVENT)
        self.client.get_balance_allowance.side_effect=TimeoutError('private')
        with self.assertRaises(orders.TradeError) as error:orders.submit(body,EVENT)
        self.assertNotIn('private',str(error.exception))
        self.client.create_order.assert_not_called();self.client.post_order.assert_not_called()
        self.assertEqual(orders.list_intents(account.load()['signer']),[])

    def test_take_profit_unknown_never_reposts_and_buy_reservation_blocks(self):
        orders.set_enabled(True);self.client.get_balance_allowance.return_value={'balance':'20000000'}
        orders.submit(BASE,EVENT)
        tp={**BASE,'nonce':'b'*32,'side':'SELL','purpose':'take_profit','confirm_take_profit':True}
        with self.assertRaises(orders.TradeError):orders.submit(tp,EVENT)
        self.assertEqual(self.client.post_order.call_count,1)
        with web.db.get_db() as conn:conn.execute("UPDATE web_order_reconciliation SET assessment='terminal' WHERE nonce=?",(BASE['nonce'],))
        self.client.post_order.side_effect=TimeoutError()
        self.assertEqual(orders.submit(tp,EVENT)['state'],'unknown')
        self.assertEqual(orders.submit(tp,EVENT)['state'],'unknown')
        with self.assertRaises(orders.TradeError):orders.submit({**tp,'nonce':'c'*32},EVENT)
        self.assertEqual(self.client.post_order.call_count,2)

    def test_intent_purpose_migration_preserves_existing_rows(self):
        orders.set_enabled(True);orders.submit(BASE,EVENT);web.init_web_db();web.init_web_db()
        row=orders.list_intents(account.load()['signer'])[0]
        self.assertEqual(row['purpose'],'manual');self.assertTrue(orders.enabled())

    def test_legacy_order_schema_migrates_with_existing_order(self):
        with web.db.get_db() as conn:
            conn.execute('DROP TABLE web_order_intents')
            conn.execute("""CREATE TABLE web_order_intents(nonce TEXT PRIMARY KEY,wallet TEXT NOT NULL,slug TEXT NOT NULL,asset TEXT NOT NULL,side TEXT NOT NULL,price TEXT NOT NULL,size TEXT NOT NULL,order_type TEXT NOT NULL,state TEXT NOT NULL,order_id TEXT,created REAL NOT NULL,updated REAL NOT NULL)""")
            conn.execute("INSERT INTO web_order_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('a'*32,account.load()['signer'],'test-event','1','BUY','0.25','10','GTC','accepted','0x'+'1'*64,1,1))
        web.init_web_db();web.init_web_db()
        row=orders.list_intents(account.load()['signer'])[0]
        self.assertEqual(row['purpose'],'manual');self.assertEqual(row['order_id'],'0x'+'1'*64)

    def test_status_reads_show_partial_and_never_mutate_journal(self):
        orders.set_enabled(True);orders.submit(BASE,EVENT);rows=orders.list_intents(account.load()['signer'])
        oid='0x'+'1'*64
        self.client.get_order.return_value={'id':oid,'asset_id':'1','status':'LIVE','original_size':'10','size_matched':'3.25'}
        before=self.client.post_order.call_count
        result=orders.annotate_order_statuses(rows,account.load());self.assertEqual(result[0]['exchange']['size_matched'],'3.25')
        self.assertEqual(orders.list_intents(account.load()['signer'])[0]['state'],'accepted')
        self.assertEqual(self.client.post_order.call_count,before)
        self.client.get_order.return_value['size_matched']='11'
        self.assertFalse(orders.annotate_order_statuses(rows,account.load())[0]['exchange']['available'])
        self.client.get_order.side_effect=TimeoutError()
        self.assertFalse(orders.annotate_order_statuses(rows,account.load())[0]['exchange']['available'])
        self.assertEqual(self.client.post_order.call_count,before)

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
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE;self.config=dict(web.app.config)
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(KEY)
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password long enough'),SECRET_KEY='x'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False,TESTING=True)
        web._login_attempts.clear();self.client=web.app.test_client();self.base='http://127.0.0.1:8108'
        self.client.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
        with self.client.session_transaction(base_url=self.base) as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
    def tearDown(self):
        self.p.stop();web.db.DB_FILE=self.old_db;web.app.config.clear();web.app.config.update(self.config);self.tmp.cleanup()
    def post(self,path,data,**kw):return self.client.post(path,base_url=self.base,json=data,**kw)
    def test_control_auth_and_fail_closed(self):
        self.assertEqual(self.post('/api/trading/orders',BASE,headers={'X-CSRF-Token':'token'}).status_code,403)
        payload={'enabled':True,'confirm':True,'current_password':'password long enough'}
        self.assertEqual(self.post('/api/trading/control',payload).status_code,403)
        self.assertEqual(self.post('/api/trading/control',payload,headers={'X-CSRF-Token':'token'},environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
        self.assertEqual(self.post('/api/trading/control',{**payload,'current_password':'wrong'},headers={'X-CSRF-Token':'token'}).status_code,400)
        self.assertEqual(self.post('/api/trading/control',payload,headers={'X-CSRF-Token':'token'}).status_code,200)
        fake=Mock();fake.retry_on_error=False;fake.get_order_book.return_value=BOOK;fake.create_order.return_value='signed';fake.post_order.return_value={'success':True,'orderID':'0x'+'1'*64}
        with patch.object(web,'event_for_station',return_value=EVENT),patch.object(account,'build_client',return_value=fake),patch.object(orders.reconciliation,'order_hash',return_value='0x'+'1'*64):
            self.assertEqual(self.post('/api/trading/orders',BASE,headers={'X-CSRF-Token':'token'}).get_json()['state'],'accepted')
        self.assertEqual(fake.post_order.call_count,1)
        with patch.object(web,'event_for_station',side_effect=RuntimeError('offline')):
            replay=self.post('/api/trading/orders',BASE,headers={'X-CSRF-Token':'token'})
        self.assertEqual(replay.get_json()['state'],'accepted');self.assertEqual(fake.post_order.call_count,1)
        self.assertEqual(self.post('/api/trading/control',{**payload,'enabled':False},headers={'X-CSRF-Token':'token'}).status_code,200)
        self.assertEqual(self.post('/api/trading/orders',{**BASE,'nonce':'b'*32},headers={'X-CSRF-Token':'token'}).status_code,403)

if __name__=='__main__':unittest.main()
