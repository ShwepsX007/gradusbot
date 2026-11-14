import os, tempfile, time, unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import database as db
from web import auto_scalp as scalp

T='123';N='456';C='0x'+'a'*64

def market(now):
    date=datetime.fromtimestamp(now,timezone.utc)
    slug='highest-temperature-in-london-on-'+date.strftime('%B').lower()+'-'+str(date.day)+'-'+str(date.year)
    return {'slug':slug,'title':'Highest temperature in London','active':True,'closed':False,
      'endDate':(date+timedelta(hours=24)).isoformat(),'markets':[{'active':True,'closed':False,'acceptingOrders':True,
      'groupItemTitle':'20°C','conditionId':C,'outcomes':['No','Yes'],'outcomePrices':['0.9','0.1'],'clobTokenIds':[T,N]}]}

def book(now,bid='0.89',ask='0.90',bs='100',asz='100'):
    return {'bids':[{'price':bid,'size':bs}], 'asks':[{'price':ask,'size':asz}], 'fetched_at':now}

class ScalpDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_FILE;db.DB_FILE=os.path.join(self.tmp.name,'test.db');scalp.init_db()
        self.now=time.time();self.items=scalp.candidates([market(self.now)],self.now)[0]
    def tearDown(self):db.DB_FILE=self.old;self.tmp.cleanup();scalp._discovery=None;scalp._cursor=0
    def test_candidate_mapping_exact_ranges_and_date(self):
        self.assertEqual(self.items[0]['token'],T)
        m=market(self.now);m['markets'][0]['groupItemTitle']='20°C or below';self.assertEqual(scalp.candidates([m],self.now)[0][0]['temperature_range'],(None,20.0))
        m=market(self.now);m['markets'][0]['outcomePrices']=['0.8','0.2'];self.assertEqual(scalp.candidates([m],self.now)[0],[])
        m=market(self.now);m['markets'][0]['positionIds']=['1','2'];self.assertEqual(scalp.candidates([m],self.now)[0],[])
        m=market(self.now);m['endDate']=(datetime.fromtimestamp(self.now,timezone.utc)+timedelta(hours=49)).isoformat();self.assertEqual(scalp.candidates([m],self.now)[0],[])
        m=market(self.now);m['markets'][0]['outcomes']=['Yes','No'];m['markets'][0]['outcomePrices']=['0.1','0.9'];self.assertEqual(scalp.candidates([m],self.now)[0][0]['token'],N)
    def test_virtual_entry_target_stop_partial_sale_and_no_replay(self):
        scalp.set_enabled(True);scalp.process_books(self.items,{T:book(self.now)},self.now)
        state=scalp.status();p=state['positions'][0]
        self.assertEqual(p['target'],'0.95');self.assertLessEqual(float(p['entry_fee'])+float(p['entry_price'])*float(p['original']),50)
        scalp.process_books(self.items,{T:book(self.now)},self.now);self.assertEqual(len(scalp.status()['positions']),1)
        scalp.set_enabled(False)
        later=self.now+10;scalp.process_books(self.items,{T:book(later,bid='0.95',bs='5')},later)
        p=scalp.status()['positions'][0];self.assertGreater(float(p['remaining']),0);self.assertEqual([x['kind'] for x in scalp.status()['journal']],['SELL','BUY'])
        scalp.process_books(self.items,{T:book(later,bid='0.95',bs='5')},later);self.assertEqual(len(scalp.status()['journal']),2)
        self.assertFalse(scalp.status()['config']['enabled']);self.assertFalse(scalp.status()['real_available'])
    def test_budget_limits_stale_snapshot_and_minimum_never_inflates(self):
        settings=dict(scalp.DEFAULT,trade_cap='4',total_cap='4',min_shares='5')
        scalp.set_settings(settings);scalp.set_enabled(True);scalp.process_books(self.items,{T:book(self.now)},self.now)
        self.assertEqual(scalp.status()['positions'],[])
        scalp.set_enabled(False);settings['trade_cap']='10';settings['total_cap']='10';scalp.set_settings(settings);scalp.set_enabled(True)
        scalp.process_books(self.items,{T:book(self.now)},self.now+16);self.assertEqual(scalp.status()['positions'],[])
        scalp.process_books(self.items,{T:book(self.now,asz='4')},self.now);self.assertEqual(scalp.status()['positions'],[])
    def test_fahrenheit_buckets_and_unknown_mappings(self):
        m=market(self.now)
        for label,unit in [('68°F','F'),('70-71°F','F'),('20°C','C'),('80°F or above','F')]:
            m['markets'][0]['groupItemTitle']=label
            item=scalp.candidates([m],self.now)[0][0]
            self.assertEqual(item['unit'],unit)
        m['markets'][0]['groupItemTitle']='70-20°F'
        self.assertEqual(scalp.candidates([m],self.now)[0],[])
        m['markets'][0]['groupItemTitle']='70°C to 71°F'
        self.assertEqual(scalp.candidates([m],self.now)[0],[])
    def test_trusted_station_source_noaa_and_hong_kong_fallback(self):
        m=market(self.now);m['resolutionSource']='https://www.weather.gov/wrh/timeseries?site=kord'
        self.assertEqual(scalp.candidates([m],self.now)[0][0]['station'],'KORD')
        m['resolutionSource']='https://example.test/?site=KORD'
        self.assertIsNone(scalp.candidates([m],self.now)[0][0]['station'])
        m['resolutionSource']='https://www.weather.gov/wrh/timeseries?site=kord';m['description']='Source https://www.weather.gov.hk/en/data'
        self.assertIsNone(scalp.candidates([m],self.now)[0][0]['station'])

    def test_forecast_fallback_persists_and_no_unvetted_input(self):
        cfg=dict(scalp.DEFAULT,forecast_mode='forecast',forecast_gap='3',forecast_gap_units='F')
        scalp.set_settings(cfg);scalp.set_enabled(True)
        # A caller cannot bypass the weather/fallback tagging step.
        scalp.process_books(self.items,{T:book(self.now)},self.now)
        self.assertEqual(scalp.status()['positions'],[])
        chosen,stats=scalp.prepare_forecasts(self.items,cfg,self.now)
        self.assertEqual(stats['fallback'],1)
        scalp.process_books(chosen,{T:book(self.now)},self.now)
        status=scalp.status();p=status['positions'][0]
        self.assertEqual(p['forecast_info']['mode'],'fallback')
        self.assertIn('Станция',p['forecast_info']['reason'])
        self.assertEqual(status['journal'][0]['forecast_info'],p['forecast_info'])
        self.assertFalse(status['real_available'])
    def test_obsolete_settings_revision_cannot_open_after_change(self):
        with db.get_db() as c:old=c.execute('SELECT revision FROM web_scalp_config WHERE id=1').fetchone()[0]
        scalp.set_enabled(True)
        scalp.process_books(self.items,{T:book(self.now)},self.now,expected_revision=old)
        self.assertEqual(scalp.status()['positions'],[])
        scalp.process_books(self.items,{T:book(self.now)},self.now,expected_revision=old+1)
        self.assertEqual(len(scalp.status()['positions']),1)
    def test_old_settings_do_not_silently_enable_forecast(self):
        import json
        old={k:v for k,v in scalp.DEFAULT.items() if not k.startswith('forecast_')}
        with db.get_db() as c:c.execute('UPDATE web_scalp_config SET settings=?',(json.dumps(old),))
        settings=scalp.status()['config']['settings']
        self.assertEqual(settings['forecast_mode'],'off');self.assertEqual(settings['forecast_gap'],'3')

    def test_poll_uses_forecast_then_records_entry(self):
        scalp.set_settings(dict(scalp.DEFAULT,forecast_mode='forecast',forecast_gap='3',forecast_gap_units='F'))
        scalp.set_enabled(True)
        m=market(self.now);m['resolutionSource']='https://www.weather.gov/wrh/timeseries?site=egll'
        m['markets'][0]['groupItemTitle']='86°F'
        day=datetime.fromtimestamp(self.now,timezone.utc).date();start=datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc).timestamp()
        p={'code':'EGLL','fetched_at':time.time(),'open':{'timezone':'UTC','hourly':{'epoch':[start+3600*i for i in range(24)],'temperature_2m':[20]*24}},'met':None,'met_status':'error'}
        with patch.object(scalp,'_gamma_events',return_value=([m],False)),patch.object(scalp.scalp_forecast,'get',return_value=(p,None)),patch.object(scalp.orderbook,'fetch_batch',side_effect=lambda tokens:{T:book(time.time())}):
            scalp.poll_once()
        state=scalp.status();self.assertEqual(state['config']['status'],'ok')
        info=state['positions'][0]['forecast_info'];self.assertEqual(info['mode'],'forecast');self.assertEqual(info['sources'],['Open-Meteo'])
        self.assertEqual(info['single_hours'],24);self.assertAlmostEqual(info['distance'],17.5)
        self.assertEqual(state['journal'][0]['forecast_info'],info)
        self.assertIn('прошли 1',state['config']['message'])
    def test_poll_fallback_reason_visible_in_status(self):
        scalp.set_settings(dict(scalp.DEFAULT,forecast_mode='forecast'));scalp.set_enabled(True)
        with patch.object(scalp,'_gamma_events',return_value=([market(self.now)],False)),patch.object(scalp.orderbook,'fetch_batch',side_effect=lambda tokens:{T:book(time.time())}):
            scalp.poll_once()
        state=scalp.status();self.assertEqual(state['config']['status'],'partial')
        self.assertIn('без прогноза / только цена 1',state['config']['message']);self.assertIn('Станция',state['config']['message'])
        self.assertEqual(state['positions'][0]['forecast_info']['mode'],'fallback')

    def test_validation_and_balance_immutability(self):
        for patching in [{'fee_rate':'NaN'},{'min_price':'0.99','max_price':'0.9'},{'interval_seconds':True},{'total_cap':'10','trade_cap':'20'}]:
            with self.assertRaises(scalp.ScalpError):scalp.validate_settings(dict(scalp.DEFAULT,**patching))
        scalp.set_enabled(True)
        with self.assertRaises(scalp.ScalpError):scalp.set_settings(scalp.DEFAULT)
        scalp.process_books(self.items,{T:book(self.now)},self.now);scalp.set_enabled(False)
        with self.assertRaises(scalp.ScalpError):scalp.set_settings(dict(scalp.DEFAULT,initial_balance='2000'))
    def test_discovery_error_does_not_stop_open_position_monitoring(self):
        scalp.set_enabled(True);scalp.process_books(self.items,{T:book(self.now)},self.now)
        later=time.time()
        with patch.object(scalp,'_gamma_events',side_effect=RuntimeError('secret')),patch.object(scalp.orderbook,'fetch_batch',return_value={T:book(later,bid='0.95')}):scalp.poll_once()
        state=scalp.status();self.assertEqual(state['positions'][0]['remaining'],'0.00');self.assertEqual(state['config']['status'],'partial');self.assertNotIn('secret',state['config']['message'])
    def test_demo_does_not_load_keys_or_submit_orders(self):
        scalp.set_enabled(True)
        with patch.object(scalp.trading_account,'load',side_effect=AssertionError('keys forbidden')):
            scalp.process_books(self.items,{T:book(self.now)},self.now)
        self.assertEqual(len(scalp.status()['journal']),1)
        entry=scalp.status()['journal'][0];self.assertEqual(entry['token'],T);self.assertEqual(entry['condition_id'],C);self.assertEqual(entry['event_slug'],market(self.now)['slug'])

    def test_api_rejects_live_and_requires_auth_tunnel_csrf(self):
        from web import app as web
        from werkzeug.security import generate_password_hash
        config=dict(web.app.config)
        db.init_db();web.init_web_db()
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password long enough'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False,TESTING=True)
            c=web.app.test_client();base='http://127.0.0.1:8108'
            c.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
            self.assertEqual(c.get('/api/auto-scalp',base_url=base).status_code,401)
            with c.session_transaction(base_url=base) as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
            self.assertEqual(c.get('/api/auto-scalp',base_url=base,environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
            body={'mode':'demo','action':'start','confirm':True}
            self.assertEqual(c.post('/api/auto-scalp',base_url=base,json=body).status_code,403)
            self.assertEqual(c.post('/api/auto-scalp',base_url=base,json={**body,'mode':'real'},headers={'X-CSRF-Token':'token'}).status_code,400)
            self.assertFalse(scalp.status()['config']['enabled'])
            self.assertEqual(c.post('/api/auto-scalp',base_url=base,json=body,headers={'X-CSRF-Token':'token'}).status_code,200)
            self.assertTrue(scalp.status()['config']['enabled'])
        finally:web.app.config.clear();web.app.config.update(config)

if __name__=='__main__':unittest.main()
