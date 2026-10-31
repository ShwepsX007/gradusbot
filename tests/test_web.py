import os
import tempfile
import time
import unittest
from unittest.mock import patch

from web import app as web


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = web.db.DB_FILE
        web.db.DB_FILE = os.path.join(self.tmp.name, 'test.db')
        web.db.init_db()
        web.init_web_db()
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

    def test_weather_decodes_cloud_precipitation_and_wind(self):
        station = self.add_station()
        self.client.patch(f"/api/stations/{station['id']}", json={'enabled': True})
        metar = {'temp': 16, 'dew_point': 12, 'wind_speed': 8,
                 'wx_phrase': 'FEW', 'raw_metar': 'EGLL 201200Z 21008KT -RA FEW020 BKN060 16/12 Q1012'}
        with patch.object(web.utils, 'fetch_metar', return_value=metar), patch.object(web, 'station_info', return_value=None):
            result = self.client.get(f"/api/stations/{station['id']}/weather").get_json()['metar']
        self.assertEqual(result['wind_ms'], 4.1)
        self.assertEqual(result['cloud'], {'description': 'Преимущественно облачно', 'code': 'BKN'})
        self.assertEqual(result['precipitation']['description'], 'Слабые осадки: дождь')

    def test_forecast_requests_precipitation_probability(self):
        with patch.object(web, 'upstream', return_value={}) as upstream:
            web.forecast(51.0, 0.0)
        self.assertIn('precipitation_probability', upstream.call_args.args[1]['hourly'])

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

    def test_station_rename_preserves_status_and_rejects_invalid(self):
        station = self.add_station()
        sid = station['id']
        self.client.patch(f'/api/stations/{sid}', json={'enabled': True})
        response = self.client.patch(f'/api/stations/{sid}', json={'name': ' Heathrow '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['name'], 'Heathrow')
        self.assertEqual(response.get_json()['enabled'], 1)
        self.assertEqual(self.client.patch(f'/api/stations/{sid}', json={'name': ' '}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/stations/{sid}', json={'name': None}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/stations/{sid}', json={'name': 'X', 'enabled': 1}).status_code, 400)
        self.assertEqual(web.db.get_station(sid)['name'], 'Heathrow')

    def test_market_rename_updates_all_bindings_and_market(self):
        first = self.add_station()['id']
        second = web.db.add_station('Paris', '', '', 'LFPG', 'metar')
        mid = web.db.add_market('Old', 'test-market')
        bid1 = web.db.add_binding(first, 'test-market', 'Old')
        bid2 = web.db.add_binding(second, 'test-market', 'Old')
        self.assertEqual(self.client.patch('/api/markets/test-market', json={'name': ' New name '}).status_code, 200)
        self.assertEqual(web.db.get_market(mid)['name'], 'New name')
        self.assertEqual(web.db.get_binding(bid1)['market_name'], 'New name')
        self.assertEqual(web.db.get_binding(bid2)['market_name'], 'New name')
        self.assertEqual(self.client.patch('/api/markets/test-market', json={'name': ''}).status_code, 400)
        self.assertEqual(self.client.patch('/api/markets/nonexistent', json={'name': 'New'}).status_code, 404)
        self.assertEqual(self.client.get('/api/markets').get_json()[0]['name'], 'New name')

    def test_units_are_validated_and_saved_with_bot_settings(self):
        data = {'metar_interval': 120, 'awc_rate_per_min': 20, 'm_interval': 30, 'units': 'B'}
        self.assertEqual(self.client.post('/api/settings', json=data).status_code, 200)
        self.assertEqual(web.db.get_setting('units'), 'B')
        data.update(units='K', metar_interval=180)
        self.assertEqual(self.client.post('/api/settings', json=data).status_code, 400)
        self.assertEqual(web.db.get_setting('metar_interval'), '120')
        self.assertEqual(self.client.get('/api/settings').get_json()['units'], 'B')

    def test_web_metar_history_is_deduplicated_and_separate(self):
        sid = self.add_station()['id']
        self.client.patch(f'/api/stations/{sid}', json={'enabled': True})
        obs = int(time.time()) - 60
        metar = {'temp': 21, 'dew_point': 12, 'obs_time': obs}
        with patch.object(web.utils, 'fetch_metar', return_value=metar), patch.object(web, 'station_info', return_value=None):
            first = self.client.get(f'/api/stations/{sid}/weather').get_json()
            second = self.client.get(f'/api/stations/{sid}/weather').get_json()
        self.assertEqual(first['history'], [[obs, 21.0]])
        self.assertEqual(second['history'], first['history'])
        self.assertEqual(web.db.get_station_history(sid), [])
        self.assertEqual(self.client.delete(f'/api/stations/{sid}', json={}).status_code, 200)
        with web.db.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM web_metar_history').fetchone()[0], 0)

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
