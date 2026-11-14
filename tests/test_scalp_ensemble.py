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
        self.assertIsNone(result);self.assertIn('сутки уже начались',error)
        self.gf['hourly']['time'][10]+=1
        result,error=ens.model_distribution(self.gf,self.day,'UTC','max','33°C',31,self.now)
        self.assertIsNone(result);self.assertIn('сутки',error)
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
    def test_started_day_never_requests_ensemble(self):
        with patch.object(ens,'_coordinates',return_value=(('51','0','UTC'),None)),patch.object(ens,'fetch_model') as fetch:
            ens._refresh_station('EGLL',self.start+1,[self.day])
        fetch.assert_not_called()

if __name__=='__main__':unittest.main()
