import os
import tempfile
import unittest
from unittest.mock import patch

from web import app as web


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = web.db.DB_FILE
        web.db.DB_FILE = os.path.join(self.tmp.name, 'test.db')
        web.db.init_db()
        web._cache.clear()
        self.client = web.app.test_client()

    def tearDown(self):
        web.db.DB_FILE = self.old_db
        self.tmp.cleanup()

    def add_station(self):
        return self.client.post('/api/stations', json={'name': 'London', 'code': 'EGLL'}).get_json()

    def test_station_disabled_and_no_upstream_requests(self):
        s = self.add_station()
        self.assertEqual(s['enabled'], 0)
        with patch.object(web.utils, 'fetch_metar') as fetch:
            result = self.client.get(f"/api/stations/{s['id']}/weather").get_json()
            fetch.assert_not_called()
        self.assertIsNone(result['metar'])
        self.assertIn('выключен', result['message'])

    def test_weather_uses_bot_metar_and_forecast(self):
        s = self.add_station()
        self.client.patch(f"/api/stations/{s['id']}", json={'enabled': True})
        with patch.object(web.utils, 'fetch_metar', return_value={'temp': 20, 'dew_point': 10}), \
             patch.object(web, 'station_info', return_value={'lat': 51.47, 'lon': -0.45}), \
             patch.object(web, 'forecast', return_value={'timezone': 'Europe/London'}):
            result = self.client.get(f"/api/stations/{s['id']}/weather").get_json()
        self.assertEqual(result['metar']['humidity'], 53)
        self.assertEqual(result['forecast']['timezone'], 'Europe/London')

    def test_market_binding_and_positions(self):
        s = self.add_station()
        payload = {'title': 'Temperature in London', 'options': [], 'closed': False}
        with patch.object(web.utils, 'fetch_market', return_value=payload):
            r = self.client.post(f"/api/stations/{s['id']}/bindings", json={'slug': 'https://polymarket.com/event/london-test'})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(self.client.post(f"/api/stations/{s['id']}/bindings", json={'slug': 'london-test'}).status_code, 409)
            self.assertEqual(self.client.get('/api/market/london-test').get_json()['market'], payload)
            self.assertEqual(self.client.delete(f"/api/bindings/{r.get_json()['id']}", json={}).status_code, 200)
            self.assertEqual(self.client.get(f"/api/stations/{s['id']}/bindings").get_json(), [])

    def test_station_delete_cascades_only_its_data(self):
        s = self.add_station()
        sid = s['id']
        mid = web.db.add_market('Weather', 'weather-london')
        bid = web.db.add_binding(sid, 'weather-london', 'Weather')
        web.db.set_binding_setting(bid, 'enabled', '1')
        web.db.add_station_history(sid, 15)
        web.db.set_setting(f'st_{sid}_metar_m1', '55')
        r = self.client.delete(f'/api/stations/{sid}', json={})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(web.db.get_station(sid))
        self.assertEqual(web.db.get_station_history(sid), [])
        self.assertEqual(web.db.get_bindings(sid), [])
        self.assertIsNone(web.db.get_setting(f'bind_{bid}_enabled', None))
        self.assertIsNone(web.db.get_setting(f'st_{sid}_metar_m1', None))
        self.assertIsNotNone(web.db.get_market(mid))

    def test_market_delete_cascades_bindings_but_not_other_markets(self):
        sid = self.add_station()['id']
        mid = web.db.add_market('Weather', 'weather-london')
        bid = web.db.add_binding(sid, 'weather-london', 'Weather')
        web.db.set_binding_setting(bid, 'enabled', '1')
        web.db.add_market_history(mid, {'a': 50})
        other = web.db.add_market('Other', 'other')
        self.assertEqual(self.client.delete('/api/markets/weather-london', json={}).status_code, 200)
        self.assertIsNone(web.db.get_market(mid))
        self.assertIsNotNone(web.db.get_market(other))
        self.assertIsNone(web.db.get_binding(bid))
        self.assertIsNone(web.db.get_setting(f'bind_{bid}_enabled', None))
        self.assertEqual(web.db.get_market_history(mid), [])

    def test_deletion_blocked_by_local_position(self):
        sid = self.add_station()['id']
        web.db.add_market('Weather', 'weather-london')
        bid = web.db.add_binding(sid, 'weather-london', 'Weather')
        web.db.add_position(0, 'weather-london', 'token', 'BUY', 1, 0, 0, 0.5, 'Weather', 'Yes')
        for url in (f'/api/stations/{sid}', f'/api/bindings/{bid}', '/api/markets/weather-london'):
            self.assertEqual(self.client.delete(url, json={}).status_code, 409)
        self.assertIsNotNone(web.db.get_station(sid))
        self.assertIsNotNone(web.db.get_binding(bid))
        self.assertEqual(len(web.db.get_positions()), 1)

    def test_automatic_station_search_filters_weather_events(self):
        station = self.client.post('/api/stations', json={'name': 'Ньюйорк', 'code': 'KLGA'}).get_json()
        events = {'events': [
            {'slug': 'highest-temperature-in-new-york-on-june-15-2026', 'title': 'New York temperature'},
            {'slug': 'lowest-temperature-in-new-york-on-june-15-2026', 'title': 'New York low'},
            {'slug': 'highest-temperature-in-london-on-june-15-2026', 'title': 'London temperature'},
            {'slug': 'highest-temperature-in-new-york-on-june-16-2026', 'title': 'Closed', 'closed': True},
        ]}
        with patch.object(web, 'upstream', return_value=events) as fetch:
            result = self.client.get(f"/api/stations/{station['id']}/market-search").get_json()
        self.assertEqual(result['city'], 'new york')
        self.assertEqual(len(result['events']), 2)
        self.assertEqual(fetch.call_args.args[1]['q'], 'temperature in new york')

    def test_auto_search_unknown_station_uses_manual_fallback(self):
        station = self.client.post('/api/stations', json={'name': 'Неизвестно', 'code': 'ZZZZ'}).get_json()
        with patch.object(web, 'upstream') as fetch:
            result = self.client.get(f"/api/stations/{station['id']}/market-search").get_json()
        fetch.assert_not_called()
        self.assertEqual(result['events'], [])
        self.assertIn('ручной поиск', result['message'])

    def test_station_urls_are_never_exposed(self):
        sid = web.db.add_station('Private', 'https://example.test/?apiKey=secret', '', 'KJFK', 'metar')
        for url in ('/api/stations', f'/api/stations/{sid}/weather'):
            response = self.client.get(url).get_data(as_text=True)
            self.assertNotIn('secret', response)
            self.assertNotIn('api_url', response)

    def test_validation_and_origin(self):
        self.assertEqual(self.client.post('/api/stations', json={'name': 'x', 'code': '../bad'}).status_code, 400)
        self.assertEqual(self.client.post('/api/stations', json={'name': 'x', 'code': 'EGLL'}, headers={'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/settings', json={'metar_interval': 5, 'awc_rate_per_min': 20, 'm_interval': 30}).status_code, 400)
        self.assertIsNone(web.valid_slug('https://example.com/event/test'))


if __name__ == '__main__':
    unittest.main()
