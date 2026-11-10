import time
import unittest
from unittest.mock import Mock, patch
from web import app as web, price_history


class PriceHistoryTests(unittest.TestCase):
    def test_request_sanitizes_sorts_and_limits(self):
        response=Mock();response.json.return_value={'history':[{'t':200,'p':'.25'},{'t':100,'p':0},{'t':200,'p':'.3'}, {'t':300,'p':'NaN'}, {'t':400,'p':2}, {'t':True,'p':.1}, {'t':time.time()+1000,'p':.5}]}
        with patch.object(price_history.requests,'get',return_value=response) as get:
            data=price_history.fetch_history('123','1d')
        self.assertEqual(data['history'],[{'t':100.0,'p':0.0},{'t':200.0,'p':0.3}])
        self.assertEqual(get.call_args.kwargs['params'],{'market':'123','interval':'1d','fidelity':15})
        response.json.return_value={'history':[{'t':i+1,'p':.5} for i in range(3000)]}
        with patch.object(price_history.requests,'get',return_value=response):data=price_history.fetch_history('123','max')
        self.assertEqual(len(data['history']),2000);self.assertEqual(data['history'][0]['t'],1);self.assertEqual(data['history'][-1]['t'],3000)
        response.json.return_value={'bad':[]}
        with patch.object(price_history.requests,'get',return_value=response):self.assertIsNone(price_history.fetch_history('123','1d'))
        with patch.object(price_history.requests,'get',side_effect=price_history.requests.Timeout):self.assertIsNone(price_history.fetch_history('123','1d'))

    def test_route_checks_auth_token_side_caches_and_fails_closed(self):
        config=dict(web.app.config);web._history_cache.clear()
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH='hash')
            client=web.app.test_client();path='/api/market/event/price-history?token=123&side=no&period=1d'
            self.assertEqual(client.get(path).status_code,401)
            with client.session_transaction() as s:s['auth_user']='test';s['auth_version']=web.auth_version()
            event={'title':'test','markets':[{'outcomes':['No','Yes'],'clobTokenIds':['123','456']}]}
            result={'history':[{'t':100,'p':.3}],'fidelity_minutes':15,'fetched_at':100}
            with patch.object(web,'event_for_station',return_value=event),patch.object(web.book_history,'read',return_value=[{'t':200,'bid':.2,'ask':.4}]),patch.object(price_history,'fetch_history',return_value=result) as fetch:
                data=client.get(path).get_json();self.assertEqual(data['history'],result['history']);self.assertEqual(data['quotes'][0]['bid'],.2);self.assertIsNone(data['history_error'])
                self.assertEqual(client.get(path).status_code,200);self.assertEqual(fetch.call_count,1)
                self.assertEqual(client.get('/api/market/event/price-history?token=123&side=yes').status_code,404)
                self.assertEqual(client.get('/api/market/event/price-history?token=999&side=no').status_code,404)
                self.assertEqual(client.get('/api/market/event/price-history?token=123&side=no&period=bad').status_code,400)
                self.assertEqual(client.get('/api/market/event/price-history?token=12x&side=no').status_code,400)
                self.assertEqual(fetch.call_count,1)
            # Price cache is reused, but locally recorded quotes are read afresh.
            with patch.object(web,'event_for_station',return_value=event),patch.object(web.book_history,'read',return_value=[{'t':300,'bid':.3,'ask':.5}]),patch.object(price_history,'fetch_history') as fetch:
                self.assertEqual(client.get(path).get_json()['quotes'][0]['t'],300);fetch.assert_not_called()
            with patch.object(web,'event_for_station',return_value=event),patch.object(web.book_history,'read',return_value=[{'t':200,'bid':.2,'ask':.4}]),patch.object(price_history,'fetch_history',return_value=None):
                data=client.get('/api/market/event/price-history?token=123&side=no&period=1h').get_json();self.assertEqual(data['quotes'][0]['bid'],.2);self.assertTrue(data['history_error']);self.assertEqual(data['history'],[])
            with patch.object(web,'event_for_station',return_value=None):self.assertEqual(client.get(path).status_code,502)
        finally:
            web.app.config.clear();web.app.config.update(config);web._history_cache.clear()


if __name__=='__main__':unittest.main()
