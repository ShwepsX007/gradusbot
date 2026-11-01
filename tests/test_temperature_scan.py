import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from web import app as web
from web.temperature_scan import observation_date, event_summary, apply_timezone_batch, yes_price_summary


def event(slug='highest-temperature-in-manila-on-november-2-2026', source='https://aviationweather.gov/api/data/metar?ids=RPLL&format=raw'):
    return {'slug':slug,'title':'Highest temperature in Manila?', 'resolutionSource':source,
            'active':True,'closed':False,'markets':[{'active':True,'closed':False,'acceptingOrders':True}]}


class ScanTests(unittest.TestCase):
    def test_dates_and_strict_source(self):
        self.assertEqual(observation_date('highest-temperature-in-manila-on-november-2-2026'),'2026-11-02')
        self.assertEqual(observation_date('lowest-temperature-in-new-york-on-february-29-2024'),'2024-02-29')
        self.assertIsNone(observation_date('highest-temperature-in-manila-on-february-29-2026'))
        self.assertIsNone(observation_date('october-2026-temperature-increase-c'))
        item=event_summary(event())
        self.assertEqual(item['station_code'],'RPLL')
        self.assertEqual(item['station']['candidate']['kind'],'Aviation Weather Center')
        self.assertIsNone(item['timezone'])
        self.assertIsNone(event_summary({**event(), 'closed':True}))
        self.assertIsNone(event_summary({**event(), 'active':False}))
        self.assertIsNone(event_summary({**event(), 'slug':'../../../bad'}))

    def test_hong_kong_observatory_timezone_never_invents_station(self):
        hk=event('highest-temperature-in-hong-kong-on-november-2-2026','')
        hk['description']='Resolution: https://www.weather.gov.hk/en/cis/climat.htm'
        result=event_summary(hk)
        self.assertEqual(result['timezone'],'Asia/Hong_Kong')
        self.assertIsNone(result['station_code'])
        self.assertEqual(result['station']['status'],'unknown')
        hk['description']='https://www.weather.gov.hk.evil.example/en/cis/climat.htm'
        self.assertIsNone(event_summary(hk)['timezone'])
        hk['slug']='highest-temperature-in-manila-on-november-2-2026'
        hk['description']='https://www.weather.gov.hk/en/cis/climat.htm'
        self.assertIsNone(event_summary(hk)['timezone'])

    def test_yes_price_summary_uses_explicit_labels_and_highest_odds(self):
        markets = [
            {'groupItemTitle':'20°C', 'outcomes':'["Yes", "No"]', 'outcomePrices':'["0.35", "0.65"]', 'acceptingOrders':True},
            {'groupItemTitle':'21°C', 'outcomes':['No','Yes'], 'outcomePrices':['0.72','0.28'], 'acceptingOrders':True},
            {'groupItemTitle':'22°C', 'outcomes':['Yes','No'], 'outcomePrices':['0.2','0.8'], 'acceptingOrders':True},
            {'question':'23°C or above?', 'outcomes':['Yes','No'], 'outcomePrices':[0.05,0.95]},
            {'groupItemTitle':'zero', 'outcomes':['Yes','No'], 'outcomePrices':[0,1], 'acceptingOrders':True},
            {'groupItemTitle':'suspended', 'outcomes':['Yes','No'], 'outcomePrices':[0.9,0.1], 'acceptingOrders':False},
            {'groupItemTitle':'closed', 'outcomes':['Yes','No'], 'outcomePrices':[0.99,0.01], 'closed':True},
            {'groupItemTitle':'not active', 'outcomes':['Yes','No'], 'outcomePrices':[1,0], 'active':False},
        ]
        top, maximum = yes_price_summary(markets)
        self.assertEqual([q['label'] for q in top],['20°C','21°C','22°C'])
        self.assertEqual([q['price'] for q in top],[0.35,0.28,0.2])
        self.assertEqual(maximum, {'label':'23°C or above?', 'price':0.05, 'accepting_orders':False})
        item = event();item['markets'] = markets
        self.assertEqual(event_summary(item)['top_yes_prices'],top)
        self.assertEqual(event_summary(item)['max_odds_yes'],maximum)

    def test_yes_price_summary_rejects_bad_or_ambiguous_data(self):
        def m(outcomes, prices): return {'groupItemTitle':'test','outcomes':outcomes,'outcomePrices':prices}
        bad = [m(['Yes','Maybe'],[0.5,0.5]),m(['Yes','Yes'],[0.5,0.5]),m(['Yes','No'],[0.5]),
               m('not json','[1,0]'),m(['Yes','No'],['NaN','0.5']),m(['Yes','No'],[True,0]),
               m(['Yes','No'],[-0.01,1]),m(['Yes','No'],[1.01,0]),m(['Yes','No'],[None,0]),
               {'outcomes':['Yes','No'],'outcomePrices':[0.5,0.5]}]
        self.assertEqual(yes_price_summary(bad),([],None))
        only_zero=m(['Yes','No'],[0,1])
        self.assertEqual(yes_price_summary([only_zero])[1],None)
        one=m(['Yes','No'],[1,0])
        self.assertEqual(yes_price_summary([one])[1]['price'],1)

    def test_open_meteo_batch_mapping_never_guess(self):
        data=[{'timezone':'Asia/Manila'},{'location_id':1,'timezone':'America/New_York'}]
        self.assertEqual(apply_timezone_batch(['RPLL','KLGA'],data),{'RPLL':'Asia/Manila','KLGA':'America/New_York'})
        self.assertEqual(apply_timezone_batch(['RPLL'],{'timezone':'Asia/Manila'}),{'RPLL':'Asia/Manila'})
        self.assertEqual(apply_timezone_batch(['RPLL','KLGA'],data[:1]),{})
        self.assertEqual(apply_timezone_batch(['RPLL','KLGA'],list(reversed(data))),{})
        self.assertEqual(apply_timezone_batch(['RPLL','KLGA'],[{'timezone':'Asia/Manila'},{'timezone':'America/New_York'}]),{})
        self.assertEqual(apply_timezone_batch(['RPLL'],{'timezone':'bad/unknown'}),{})

    def test_scan_pagination_and_partial_error(self):
        items=[event(),event('highest-temperature-in-london-on-november-2-2026','https://aviationweather.gov/api/data/metar?ids=EGLC')]
        with patch.object(web,'SCAN_PAGE_SIZE',2), patch.object(web,'upstream',side_effect=[items, [items[0]], []]) as get, \
             patch.object(web,'scan_station_zones',return_value={'RPLL':'Asia/Manila','EGLC':'Europe/London'}):
            data=web.build_temperature_scan()
        self.assertFalse(data['incomplete']); self.assertEqual(len(data['events']),2)
        self.assertEqual(data['events'][0]['timezone'],'Asia/Manila')
        self.assertGreater(data['events'][0]['quotes_at'],0)
        self.assertEqual(get.call_args_list[0].args[1]['tag_slug'],'daily-temperature')
        self.assertEqual(get.call_args_list[1].args[1]['offset'],2)
        self.assertEqual(get.call_args_list[2].args[1]['tag_slug'],'temperature')
        with patch.object(web,'SCAN_PAGE_SIZE',2),patch.object(web,'upstream',side_effect=[items,RuntimeError('offline'),[]]), \
             patch.object(web,'scan_station_zones',return_value={}):
            data=web.build_temperature_scan()
        self.assertTrue(data['incomplete']); self.assertEqual(data['unknown_timezones'],2)
        with patch.object(web,'upstream',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): web.build_temperature_scan()

    def test_page_cap_and_awc_backoff(self):
        with patch.object(web,'SCAN_PAGE_SIZE',1),patch.object(web,'SCAN_MAX_PAGES',1), \
             patch.object(web,'upstream',return_value=[event()]),patch.object(web,'scan_station_zones',return_value={}):
            data=web.build_temperature_scan()
        self.assertTrue(data['incomplete']);self.assertEqual(len(data['events']),1)
        r=Mock(status_code=429)
        with patch.object(web.utils.AWC_LIMITER,'allow',return_value=True), \
             patch.dict(web.utils._AWC_STATE,{'blocked_until':0}), \
             patch.object(web.utils.SESSION,'get',return_value=r),patch.object(web.utils,'_awc_penalty') as penalty:
            self.assertEqual(web.scan_station_locations(['RPLL'],time.monotonic()+10),{})
            penalty.assert_called_once_with(429)

    def test_batch_locations_rate_limit_and_timezone(self):
        r=Mock(status_code=200)
        r.json.return_value=[{'id':'RPLL','lat':14.5,'lon':121},{'id':'KLGA','lat':40.7,'lon':-73.8}]
        deadline=time.monotonic()+10
        with patch.object(web.utils.AWC_LIMITER,'allow',return_value=True), \
             patch.dict(web.utils._AWC_STATE,{'blocked_until':0}), \
             patch.object(web.utils.SESSION,'get',return_value=r) as get:
            coords=web.scan_station_locations(['RPLL','KLGA'],deadline)
        self.assertEqual(coords,{'RPLL':(14.5,121.0),'KLGA':(40.7,-73.8)})
        self.assertEqual(get.call_args.kwargs['params']['ids'],'RPLL,KLGA')
        web._scan_zones.clear()
        with patch.object(web,'scan_station_locations',return_value=coords), patch.object(web,'upstream',return_value=[{'timezone':'Asia/Manila'},{'location_id':1,'timezone':'America/New_York'}]):
            zones=web.scan_station_zones(['RPLL','KLGA'],deadline)
        self.assertEqual(zones,{'RPLL':'Asia/Manila','KLGA':'America/New_York'})
        with patch.object(web,'scan_station_locations') as loc:
            self.assertEqual(web.scan_station_zones(['RPLL','KLGA'],deadline),zones)
            loc.assert_not_called()
        web._scan_zones.clear()

    def test_scan_api_requires_auth_and_caches(self):
        previous=dict(web.app.config)
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='testuser',WEB_PASSWORD_HASH='testhash')
            web._scan_cache=None
            client=web.app.test_client()
            self.assertEqual(client.get('/api/temperature-scan').status_code,401)
            with client.session_transaction() as sess:
                sess['auth_user']='testuser';sess['auth_version']=web.auth_version()
            with patch.object(web,'build_temperature_scan',return_value={'events':[], 'incomplete':False}) as build:
                self.assertEqual(client.get('/api/temperature-scan').status_code,200)
                self.assertEqual(client.get('/api/temperature-scan').status_code,200)
                self.assertEqual(build.call_count,1)
        finally:
            web._scan_cache=None;web.app.config.clear();web.app.config.update(previous)

    def test_source_resolution_and_binding_are_idempotent(self):
        previous=dict(web.app.config)
        temp=tempfile.TemporaryDirectory();old_db=web.db.DB_FILE
        try:
            web.db.DB_FILE=os.path.join(temp.name,'test.db');web.db.init_db();web.init_web_db()
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='testuser',WEB_PASSWORD_HASH='testhash')
            client=web.app.test_client()
            with client.session_transaction() as sess:
                sess['auth_user']='testuser';sess['auth_version']=web.auth_version();sess['csrf']='token'
            client.environ_base['HTTP_X_CSRF_TOKEN']='token'
            slug=event()['slug']
            with patch.object(web,'event_for_station',return_value=event()):
                result=client.post('/api/market-station/resolve',json={'slug':slug}).get_json()
                self.assertEqual(result['candidate']['code'],'RPLL')
                first=client.post('/api/market-station/add',json={'slug':slug,'code':'RPLL','confirm':True}).get_json()
                second=client.post('/api/market-station/add',json={'slug':slug,'code':'RPLL','confirm':True}).get_json()
            self.assertTrue(first['created_station']);self.assertFalse(first['station']['enabled']);self.assertTrue(second['already_bound'])
            self.assertEqual(len(web.db.get_stations()),1);self.assertEqual(len(web.db.get_markets()),1)
        finally:
            web.db.DB_FILE=old_db;temp.cleanup();web.app.config.clear();web.app.config.update(previous)


if __name__ == '__main__': unittest.main()
