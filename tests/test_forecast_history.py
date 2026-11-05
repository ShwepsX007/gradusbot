import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from web import app as web, forecast_history as history


def opened(points):
    return {'hourly':{'epoch':[p[0] for p in points],'temperature_2m':[p[1] for p in points]}}

def metno(points):return {'points':[[t,v] for t,v in points]}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db();web._cache.clear()
        self.sid=web.db.add_station('London','','','EGLL','metar')
        self.obs=int(time.time())-3600
    def tearDown(self):
        web.db.DB_FILE=self.old;self.tmp.cleanup()
    def test_latest_and_three_hour_snapshots_and_correct_sign(self):
        t=self.obs;old=opened([(t-1800,12),(t+1800,16)]);old_met=metno([(t-1800,10),(t+1800,14)])
        self.assertTrue(history.capture(self.sid,old,old_met,now=t-4*3600))
        new=opened([(t-1800,18),(t+1800,20)]);new_met=metno([(t-1800,22),(t+1800,24)])
        self.assertTrue(history.capture(self.sid,new,new_met,now=t-3000))
        result=history.compare(self.sid,{'obs_time':t,'temp':15},now=t+10)
        self.assertEqual(result['latest']['forecast_temp'],21)
        self.assertEqual(result['latest']['difference'],-6)
        self.assertEqual(result['three_hours']['forecast_temp'],13)
        self.assertEqual(result['three_hours']['difference'],2)
        self.assertTrue(result['latest']['interpolated'])
        self.assertEqual(set(result['latest']['sources']),{'Open-Meteo','MET Norway'})
        self.assertGreaterEqual(result['three_hours']['lead_seconds'],10800)
    def test_does_not_use_forecasts_saved_after_or_at_observation(self):
        t=self.obs;data=opened([(t,12),(t+3600,13)])
        history.capture(self.sid,data,None,now=t)
        result=history.compare(self.sid,{'obs_time':t,'temp':15},now=t+5)
        self.assertIsNone(result['latest']);self.assertIsNone(result['three_hours'])
    def test_no_interpolation_using_values_in_the_past_at_capture(self):
        t=self.obs
        # MET Norway's earlier point predates capture, so cannot contribute to 10:30.
        om=opened([(t+600,12),(t+4200,14)]);met=metno([(t-1200,20),(t+2400,22)])
        history.capture(self.sid,om,met,now=t)
        result=history.compare(self.sid,{'obs_time':t+1200,'temp':17},now=t+1500)
        self.assertAlmostEqual(result['latest']['forecast_temp'],12+2/6)
        self.assertEqual(list(result['latest']['sources']),['Open-Meteo'])
    def test_interpolation_rejects_gaps_and_mixed_sources(self):
        t=self.obs
        history.capture(self.sid,opened([(t-3600,10),(t+3600,20)]),None,now=t-4*3600)
        r=history.compare(self.sid,{'obs_time':t,'temp':16},now=t+60)
        self.assertIsNone(r['latest'])
        self.assertIsNone(history._estimate([{'time':t-1800,'sources':{'Open-Meteo':10}},{'time':t+1800,'sources':{'MET Norway':10}}],t-4000,t))
    def test_five_minute_throttle_and_concurrent_snapshot(self):
        t=self.obs;data=opened([(t+3600,12),(t+7200,13)]);out=[];errors=[]
        def run():
            try:out.append(history.capture(self.sid,data,None,now=t))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=run) for _ in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(errors,[]);self.assertEqual(sum(out),1)
        self.assertFalse(history.capture(self.sid,data,None,now=t+299))
        self.assertTrue(history.capture(self.sid,data,None,now=t+300))
    def test_snapshot_deletion_and_active_view_only(self):
        t=int(time.time());data=opened([(t+3600,10),(t+7200,11)])
        web.db.update_station(self.sid,enabled=1)
        with web.app.test_request_context('/'),patch.object(web.metar_archive,'refresh'),patch.object(web.metar_archive,'get',return_value={'reports':[]}),patch.object(web,'station_info',return_value={'lat':51.4,'lon':.1}),patch.object(web,'forecast',return_value=data),patch.object(web.forecast_ensemble,'get_metno',return_value={'status':'error','data':None}),patch.object(web.utils,'fetch_metar',return_value=None):
            web.weather(self.sid)
            with web.db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_forecast_snapshots').fetchone()[0],0)
            web.weather(self.sid,active_view=True)
            with web.db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_forecast_snapshots').fetchone()[0],1)
        with web.app.test_request_context('/'),patch.object(web,'_has_position',return_value=False):web.remove_station(self.sid)
        with web.db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_forecast_snapshots').fetchone()[0],0)
    def test_snapshot_storage_failure_does_not_break_weather(self):
        t=int(time.time());data=opened([(t+3600,10),(t+7200,11)])
        with web.app.test_request_context('/'),patch.object(web.metar_archive,'refresh'),patch.object(web.metar_archive,'get',return_value={'reports':[]}),patch.object(web,'station_info',return_value={'lat':51.4,'lon':.1}),patch.object(web,'forecast',return_value=data),patch.object(web.forecast_ensemble,'get_metno',return_value={'status':'error','data':None}),patch.object(web.utils,'fetch_metar',return_value={'temp':10,'obs_time':t-60}),patch.object(history,'capture',side_effect=RuntimeError('locked')):
            result=web.weather(self.sid,active_view=True).get_json()
        self.assertEqual(result['metar']['temp'],10);self.assertEqual(result['forecast_history_status'],'error')
        self.assertTrue(result['forecast_ensemble']['points'])
    def test_common_sources_only_are_averaged_at_observation(self):
        t=self.obs
        points=[{'time':t-1800,'sources':{'Open-Meteo':10,'MET Norway':20}},{'time':t+1800,'sources':{'Open-Meteo':12}}]
        estimate=history._estimate(points,t-3600,t)
        self.assertEqual(estimate,({'Open-Meteo':11},True))
    def test_future_or_missing_metar_is_not_compared(self):
        t=self.obs
        self.assertIsNone(history.compare(self.sid,{'obs_time':t+3600,'temp':10},now=t))
        self.assertIsNone(history.compare(self.sid,{'temp':10},now=t))
        self.assertIsNone(history.compare(self.sid,{'obs_time':t,'temp':float('nan')},now=t+60))
    def test_history_is_bounded_and_empty_not_saved(self):
        t=int(time.time());data=opened([(t+3600,10),(t+7200,11)])
        history.capture(self.sid,data,None,now=t)
        with web.db.get_db() as c:c.execute('UPDATE web_forecast_snapshots SET captured_at=?',(t-history.RETENTION-1,))
        history.init_db()
        with web.db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_forecast_snapshots').fetchone()[0],0)
        self.assertFalse(history.capture(self.sid,opened([(t-3600,10)]),None,now=t))
