import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo
import database as db
from web import auto_scalp as scalp, scalp_ensemble as ens


def fixture(start, count, temperatures):
    stamps=[start+3600*i for i in range(72)]
    hourly={'time':stamps,'temperature_2m':[temperatures[0]]*72}
    for i in range(1,count):hourly[f'temperature_2m_member{i:02d}']=[temperatures[i]]*72
    return {'latitude':51.0,'longitude':0.0,'hourly_units':{'time':'unixtime','temperature_2m':'°C'},'hourly':hourly}

class EnsembleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_FILE;db.DB_FILE=os.path.join(self.tmp.name,'test.db');scalp.init_db()
        self.now=time.time();tomorrow=datetime.now(timezone.utc).date()+timedelta(days=1)
        self.day=tomorrow.isoformat();self.start=datetime.combine(tomorrow,datetime.min.time(),tzinfo=timezone.utc).timestamp()
        self.ec=fixture(self.start,51,[33]*10+[30]*41);self.gf=fixture(self.start,31,[33]*5+[30]*26)
        ens._candidates=[];ens._cursor=0
    def tearDown(self):db.DB_FILE=self.old;self.tmp.cleanup();ens._candidates=[];ens._cursor=0
    def test_counts_not_averaged_fahrenheit_and_ranges(self):
        a,error=ens.model_distribution(self.ec,self.day,'UTC','max','33°C',51,self.now)
        self.assertIsNone(error);self.assertEqual((a['yes_count'],a['no_count'],a['members']),(10,41,51));self.assertAlmostEqual(a['no'],41/51)
        b,error=ens.model_distribution(self.gf,self.day,'UTC','min','91-92°F',31,self.now)
        self.assertIsNone(error);self.assertEqual((b['yes_count'],b['no_count']),(5,26))
        low,_=ens.model_distribution(self.ec,self.day,'UTC','max','30°C or below',51,self.now)
        self.assertEqual(low['yes_count'],41)
        high,_=ens.model_distribution(self.ec,self.day,'UTC','max','33°C or above',51,self.now)
        self.assertEqual(high['yes_count'],10)
    def test_unavailable_instead_of_fake_certainty_or_hindsight(self):
        self.ec['hourly']['temperature_2m_member01'][3]=None
        result,error=ens.model_distribution(self.ec,self.day,'UTC','max','33°C',51,self.now)
        self.assertEqual(result['members'],50)
        for i in range(2,8):self.ec['hourly'][f'temperature_2m_member{i:02d}'][3]=None
        result,error=ens.model_distribution(self.ec,self.day,'UTC','max','33°C',51,self.now)
        self.assertIsNone(result);self.assertIn('Неполные',error)
        result,error=ens.model_distribution(self.gf,self.day,'UTC','max','33°C',31,self.start+1)
        self.assertIsNone(result);self.assertIn('METAR',error)
        self.gf['hourly']['time'][10]+=1
        result,error=ens.model_distribution(self.gf,self.day,'UTC','max','33°C',31,self.now)
        self.assertIsNone(result);self.assertIn('часов',error)
    def test_dst_and_half_hour_zone(self):
        for day,zone in [('2027-03-28','Europe/London'),('2027-10-31','Europe/London'),('2027-06-01','Asia/Kolkata')]:
            start,end=ens._day_bounds(day,zone);base=int(start//3600*3600)-3600
            data=fixture(base,31,[33]*31)
            result,error=ens.model_distribution(data,day,zone,'max','33°C',31,start-5000)
            self.assertIsNone(error);self.assertEqual(result['yes_count'],31)
    def test_reject_missing_units_and_invalid_members(self):
        self.ec['hourly_units']['temperature_2m']='°F'
        self.assertIsNone(ens.model_distribution(self.ec,self.day,'UTC','max','33°C',51,self.now)[0])
        self.ec['hourly_units']['temperature_2m']='°C';self.ec['hourly']['temperature_2m_member00']=[33]*72
        self.assertIsNone(ens.model_distribution(self.ec,self.day,'UTC','max','33°C',51,self.now)[0])
    def test_observer_records_post_entry_only_and_never_changes_ledger(self):
        book={'asks':[{'price':'0.9','size':'12'}],'bids':[{'price':'0.89','size':'20'}],'fetched_at':self.now}
        item={'token':'123','condition_id':'0x'+'a'*64,'event_slug':'highest-temperature-in-london-on-'+datetime.fromisoformat(self.day).strftime('%B').lower()+'-'+str(datetime.fromisoformat(self.day).day)+'-'+str(datetime.fromisoformat(self.day).year),'title':'London','label':'33°C','station':'EGLL','date':self.day,'extreme':'max','end':self.now+86400}
        scalp.set_enabled(True);scalp.process_books([{**item,'forecast_info':{'mode':'off','station':'EGLL','day':self.day}}],{'123':book},self.now)
        before=scalp.status();self.assertEqual(len(before['positions']),1)
        with db.get_db() as c:
            c.execute('INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload) VALUES(?,?,?,?,?)',('EGLL','51','0',self.now+1000,json.dumps({'open':{'timezone':'UTC'}})))
        def fetch(lat,lon,model):return (self.ec if model=='ecmwf_ifs025' else self.gf),None,False
        with patch.object(ens,'fetch_model',side_effect=fetch) as mocked,patch.object(ens.orderbook,'fetch_batch',return_value={'123':book}):
            ens.run_once()
        self.assertEqual(mocked.call_count,2)
        after=scalp.status();self.assertEqual(after['config']['balance'],before['config']['balance']);self.assertEqual(after['positions'],before['positions']);self.assertEqual(after['journal'],before['journal'])
        record=after['ensemble_observation']['results'][0];self.assertEqual(record['position_id'],before['positions'][0]['id']);self.assertEqual(record['models'][0]['yes_count'],10)
        self.assertAlmostEqual(record['quote']['fee_per_share'],.0045);self.assertAlmostEqual(record['models'][0]['net_hold_edge'],41/51-.9-.0045)
        snapshot=after['ensemble_observation']['snapshots'][str(record['position_id'])];self.assertGreaterEqual(snapshot['observed_at'],before['positions'][0]['opened'])
        with patch.object(ens,'fetch_model',side_effect=AssertionError('must cache')),patch.object(ens.orderbook,'fetch_batch',return_value={'123':book}):ens.run_once()
        self.assertEqual(scalp.status()['ensemble_observation']['snapshots'][str(record['position_id'])],snapshot)
    def test_recent_precomputed_candidate_snapshot_is_saved_at_entry(self):
        item={'token':'123','condition_id':'0x'+'a'*64,'event_slug':'highest-temperature-in-london-on-'+datetime.fromisoformat(self.day).strftime('%B').lower()+'-'+str(datetime.fromisoformat(self.day).day)+'-'+str(datetime.fromisoformat(self.day).year),'title':'London','label':'33°C','station':'EGLL','date':self.day,'extreme':'max','end':self.now+86400}
        scalp.set_enabled(True)
        with db.get_db() as c:c.execute('INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload) VALUES(?,?,?,?,?)',('EGLL','51','0',self.now+1000,json.dumps({'open':{'timezone':'UTC'}})))
        quote={'asks':[{'price':'.90','size':'50'}],'bids':[{'price':'.89','size':'50'}],'fetched_at':time.time()}
        ens.offer_candidates([item],{'123':quote})
        with patch.object(ens,'fetch_model',side_effect=lambda lat,lon,model:(self.ec if model=='ecmwf_ifs025' else self.gf,None,False)):
            ens.run_once()
        quote['fetched_at']=time.time()
        scalp.process_books([{**item,'forecast_info':{'mode':'off','station':'EGLL','day':self.day}}],{'123':quote},quote['fetched_at'])
        with patch.object(ens.orderbook,'fetch_batch',return_value={'123':quote}):ens.run_once()
        state=scalp.status();position=state['positions'][0];snapshot=state['ensemble_observation']['snapshots'][str(position['id'])]
        self.assertEqual(snapshot['snapshot_kind'],'at_entry');self.assertEqual(snapshot['captured_at'],position['opened'])
        self.assertLessEqual(snapshot['observed_at'],position['opened']);self.assertEqual(snapshot['quote']['ask'],.9)
        self.assertEqual(snapshot['models'][0]['yes_count'],10);self.assertEqual(snapshot['models'][1]['yes_count'],5)
        # The audit snapshot never changes the actual decision or budget.
        self.assertEqual(position['entry_price'],'0.90')
    def test_snapshot_failure_never_prevents_demo_entry(self):
        now=time.time();item={'token':'999','condition_id':'0x'+'a'*64,'event_slug':'event','title':'x','label':'33°C','end':now+1000}
        book={'asks':[{'price':'.90','size':'50'}],'bids':[{'price':'.89','size':'50'}],'fetched_at':now}
        scalp.set_enabled(True)
        scalp.process_books([item],{'999':book},now)
        with patch.object(ens,'capture_entry',side_effect=RuntimeError('offline')),patch.object(ens.orderbook,'fetch_batch',return_value={'999':book}):ens.run_once()
        self.assertEqual(scalp.status()['positions'][0]['token'],'999')
    def overview_event(self):
        dt=datetime.fromisoformat(self.day)
        slug='highest-temperature-in-london-on-'+dt.strftime('%B').lower()+'-'+str(dt.day)+'-'+str(dt.year)
        return {'slug':slug,'title':'London', 'resolutionSource':'https://aviationweather.gov/api/data/metar?ids=EGLL',
                'markets':[{'groupItemTitle':'33°C','outcomes':['Yes','No'],'clobTokenIds':['111','112']},
                           {'groupItemTitle':'30°C','outcomes':['No','Yes'],'clobTokenIds':['212','211']}]}

    def test_overview_uses_worker_shared_cache_for_all_ranges_without_trading(self):
        event=self.overview_event();before=scalp.status()
        with db.get_db() as c:c.execute('INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload) VALUES(?,?,?,?,?)',('EGLL','51','0',self.now+1000,json.dumps({'open':{'timezone':'UTC'}})))
        with patch.object(ens,'fetch_model',side_effect=AssertionError('overview must not fetch')):
            first=ens.overview(event,self.now)
        self.assertEqual(first['station'],'EGLL');self.assertEqual(len(first['outcomes']),2)
        self.assertIn('Ожидается',first['outcomes'][0]['models'][0]['reason'])
        with patch.object(ens,'fetch_model',side_effect=lambda lat,lon,model:(self.ec if model=='ecmwf_ifs025' else self.gf,None,False)) as fetch:
            ens.run_once()
            self.assertEqual(fetch.call_count,2)
            result=ens.overview(event)
            self.assertEqual([m['yes_count'] for m in result['outcomes'][0]['models']],[10,5])
            self.assertEqual([m['yes_count'] for m in result['outcomes'][1]['models']],[41,26])
            ens.run_once();self.assertEqual(fetch.call_count,2)
        after=scalp.status();self.assertEqual(after['positions'],before['positions']);self.assertEqual(after['journal'],before['journal'])
        self.assertEqual(after['ensemble_observation']['results'],[]);self.assertEqual(after['ensemble_observation']['snapshots'],{})

    def test_overview_rejects_ambiguous_station_and_duplicate_tokens(self):
        event=self.overview_event();event['description']='ICAO LFPG'
        result=ens.overview(event,self.now);self.assertIn('неизвестна',result['reason'])
        with db.get_db() as c:self.assertEqual(c.execute('SELECT count(*) FROM web_scalp_ensemble_views').fetchone()[0],0)
        del event['description'];event['markets'].append(dict(event['markets'][0]))
        self.assertEqual([o['token'] for o in ens.overview(event,self.now)['outcomes']],['212'])

    def test_view_lease_expiry_and_started_day_requires_metar(self):
        event=self.overview_event()
        ens.overview(event,self.now)
        with patch.object(ens,'fetch_model',side_effect=AssertionError('expired')):
            ens.run_once(self.now+ens.VIEW_LEASE+1)
        with db.get_db() as c:
            c.execute('INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload) VALUES(?,?,?,?,?)',('EGLL','51','0',self.start+5000,json.dumps({'open':{'timezone':'UTC'}})))
        ens.overview(event,self.start+1)
        with patch.object(ens,'fetch_model',return_value=(None,'offline',False)),patch.object(ens,'_refresh_metar'):
            ens.run_once(self.start+1)
        result=ens.overview(event,self.start+1)
        self.assertTrue(all('METAR' in m['reason'] and 'yes' not in m for r in result['outcomes'] for m in r['models']))

    def test_overview_endpoint_auth_csrf_and_server_side_event(self):
        from web import app as web
        from werkzeug.security import generate_password_hash
        old=dict(web.app.config)
        db.init_db();web.init_web_db()
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password long enough'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=False,TESTING=True)
            client=web.app.test_client();event=self.overview_event();url='/api/market/'+event['slug']+'/ensemble'
            self.assertEqual(client.post(url,json={}).status_code,401)
            with client.session_transaction() as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
            self.assertEqual(client.post(url,json={}).status_code,403)
            with patch.object(web,'event_for_station',return_value=event),patch.object(ens,'fetch_model',side_effect=AssertionError('no network')):
                response=client.post(url,json={'station':'LFPG'},headers={'X-CSRF-Token':'token'})
                self.assertEqual(response.status_code,200);self.assertEqual(response.get_json()['station'],'EGLL')
            with patch.object(web,'event_for_station',return_value={**event,'slug':'different'}):
                self.assertEqual(client.post(url,json={},headers={'X-CSRF-Token':'token'}).status_code,502)
        finally:web.app.config.clear();web.app.config.update(old)

    def test_migrate_old_cache_schema(self):
        with db.get_db() as c:
            c.execute('DROP TABLE web_scalp_ensemble_cache')
            c.execute('CREATE TABLE web_scalp_ensemble_cache(station TEXT, model TEXT, payload TEXT, fetched REAL, next_attempt REAL NOT NULL DEFAULT 0, error TEXT, PRIMARY KEY(station,model))')
        ens.init_db();ens.init_db()
        with db.get_db() as c:cols={r['name'] for r in c.execute('PRAGMA table_info(web_scalp_ensemble_cache)')}
        self.assertTrue({'lat','lon'}<=cols)

    def test_cleanup_even_when_no_positions_and_no_candidates(self):
        with db.get_db() as c:
            c.execute('INSERT INTO web_scalp_ensemble_results VALUES(?,?,?)',('old','{}',self.now-ens.RETAIN-1))
            c.execute('INSERT INTO web_scalp_ensemble_snapshots VALUES(?,?,?)',(99,'{}',self.now-ens.RETAIN-1))
        ens._last_cleanup=0;ens.run_once()
        with db.get_db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM web_scalp_ensemble_results').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM web_scalp_ensemble_snapshots').fetchone()[0],0)
    def test_wrong_coordinates_or_incomplete_response_not_cached(self):
        wrong=dict(self.ec,latitude=0)
        self.assertFalse(ens._valid_response(wrong,'51','0',51))
        self.assertTrue(ens._valid_response(self.ec,'51','0',51))
        self.assertFalse(ens._valid_response(self.gf,'51','0',51))

    def test_candidate_observation_cannot_fetch_when_demo_stopped(self):
        ens.offer_candidates([{'token':'123','station':'EGLL','date':self.day}],{})
        with patch.object(ens,'fetch_model',side_effect=AssertionError('unexpected')) as fetch:ens.run_once()
        fetch.assert_not_called();self.assertEqual(ens.status()['results'],[])

    def test_missing_station_and_stale_quote_do_not_create_estimate(self):
        item={'token':'123','title':'test','label':'33°C','station':None,'date':self.day,'extreme':'max','book':{'asks':[{'price':'.9','size':'1'}],'fetched_at':self.now-121}}
        out=ens._evaluate(item,None,None,self.now,'.05')
        self.assertIsNone(out['quote']);self.assertEqual(out['models'],[]);self.assertIn('Станция',out['reason'])
    def test_provider_rate_limit_cancels_second_model_and_retries_later(self):
        with patch.object(ens,'_coordinates',return_value=(('51','0','UTC'),None)),patch.object(ens,'fetch_model',return_value=(None,'Сервис ограничил запросы',True)) as fetch:
            ens._refresh_station('EGLL',self.now,[self.day]);ens._refresh_station('EGLL',self.now+1,[self.day])
        self.assertEqual(fetch.call_count,1)
        with db.get_db() as c:self.assertGreater(c.execute('SELECT blocked_until FROM web_scalp_ensemble_control').fetchone()[0],self.now)
    def test_intraday_max_min_use_observations_and_future_only(self):
        now=self.start+12*3600+1800;data=fixture(self.start-3600,31,[30]*31)
        # Forecast extrema from before the current instant must not leak in.
        data['hourly']['temperature_2m'][0]=50
        data['hourly']['temperature_2m_member01'][0]=-50
        data['hourly']['temperature_2m_member02']=[33]*72
        data['hourly']['temperature_2m_member03']=[28]*72
        observed={'temp':31}
        result,error=ens.model_distribution(data,self.day,'UTC','max','31°C',31,now,observed)
        self.assertIsNone(error);self.assertEqual((result['yes_count'],result['no_count']),(30,1))
        result,error=ens.model_distribution(data,self.day,'UTC','min','28°C',31,now,{'temp':29})
        self.assertIsNone(error);self.assertEqual((result['yes_count'],result['no_count']),(1,30))
        result,error=ens.model_distribution(data,self.day,'UTC','max','88°F',31,now,observed)
        self.assertIsNone(error);self.assertEqual(result['yes_count'],30)
        self.assertIsNone(ens.model_distribution(data,self.day,'UTC','max','31°C',31,now,None)[0])
        self.assertIsNone(ens.model_distribution(data,self.day,'UTC','max','31°C',31,self.start+86400,observed)[0])

    def test_intraday_boundary_interpolation_dst_and_half_hour_zone(self):
        for day,zone in [('2027-03-28','Europe/London'),('2027-10-31','Europe/London'),('2027-06-01','Asia/Kolkata')]:
            start,end=ens._day_bounds(day,zone);now=start+12*3600+1500
            base=int(start//3600*3600)-3600;data=fixture(base,31,[20]*31)
            result,error=ens.model_distribution(data,day,zone,'max','20°C',31,now,{'temp':19})
            self.assertIsNone(error,(day,error));self.assertEqual(result['yes_count'],31)
            # Even at the end of the day a bracketing forecast is required.
            result,error=ens.model_distribution(data,day,zone,'max','20°C',31,end-60,{'temp':19})
            self.assertIsNone(error,(day,error));self.assertEqual(result['yes_count'],31)
            truncated=fixture(base,31,[20]*31)
            h=truncated['hourly'];right=next(i for i,t in enumerate(h['time']) if t>=end)
            for key in h:h[key]=h[key][:right]
            result,error=ens.model_distribution(truncated,day,zone,'max','20°C',31,now,{'temp':19})
            self.assertIsNone(result);self.assertIn('остаток',error)

    def test_metar_completeness_freshness_and_times(self):
        start=self.start;now=start+5*3600
        points=[[start+600+i*3600,20+i] for i in range(5)]
        row={'payload':json.dumps(points),'fetched':now-30}
        obs,error=ens._metar_observation(row,start,start+86400,now,'max')
        self.assertIsNone(error);self.assertEqual(obs['temp'],24);self.assertEqual(obs['last_observation'],points[-1][0]);self.assertEqual(obs['count'],5)
        obs,error=ens._metar_observation(row,start,start+86400,now,'min');self.assertEqual(obs['temp'],20)
        for changed in ({'payload':json.dumps(points),'fetched':now-901}, {'payload':json.dumps(points[3:]),'fetched':now},
                        {'payload':json.dumps(points[:1]+points[3:]),'fetched':now}, {'payload':json.dumps(points[:2]),'fetched':now},
                        {'payload':json.dumps(points),'fetched':now+1}):
            self.assertIsNone(ens._metar_observation(changed,start,start+86400,now,'max')[0])

    def test_intraday_worker_overview_snapshot_and_no_trade_effect(self):
        now=self.start+12*3600;event=self.overview_event();prior=scalp.status()
        with db.get_db() as c:c.execute('INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload) VALUES(?,?,?,?,?)',('EGLL','51','0',now+10000,json.dumps({'open':{'timezone':'UTC'}})))
        def metar(code,at):
            points=[[self.start+600+i*3600,31] for i in range(12)]
            with db.get_db() as c:c.execute('INSERT OR REPLACE INTO web_scalp_ensemble_metar VALUES(?,?,?,?,?)',(code,json.dumps(points),now,now+300,None))
        with patch.object(ens.time,'time',return_value=now),patch.object(ens,'fetch_model',side_effect=lambda a,b,m:(self.ec if m=='ecmwf_ifs025' else self.gf,None,False)) as fetch,patch.object(ens,'_refresh_metar',side_effect=metar) as get_metar:
            self.assertIn('METAR',ens.overview(event)['outcomes'][0]['models'][0]['reason'])
            ens.run_once();out=ens.overview(event)
            self.assertEqual(fetch.call_count,2);get_metar.assert_called_once()
            result=out['outcomes'][0];self.assertEqual(result['observation']['temp'],31)
            self.assertEqual([x['yes_count'] for x in result['models']],[10,5])
            self.assertEqual(scalp.status()['positions'],prior['positions']);self.assertEqual(scalp.status()['journal'],prior['journal'])
            # Same data may be captured only when the result was available at entry.
            item={'token':'112','condition_id':'condition','event_slug':event['slug'],'label':'33°C','station':'EGLL','date':self.day,'extreme':'max','opened':now+1,'entry_price':'.9','entry_fee':'.01','original':'5'}
            result.update({k:item[k] for k in ('condition_id','event_slug','label','station','date','extreme')})
            with db.get_db() as c:
                c.execute('INSERT INTO web_scalp_ensemble_results VALUES(?,?,?)',('112',json.dumps(result),now))
                ens.capture_entry(c,1,item)
                saved=json.loads(c.execute('SELECT payload FROM web_scalp_ensemble_snapshots WHERE position_id=1').fetchone()['payload'])
                self.assertEqual(saved['snapshot_kind'],'at_entry');self.assertEqual(saved['observation']['temp'],31)
                result['observation']['fetched_at']=now+20
                c.execute('UPDATE web_scalp_ensemble_results SET payload=? WHERE token=?',(json.dumps(result),'112'))
                ens.capture_entry(c,2,item)
                self.assertIsNone(c.execute('SELECT payload FROM web_scalp_ensemble_snapshots WHERE position_id=2').fetchone())

    def test_metar_refresh_when_models_cached_and_provider_blocked(self):
        now=self.start+12*3600;event=self.overview_event()
        with db.get_db() as c:
            c.execute('INSERT INTO web_scalp_forecast_cache(code,lat,lon,station_expires,payload) VALUES(?,?,?,?,?)',('EGLL','51','0',now+10000,json.dumps({'open':{'timezone':'UTC'}})))
            c.execute('UPDATE web_scalp_ensemble_control SET blocked_until=?',(now+1800,))
            for name,payload in [('ECMWF IFS',self.ec),('GEFS',self.gf)]:
                c.execute('INSERT INTO web_scalp_ensemble_cache(station,model,payload,fetched,lat,lon,next_attempt) VALUES(?,?,?,?,?,?,?)',('EGLL',name,json.dumps(payload),now-60,'51','0',now+3600))
        with patch.object(ens.time,'time',return_value=now),patch.object(ens,'fetch_model',side_effect=AssertionError('models cached')),patch.object(ens,'_refresh_metar') as metar:
            ens.overview(event);ens.run_once();metar.assert_called_once()

    def test_metar_network_failure_blocks_fabricated_data_and_retries(self):
        now=self.now
        with patch.object(ens.utils,'_AWC_STATE',{'blocked_until':0,'strikes':0,'last_error':''}),patch.object(ens.utils.AWC_LIMITER,'allow',return_value=True),patch.object(ens.utils.SESSION,'get',side_effect=RuntimeError('offline')) as get:
            ens._refresh_metar('EGLL',now);ens._refresh_metar('EGLL',now+1);self.assertEqual(get.call_count,1)
        with db.get_db() as c:
            row=dict(c.execute('SELECT * FROM web_scalp_ensemble_metar WHERE station=?',('EGLL',)).fetchone())
            self.assertIsNone(row['payload']);self.assertIsNone(ens._metar_observation(row,now-3600,now+86400,now,'max')[0])

    def test_metar_network_fetch_limits_and_conflicting_corrections(self):
        now=self.now;start=now-3600
        class Response:
            status_code=200
            def __init__(self,data):self.data=data
            def raise_for_status(self):pass
            def iter_content(self,n):yield json.dumps(self.data).encode()
            def close(self):pass
        data=[{'icaoId':'EGLL','obsTime':int(start),'temp':12},{'icaoId':'EGLL','obsTime':int(now-60),'temp':13}]
        with patch.object(ens.utils,'_AWC_STATE',{'blocked_until':0,'strikes':0,'last_error':''}),patch.object(ens.utils.AWC_LIMITER,'allow',return_value=True),patch.object(ens.utils.SESSION,'get',return_value=Response(data)) as get:
            ens._refresh_metar('EGLL',now);ens._refresh_metar('EGLL',now+1)
            self.assertEqual(get.call_count,1)
            self.assertEqual(get.call_args.kwargs['params'],{'ids':'EGLL','format':'json','hours':48})
        with db.get_db() as c:
            row=dict(c.execute('SELECT * FROM web_scalp_ensemble_metar WHERE station=?',('EGLL',)).fetchone())
            self.assertEqual(len(json.loads(row['payload'])),2)
        conflict=data+[{'icaoId':'EGLL','obsTime':int(now-60),'temp':14}]
        with patch.object(ens.utils,'_AWC_STATE',{'blocked_until':0,'strikes':0,'last_error':''}),patch.object(ens.utils.AWC_LIMITER,'allow',return_value=True),patch.object(ens.utils.SESSION,'get',return_value=Response(conflict)):
            ens._refresh_metar('EGLL',now+301)
        with db.get_db() as c:
            row=c.execute('SELECT * FROM web_scalp_ensemble_metar WHERE station=?',('EGLL',)).fetchone()
            self.assertIsNone(row['payload']);self.assertIn('неполны',row['error'])

    def test_started_day_requests_models_and_metar_and_expired_day_does_not(self):
        with patch.object(ens,'_coordinates',return_value=(('51','0','UTC'),None)),patch.object(ens,'fetch_model',return_value=(None,'offline',False)) as fetch,patch.object(ens,'_refresh_metar') as metar:
            ens._refresh_station('EGLL',self.start+1,[self.day]);self.assertEqual(fetch.call_count,2);metar.assert_called_once()
            fetch.reset_mock();metar.reset_mock();ens._refresh_station('EGLL',self.start+86401,[self.day]);fetch.assert_not_called();metar.assert_not_called()

if __name__=='__main__':unittest.main()
