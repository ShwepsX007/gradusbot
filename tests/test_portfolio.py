import os
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from web import app as web, portfolio, trading_account as account

CREDS={'private_key':'0x'+'0'*63+'1','api_key':'secret-key','api_secret':'secret-test',
       'api_passphrase':'secret-pass','signature_type':'3','funder':'0x'+'2'*40}

class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=web.db.DB_FILE;web.db.DB_FILE=os.path.join(self.tmp.name,'test.db');web.db.init_db();web.init_web_db()
        self.p=patch.object(account.config,'BASE_DIR',self.tmp.name);self.p.start();account.save(CREDS)
    def tearDown(self):
        self.p.stop();web.db.DB_FILE=self.old;self.tmp.cleanup()
    def test_cursor_walk_preserves_filters_and_caps(self):
        seen=[]
        def get(path,params):
            seen.append((path,dict(params)))
            n=len(seen)
            return {'data':[{'n':n}],'pagination':{'has_more':True,'next_cursor':str(n)}}
        with patch.object(portfolio,'_get',side_effect=get):
            rows,partial=portfolio._pages('activity',CREDS['funder'],{'start':1})
        self.assertEqual(len(rows),portfolio.PAGE_LIMIT);self.assertTrue(partial)
        for i,(_,p) in enumerate(seen):
            self.assertEqual(p['user'],CREDS['funder']);self.assertEqual(p['start'],1)
            if i:self.assertEqual(p['cursor'],str(i))
        with patch.object(portfolio,'_get',return_value={'data':[],'pagination':{'has_more':True,'next_cursor':'same'}}):
            with self.assertRaises(account.AccountError):portfolio._pages('positions',CREDS['funder'])
    def test_snapshot_preserves_unavailable_and_isolates_failures(self):
        def get(path,params):
            if path=='positions':return {'data':[{'token_id':'5','event_slug':'event','current_size':3,'current_value':None,'total_pnl':None,'private':'secret'}], 'pagination':{'has_more':False,'next_cursor':None}}
            if path=='activity':return {'data':[], 'pagination':{'has_more':False,'next_cursor':None}}
            if path=='user-pnl':return {'data':{'points':[{'timestamp':100,'position_pnl':None},{'timestamp':200,'position_pnl':1.5}]}}
            raise RuntimeError('secret-test')
        with patch.object(portfolio,'_get',side_effect=get),patch.object(portfolio,'_open_orders',return_value=([],False)),patch.object(account,'_balance',return_value='2.000000'):
            snapshot=portfolio.snapshot()
        self.assertIsNone(snapshot['value']);self.assertIsNone(snapshot['positions'][0]['value'])
        self.assertIsNone(snapshot['pnl'][0]['value']);self.assertEqual(snapshot['pnl'][1]['value'],1.5)
        self.assertIn('Стоимость портфеля Data API недоступна.',snapshot['errors'])
        self.assertNotIn('secret',str(snapshot));self.assertFalse(snapshot['orders_partial'])
    def test_prices_and_books_are_not_inferred(self):
        def get(path, params):
            if path == 'positions':
                rows = [{'token_id':'12','event_slug':'event','current_size':4,'avg_price':0.35},
                        {'token_id':'13','current_size':2,'avg_price':None},
                        {'token_id':'14','current_size':2,'avg_price':0.4}]
                return {'data': rows, 'pagination': {'has_more':False,'next_cursor':None}}
            if path == 'activity':
                return {'data':[{'type':'TRADE','side':'SELL','price':0.65},{'type':'TRADE','side':'BUY','price':0.2}], 'pagination':{'has_more':False,'next_cursor':None}}
            if path == 'value': return {'data': {'value': 1}}
            return {'data': {'points': []}}
        books={'12':{'bids':[{'price':'0.6','size':'1.5'}], 'fetched_at':123},
               '13':{'bids':[], 'fetched_at':124}, '14':None}
        with patch.object(portfolio,'_get',side_effect=get), patch.object(portfolio.orderbook,'fetch_batch',return_value=books) as fetch, patch.object(portfolio,'_open_orders',return_value=([],False)), patch.object(account,'_balance',return_value='2'):
            result=portfolio.snapshot()
        fetch.assert_called_once_with(['12','13','14'])
        a,b,c=result['positions']
        self.assertEqual((a['avg_price'],a['bid'],a['bid_size'],a['bid_at']),(0.35,0.6,1.5,123))
        self.assertIsNone(b['bid']);self.assertEqual(b['bid_at'],124)
        self.assertIsNone(c['bid']);self.assertIsNone(c['bid_at'])
        self.assertEqual([a['price'] for a in result['activity']],[0.65,0.2])
        self.assertNotIn('avg_price',result['activity'][0])

    def test_quote_cap_and_invalid_tokens(self):
        rows=[{'token_id':str(i+1),'current_size':1} for i in range(105)]+[{'token_id':'invalid','current_size':1},{'token_id':'200','current_size':0}]
        def get(path,params):
            if path=='positions': return {'data':rows,'pagination':{'has_more':False,'next_cursor':None}}
            if path=='activity': return {'data':[],'pagination':{'has_more':False,'next_cursor':None}}
            if path=='value': return {'data':{'value':0}}
            return {'data':{'points':[]}}
        with patch.object(portfolio,'_get',side_effect=get),patch.object(portfolio.orderbook,'fetch_batch',return_value={}) as fetch,patch.object(portfolio,'_open_orders',return_value=([],False)),patch.object(account,'_balance',return_value='0'):
            result=portfolio.snapshot()
        self.assertTrue(result['quotes_partial']);self.assertEqual(sum(len(c.args[0]) for c in fetch.call_args_list),100)
        self.assertIsNone(result['positions'][-1]['bid'])

    def test_account_changed_while_reading(self):
        c=account.load()
        with patch.object(account,'load',side_effect=[c,{**c,'funder':'0x'+'3'*40}]),patch.object(portfolio,'_get',side_effect=RuntimeError()),patch.object(portfolio,'_open_orders',side_effect=RuntimeError()),patch.object(account,'_balance',side_effect=RuntimeError()):
            with self.assertRaises(account.AccountError):portfolio.snapshot()
    def test_clob_order_pagination_and_cycle(self):
        client=__import__('unittest.mock',fromlist=['Mock']).Mock();client.host=account.HOST
        client._get.side_effect=[{'data':[{'id':'a','size_matched':'2'}],'next_cursor':'next'},
                                 {'data':[{'id':'b'}],'next_cursor':'LTE='}]
        with patch.object(account,'build_client',return_value=client):
            rows,partial=portfolio._open_orders(account.load())
        self.assertEqual([x['id'] for x in rows],['a','b']);self.assertFalse(partial)
        self.assertEqual(client._get.call_args_list[1].kwargs['params']['next_cursor'],'next')
        client._get.side_effect=None;client._get.return_value={'data':[],'next_cursor':'MA=='}
        with patch.object(account,'build_client',return_value=client):
            with self.assertRaises(account.AccountError):portfolio._open_orders(account.load())

    def test_portfolio_requires_ssh_tunnel(self):
        with patch.object(web,'trusted_ssh_tunnel',return_value=False):
            config=dict(web.app.config)
            try:
                web.app.config.update(TESTING=True,WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('long password'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False)
                client=web.app.test_client()
                with client.session_transaction() as session:
                    session['auth_user']='test';session['auth_version']=web.auth_version();session['csrf']='csrf'
                response=client.get('/api/trading/portfolio')
                self.assertEqual(response.status_code,403)
            finally:
                web.app.config.clear();web.app.config.update(config)
