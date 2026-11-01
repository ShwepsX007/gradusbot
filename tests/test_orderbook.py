import unittest
from unittest.mock import patch, Mock

from web import orderbook
from web import app as web


class BookTests(unittest.TestCase):
    def test_explicit_yes_no_mapping_only(self):
        event = {'markets': [
            {'outcomes': '["No", "Yes"]', 'clobTokenIds': '["123", "456"]'},
            {'outcomes': ['Yes', 'No'], 'clobTokenIds': ['777', '888']},
            {'outcomes': ['Yes', 'Maybe'], 'clobTokenIds': ['999', '666']},
            {'clobTokenIds': ['55', '66']},
            {'outcomes': ['Yes', 'No'], 'clobTokenIds': ['0xabc', '7']},
            {'outcomes': ['Yes', 'No'], 'clobTokenIds': ['5', '5']},
        ]}
        self.assertEqual(orderbook.event_tokens(event), [{'yes':'456','no':'123'}, {'yes':'777','no':'888'}])

    def test_depth_order_aggregation_and_invalid_values(self):
        levels = [{'price':'0.2','size':'5'}, {'price':'0.6','size':'3'}, {'price':'0.4','size':'2'},
                  {'price':'0.6','size':'4'}, {'price':'NaN','size':'9'}, {'price':'0.8','size':'-1'}]
        self.assertEqual(orderbook._levels(levels, True), [
            {'price':'0.6','size':'7'}, {'price':'0.4','size':'2'}, {'price':'0.2','size':'5'}])
        self.assertEqual(orderbook._levels(levels, False)[0]['price'], '0.2')

    def test_fetch_book_empty_failure_and_wrong_asset(self):
        response = Mock();response.json.return_value = {'asset_id':'123', 'bids':[], 'asks':[]}
        with patch.object(orderbook.requests, 'get', return_value=response):
            self.assertEqual(orderbook.fetch_book('123')['bids'], [])
            self.assertIsNone(orderbook.fetch_book('234'))
            response.json.return_value = {'asset_id':'123', 'bids':[]}
            self.assertIsNone(orderbook.fetch_book('123'))
        with patch.object(orderbook.requests, 'get', side_effect=orderbook.requests.Timeout):
            self.assertIsNone(orderbook.fetch_book('123'))

    def test_batch_parsing_and_closed_book_fallback(self):
        response=Mock(status_code=200)
        response.json.return_value=[{'asset_id':'1', 'bids':[{'price':'0.3','size':'4'}], 'asks':[]},
                                    {'asset_id':'unexpected','bids':[],'asks':[]}]
        with patch.object(orderbook.requests, 'post', return_value=response):
            data=orderbook.fetch_batch(['1','2'])
        self.assertEqual(data['1']['bids'][0]['price'], '0.3')
        self.assertIsNone(data['2'])
        response.status_code=404
        with patch.object(orderbook.requests, 'post', return_value=response), patch.object(orderbook, 'fetch_book', side_effect=lambda token: None if token=='2' else {'token':token}) as fetch:
            data=orderbook.fetch_batch(['1','2'])
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(data['2'])
        response.status_code=429
        response.raise_for_status.side_effect=orderbook.requests.HTTPError()
        with patch.object(orderbook.requests,'post',return_value=response), patch.object(orderbook,'fetch_book') as fetch:
            self.assertEqual(orderbook.fetch_batch(['1','2']),{'1':None,'2':None})
            fetch.assert_not_called()

    def test_batch_cap_and_partial_failure(self):
        event={'markets':[{'outcomes':['Yes','No'], 'clobTokenIds':[str(i*2+1), str(i*2+2)]} for i in range(60)]}
        with patch.object(orderbook, 'fetch_batch', side_effect=lambda tokens:{token:None if token=='2' else {'token':token} for token in tokens}) as fetch:
            data=orderbook.fetch_event_books(event)
        self.assertTrue(data['limited'])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(data['books']), orderbook.MAX_TOKENS)
        self.assertIsNone(data['books']['2'])
        self.assertNotIn('101',data['books'])

    def test_api_auth_cache_and_failure(self):
        config=dict(web.app.config)
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True, WEB_USERNAME='testuser', WEB_PASSWORD_HASH='testhash')
            web._books_cache.clear()
            anonymous=web.app.test_client()
            self.assertEqual(anonymous.get('/api/market/test/books').status_code,401)
            with anonymous.session_transaction() as sess:
                sess['auth_user']='testuser';sess['auth_version']=web.auth_version()
            event={'title':'Test','markets':[]}
            with patch.object(web, 'event_for_station', return_value=event), patch.object(web, 'fetch_event_books', return_value={'pairs':[], 'books':{}, 'limited':False,'requested_at':1}) as fetch:
                self.assertEqual(anonymous.get('/api/market/test/books').status_code,200)
                self.assertEqual(anonymous.get('/api/market/test/books').status_code,200)
                self.assertEqual(fetch.call_count,1)
            with patch.object(web, 'event_for_station', return_value=None):
                self.assertEqual(anonymous.get('/api/market/other/books').status_code,502)
        finally:
            web.app.config.clear();web.app.config.update(config);web._books_cache.clear()


if __name__ == '__main__': unittest.main()
