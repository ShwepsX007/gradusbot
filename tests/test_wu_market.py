import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from web import wu_market as wm


class WUMarketTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,6,1,tzinfo=timezone.utc).timestamp()
        self.now=self.start+12*3600+600
        self.points=[{'time':self.start+h*3600,'temp':20 if h==15 else 10} for h in range(24)]
        self.snapshot={'status':'ok','fetched_at':self.now-60,'points':self.points}
        self.obs={'temp':22,'extreme':'max','fetched_at':self.now-60,'last_observation':self.now-300}
        self.row={'station':'EGLL','date':'2026-06-01','timezone':'UTC','label':'22°C','extreme':'max','observation':self.obs,'observed_at':self.now}
        self.view={'slug':'highest-temperature-in-london','station':'EGLL','date':'2026-06-01','timezone':'UTC','outcomes':[self.row]}

    def test_started_day_uses_metar_and_only_remaining_forecast(self):
        self.points[3]['temp']=40  # Past WU forecast must not change the result.
        result=wm._forecast_extreme(self.snapshot,'2026-06-01','UTC','max',self.now,self.obs)
        self.assertEqual(result['temp'],22);self.assertTrue(result['with_metar'])
        with patch.object(wm.wu_forecast,'peek',return_value=self.snapshot) as peek:
            wm.attach(self.view,self.now)
        peek.assert_called_once_with('EGLL','UTC',self.now)
        self.assertTrue(self.row['wu']['match']);self.assertEqual(self.row['wu']['temp'],22)
        self.row['label']='21°C';self.assertFalse(wm._matches(22,self.row['label']))
        self.assertIsNone(wm._matches(21.5,'21°C'))
        self.assertIsNone(wm._matches(22,'unknown'))
        self.assertTrue(wm._matches((72-32)*5/9,'72°F'))

    def test_missing_stale_or_incomplete_data_is_not_marked(self):
        for obs in [None, {**self.obs,'fetched_at':self.now-10000}, {**self.obs,'last_observation':self.now-10000}, {**self.obs,'extreme':'min'}]:
            self.assertIsNone(wm._forecast_extreme(self.snapshot,'2026-06-01','UTC','max',self.now,obs))
        self.assertIsNone(wm._forecast_extreme({**self.snapshot,'points':self.points[:-1]},'2026-06-01','UTC','max',self.now,self.obs))
        self.assertIsNone(wm._forecast_extreme({**self.snapshot,'points':self.points[:18]+self.points[19:]},'2026-06-01','UTC','max',self.now,self.obs))
        self.assertIsNone(wm._forecast_extreme({**self.snapshot,'fetched_at':self.now-1800},'2026-06-01','UTC','max',self.now,self.obs))
        self.row['station']='LFPG'
        with patch.object(wm.wu_forecast,'peek',return_value=self.snapshot):wm.attach(self.view,self.now)
        self.assertNotIn('wu',self.row)

    def test_future_day_must_be_complete_and_dst_is_distinct(self):
        now=self.start-600
        snapshot={**self.snapshot,'fetched_at':now-10}
        result=wm._forecast_extreme(snapshot,'2026-06-01','UTC','max',now,None)
        self.assertEqual(result['temp'],20);self.assertFalse(result['with_metar'])
        snapshot['points']=self.points[1:]
        self.assertIsNone(wm._forecast_extreme(snapshot,'2026-06-01','UTC','max',now,None))
        start=datetime(2026,11,1,4,tzinfo=timezone.utc).timestamp()
        snapshot={'status':'ok','fetched_at':start-100,'points':[{'time':start+h*3600,'temp':h} for h in range(25)]}
        self.assertEqual(wm._forecast_extreme(snapshot,'2026-11-01','America/New_York','max',start-60,None)['temp'],24)
        snapshot['points'].pop(2)
        self.assertIsNone(wm._forecast_extreme(snapshot,'2026-11-01','America/New_York','max',start-60,None))

    def test_market_reads_only_cached_snapshot(self):
        with patch.object(wm.wu_forecast,'peek',return_value=None),patch.object(wm.wu_forecast,'get',side_effect=AssertionError('network must not be scheduled')):
            wm.attach(self.view,self.now)
        self.assertNotIn('wu',self.row)

if __name__=='__main__':unittest.main()
