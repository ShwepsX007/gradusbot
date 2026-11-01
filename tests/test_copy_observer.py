import os
import tempfile
import time
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from web import app as web, copy_observer as observer

W='0x'+'a'*40
C='0x'+'b'*64
TX='0x'+'c'*64

def trade(timestamp,side='BUY',price='0.4',size='10',**kwargs):
    return {'proxy_wallet':W,'token_id':'123','condition_id':C,'transaction_hash':TX,
            'side':side,'timestamp':timestamp,'price':price,'size':size,'title':'Test','outcome':'Yes',**kwargs}

def envelope(rows,more=False,cursor=None):
    return {'data':rows,'pagination':{'has_more':more,'next_cursor':cursor}}

class CopyObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db();observer.init_db()
    def tearDown(self):web.db.DB_FILE=self.old;self.tmp.cleanup()
    def add(self,mode='fixed',amount='2',cap='5',interval=30):
        with patch.object(observer,'resolve_profile',return_value=(W,'Leader')):
            observer.add_leader({'profile':W,'mode':mode,'amount':amount,'cap':cap,'interval_seconds':interval,'confirm':True})
        return observer.status()['leaders'][0]
    def test_profile_resolution_uses_only_known_hosts_and_checks_unique(self):
        profile={'proxyWallet':W,'name':'Alice'}
        with patch.object(observer,'_get',return_value=profile) as get:
            self.assertEqual(observer.resolve_profile('https://polymarket.com/profile/'+W),(W,'Alice'))
            self.assertEqual(get.call_args.args[0],observer.GAMMA_URL+'/public-profile')
        search={'profiles':[{'name':'Alice','proxyWallet':W}]}
        with patch.object(observer,'_get',side_effect=[search,profile]):
            self.assertEqual(observer.resolve_profile('https://polymarket.com/@Alice'),(W,'Alice'))
        with patch.object(observer,'_get',return_value={'profiles':[{'name':'Alice','proxyWallet':W},{'name':'Alice','proxyWallet':'0x'+'b'*40}]}):
            with self.assertRaises(observer.ObserverError): observer.resolve_profile('@Alice')
        with patch.object(observer,'_get') as get:
            for value in ['https://polymarket.com.evil.com/@Alice','https://evil.com/profile/'+W,'https://polymarket.com:123/@Alice','https://polymarket.com:bad/@Alice']:
                with self.assertRaises(observer.ObserverError):observer.resolve_profile(value)
            get.assert_not_called()
    def test_parameters_and_duplicate_wallet(self):
        self.add()
        for change in [{'mode':'wrong'},{'amount':'NaN'},{'cap':'0'},{'amount':'-1'},{'interval_seconds':True},{'interval_seconds':14},{'confirm':False}]:
            data={'profile':W,'mode':'fixed','amount':'2','cap':'5','interval_seconds':30,'confirm':True};data.update(change)
            with self.assertRaises(observer.ObserverError):observer.add_leader(data)
        with patch.object(observer,'resolve_profile',return_value=(W,'Other')):
            with self.assertRaises(observer.ObserverError):observer.add_leader({'profile':W,'mode':'fixed','amount':'1','cap':'5','interval_seconds':30,'confirm':True})
    def test_pagination_and_maker_coverage(self):
        now=int(time.time())
        with patch.object(observer,'_get',side_effect=[envelope([trade(now)],True,'next'),envelope([trade(now-4)],False)]) as get:
            result=observer.fetch_trades(W,now-2);self.assertEqual(len(result),1)
            self.assertEqual(get.call_args_list[0].args[1]['taker_only'],'false')
            self.assertEqual(get.call_args_list[1].args[1]['cursor'],'next')
        for data in [envelope([trade(now,proxy_wallet='0x'+'f'*40)]), {'data':[],'pagination':{'has_more':'false'}},envelope([trade(now-3),trade(now)],False),envelope([],True,None)]:
            with patch.object(observer,'_get',return_value=data):
                with self.assertRaises(observer.ObserverError):observer.fetch_trades(W,now-10)
        with patch.object(observer,'_get',return_value=envelope([trade(now)],True,'same')):
            with self.assertRaises(observer.ObserverError):observer.fetch_trades(W,now-10)
    def test_new_events_dedup_partial_fill_and_estimates(self):
        leader=self.add();started=leader['started_at']
        # Older trades and events from the same second as activation are ignored.
        rows=[observer._trade(t,W) for t in [trade(started+2),trade(started+2),trade(started+1,'SELL'),trade(started),trade(started-1)]]
        book={'asks':[{'price':'0.39','size':'2'},{'price':'0.4','size':'4'},{'price':'0.41','size':'50'}], 'bids':[{'price':'0.4','size':'1'}],'fetched_at':time.time()}
        with patch.object(observer,'fetch_trades',return_value=rows),patch.object(observer.orderbook,'fetch_batch',return_value={'123':book}) as books:
            observer.poll_leader(leader,started+5)
            observer.poll_leader({**leader,'last_success':started+5},started+6)
            self.assertEqual(books.call_count,1)
        result=observer.status();self.assertEqual(len(result['signals']),3)
        buys=[s for s in result['signals'] if s['side']=='BUY'];self.assertEqual(len(buys),2)
        self.assertEqual(buys[0]['estimate']['desired_shares'],'5.000000')
        self.assertEqual(buys[0]['estimate']['available_shares'],'6')
        sell=[s for s in result['signals'] if s['side']=='SELL'][0]
        self.assertIsNone(sell['estimate']['desired_shares']);self.assertEqual(sell['estimate']['available_shares'],'1')
        self.assertEqual(result['leaders'][0]['status'],'ok')
    def test_sizing_cap_and_missing_book(self):
        event={'side':'BUY','price':'0.4','size':'100'}
        for mode,amount,expected in [('same','1','12.500000'),('percent','10','10.000000'),('fixed','9','12.500000')]:
            result=observer.evaluate(event,{'mode':mode,'amount':amount,'cap':'5'},None)
            self.assertEqual(result['desired_shares'],expected);self.assertFalse(result['book_available']);self.assertIsNone(result['available_shares'])
    def test_settings_change_invalidates_inflight_and_preserves_boundary(self):
        leader=self.add();started=leader['started_at'];row=observer._trade(trade(started+1),W)
        settings={'mode':'percent','amount':'25','cap':'3','interval_seconds':20}
        def changed(*args):observer.update_settings(leader['id'],settings);return [row]
        with patch.object(observer,'fetch_trades',side_effect=changed),patch.object(observer.orderbook,'fetch_batch',return_value={}):observer.poll_leader(leader)
        current=observer.status()['leaders'][0]
        self.assertEqual(observer.status()['signals'],[]);self.assertEqual(current['started_at'],started)
        self.assertEqual(current['mode'],'percent');self.assertEqual(current['interval_seconds'],20)
        self.assertGreater(current['revision'],leader['revision'])
        with patch.object(observer,'fetch_trades',return_value=[row]),patch.object(observer.orderbook,'fetch_batch',return_value={}):observer.poll_leader(current)
        self.assertEqual(observer.status()['signals'][0]['estimate']['desired_shares'],'2.500000')

    def test_error_and_pause_race_never_write_new_signals(self):
        leader=self.add();started=leader['started_at'];row=observer._trade(trade(started+1),W)
        with patch.object(observer,'fetch_trades',side_effect=ValueError('untrusted')):
            observer.poll_leader(leader)
        self.assertEqual(observer.status()['leaders'][0]['status'],'error');self.assertEqual(observer.status()['signals'],[])
        def pause(*args):observer.set_enabled(leader['id'],False);return [row]
        with patch.object(observer,'fetch_trades',side_effect=pause),patch.object(observer.orderbook,'fetch_batch',return_value={}):observer.poll_leader(leader)
        self.assertFalse(observer.status()['leaders'][0]['enabled']);self.assertEqual(observer.status()['signals'],[])
        observer.set_enabled(leader['id'],True);current=observer.status()['leaders'][0]
        self.assertGreater(current['revision'],leader['revision']);self.assertIsNone(current['last_success'])
        observer.delete_leader(leader['id']);self.assertEqual(observer.status()['leaders'],[])
    def test_bounded_backlog_and_public_books_no_credentials(self):
        leader=self.add();started=leader['started_at']
        many=[observer._trade(trade(started+1,token_id=str(i)),W) for i in range(observer.MAX_NEW+1)]
        from web import trading_account, trading_orders
        with patch.object(observer,'fetch_trades',return_value=many),patch.object(observer.orderbook,'fetch_batch') as books, \
             patch.object(trading_account,'load',side_effect=AssertionError('must not load keys')), \
             patch.object(trading_orders,'place',side_effect=AssertionError('must not trade'),create=True), \
             patch.object(trading_orders,'cancel',side_effect=AssertionError('must not cancel')):
            observer.poll_leader(leader,started+3)
            books.assert_not_called()
        result=observer.status();self.assertEqual(result['signals'],[])
        self.assertIn('Слишком много',result['leaders'][0]['error_reason'])
        some=many[:11]
        with patch.object(observer,'fetch_trades',return_value=some),patch.object(observer.orderbook,'fetch_batch',return_value={}) as books:
            observer.poll_leader(leader,started+4)
            self.assertEqual(len(books.call_args.args[0]),10)
        self.assertEqual(len(observer.status()['signals']),11)
        self.assertTrue(all(not s['estimate']['book_available'] for s in observer.status()['signals']))

    def test_failed_poll_preserves_success_and_does_not_leak_exception(self):
        leader=self.add();started=leader['started_at']
        with patch.object(observer,'fetch_trades',return_value=[]):observer.poll_leader(leader,started+2)
        leader=observer.status()['leaders'][0]
        with patch.object(observer,'fetch_trades',side_effect=RuntimeError('secret error')):observer.poll_leader(leader,started+4)
        result=observer.status()['leaders'][0]
        self.assertEqual(result['last_success'],started+2);self.assertNotIn('secret',result['error_reason'])

    def test_service_heartbeat_and_singleton_lock(self):
        from web import trading_account
        with patch.object(trading_account.config,'BASE_DIR',self.tmp.name):
            fd=observer.acquire_lock()
            try:
                with self.assertRaises(OSError):observer.acquire_lock()
            finally:os.close(fd)
        self.assertEqual(observer.status()['service'],'not_running');observer.poll_once()
        self.assertEqual(observer.status()['service'],'running')
        with patch.object(observer.time,'time',return_value=time.time()+91):self.assertEqual(observer.status()['service'],'stale')
    def test_endpoint_requires_auth_tunnel_and_csrf(self):
        config=dict(web.app.config)
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password long enough'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False,TESTING=True)
            c=web.app.test_client();base='http://127.0.0.1:8108'
            c.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
            self.assertEqual(c.get('/api/copy-observer',base_url=base).status_code,401)
            with c.session_transaction(base_url=base) as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
            self.assertEqual(c.get('/api/copy-observer',base_url=base).status_code,200)
            self.assertEqual(c.get('/api/copy-observer',base_url=base,environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
            data={'profile':W,'mode':'fixed','amount':'2','cap':'5','interval_seconds':30,'confirm':True}
            self.assertEqual(c.post('/api/copy-observer',base_url=base,json=data).status_code,403)
            with patch.object(observer,'resolve_profile',return_value=(W,'A')):
                self.assertEqual(c.post('/api/copy-observer',base_url=base,json=data,headers={'X-CSRF-Token':'token'}).status_code,201)
        finally:web.app.config.clear();web.app.config.update(config)

if __name__=='__main__':unittest.main()
