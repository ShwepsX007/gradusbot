import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
import database as db
from web import auto_scalp as s, scalp_forecast as f, opportunity_scan


def payload(day='2026-06-01',zone='Europe/Helsinki',met=True):
    d=date.fromisoformat(day);tz=ZoneInfo(zone)
    start=int(datetime.combine(d,datetime.min.time(),tzinfo=tz).timestamp())
    end=int(datetime.combine(d+timedelta(days=1),datetime.min.time(),tzinfo=tz).timestamp())
    stamps=list(range(start,end,3600));temps=[float(i) for i in range(len(stamps))]
    return {'code':'EGLL','fetched_at':1000,'open':{'timezone':zone,'hourly':{'epoch':stamps,'temperature_2m':temps}},
            'met':{'points':[[t,v+2] for t,v in zip(stamps,temps)]} if met else None,
            'met_status':'ok' if met else 'error','met_fetched_at':1000 if met else None}


def item(label,code='EGLL',kind='max',day='2026-06-01'):
    unit='F' if 'F' in label.upper() else 'C'
    return {'label':label,'unit':unit,'temperature_range':opportunity_scan.temperature_range(label),
            'station':code,'date':day,'extreme':kind,'token':label}

class ForecastWeatherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_FILE;db.DB_FILE=os.path.join(self.tmp.name,'db');db.init_db();s.init_db()
    def tearDown(self):db.DB_FILE=self.old;self.tmp.cleanup()
    def test_local_daily_ensemble_max_min_and_dst(self):
        for day,n in [('2026-06-01',24),('2026-03-29',23),('2026-10-25',25)]:
            with self.subTest(day=day):
                p=payload(day);x=f.extreme(p,day,'max');y=f.extreme(p,day,'min')
                self.assertEqual(x['hours'],n);self.assertEqual(x['value_c'],n);self.assertEqual(y['value_c'],1)
                self.assertEqual(x['sources'],['MET Norway','Open-Meteo']);self.assertEqual(x['timezone'],'Europe/Helsinki')
    def test_one_model_accepted_and_partial_day_not_faked(self):
        p=payload(met=False);x=f.extreme(p,'2026-06-01','max')
        self.assertEqual(x['value_c'],23);self.assertEqual(x['sources'],['Open-Meteo'])
        p['open']['hourly']['epoch'].pop(5);p['open']['hourly']['temperature_2m'].pop(5)
        self.assertIsNone(f.extreme(p,'2026-06-01','max'))
        self.assertIsNone(f.extreme(p,'2026-06-02','max'))
    def test_met_only_half_hour_zone_and_incomplete_grid(self):
        p=payload(zone='Asia/Kolkata');p['open']['hourly']={'epoch':[],'temperature_2m':[]}
        # MET Norway provides UTC-aligned timestamps, local :30.
        p['met']['points']=[[stamp+1800,temp] for stamp,temp in p['met']['points']]
        result=f.extreme(p,'2026-06-01','max')
        self.assertEqual(result['hours'],24);self.assertEqual(result['sources'],['MET Norway'])
        p['met']['points'].pop(2);self.assertIsNone(f.extreme(p,'2026-06-01','max'))

    def test_real_provider_cache_and_budget_failures(self):
        p=payload();opened=p['open'];met={'status':'ok','data':p['met'],'fetched_at':1000}
        with patch.object(f,'_station',return_value=('51.4700','-0.4500')) as station,patch.object(f,'_open_meteo',return_value=opened) as op,patch.object(f.forecast_ensemble,'get_metno',return_value=met):
            self.assertEqual(f.get('EGLL',permit_fetch=False,now=1000)[1],'Лимит запросов прогноза за проход')
            value,error=f.get('EGLL',now=1000);self.assertIsNone(error);self.assertEqual(value['code'],'EGLL')
            self.assertIsNotNone(f.get('EGLL',permit_fetch=False,now=1100)[0]);self.assertEqual(station.call_count,1);self.assertEqual(op.call_count,1)
            self.assertIsNone(f.get('EGLL',permit_fetch=False,now=1901)[0])
        with patch.object(f,'_open_meteo',side_effect=RuntimeError()),patch.object(f.forecast_ensemble,'get_metno',return_value=met):
            value,error=f.get('EGLL',now=1901);self.assertIsNone(error)
            self.assertEqual(f.extreme(value,'2026-06-01','max')['sources'],['MET Norway'])
    def test_failed_forecast_does_not_reuse_stale_or_burst(self):
        with patch.object(f,'_station',side_effect=RuntimeError()) as station:
            self.assertIsNone(f.get('EGLL',now=1000)[0]);self.assertIsNone(f.get('EGLL',now=1001)[0]);self.assertEqual(station.call_count,1)
    def test_forecast_gap_celsius_and_fahrenheit_and_tail_ranges(self):
        forecast={'value_c':20,'time':1000,'timezone':'Europe/London','day':'2026-06-01','kind':'max','sources':['Open-Meteo'],'hours':24,'station':'EGLL','fetched_at':1000}
        settings={**s.DEFAULT,'forecast_mode':'forecast','forecast_gap':'3','forecast_gap_units':'C'}
        items=[item('30°C'),item('86°F'),item('72°F'),item('62°F or below'),item('70°F or above'),item('75-77°F')]
        with patch.object(f,'get',return_value=({'fake':1},None)),patch.object(f,'extreme',return_value=forecast):
            accepted,stats=s.prepare_forecasts(items,settings,1000)
        self.assertEqual({x['label'] for x in accepted},{'30°C','86°F','62°F or below','75-77°F'})
        self.assertEqual(stats,{'forecast':4,'fallback':0,'excluded':2})
        values={x['label']:x['forecast_info']['distance'] for x in accepted}
        self.assertAlmostEqual(values['30°C'],9.5);self.assertAlmostEqual(values['86°F'],10-5/18)
        settings['forecast_gap_units']='F'
        with patch.object(f,'get',return_value=({'fake':1},None)),patch.object(f,'extreme',return_value=forecast):
            accepted,_=s.prepare_forecasts([item('72°F'),item('69°F')],settings,1000)
        self.assertEqual([x['label'] for x in accepted],['72°F']);self.assertAlmostEqual(accepted[0]['forecast_info']['distance'],3.5)
    def test_minimum_uses_min_and_missing_data_explicitly_falls_back(self):
        p=payload();items=[item('0°C',kind='min'),item('30°C',code=None)]
        settings={**s.DEFAULT,'forecast_mode':'forecast','forecast_gap':'3'}
        with patch.object(f,'get',return_value=(p,None)):
            accepted,stats=s.prepare_forecasts(items,settings,1000)
        self.assertEqual(stats['excluded'],1);self.assertEqual(stats['fallback'],1)
        self.assertEqual(accepted[0]['forecast_info']['mode'],'fallback');self.assertIn('Станция',accepted[0]['forecast_info']['reason'])
    def test_budget_bounded_and_forecast_prioritized_before_fallback(self):
        seen=[]
        def get(code,permit_fetch,now):
            seen.append((code,permit_fetch))
            return (None,'Лимит запросов прогноза за проход') if not permit_fetch else ({'code':code},None)
        forecast={'value_c':20,'time':1,'timezone':'UTC','day':'2026-06-01','kind':'max','sources':['Open-Meteo'],'hours':24,'station':'EGLL','fetched_at':1000}
        items=[item('30°C',code='KAAA'),item('31°C',code='KBBB'),item('32°C',code='KCCC')]
        with patch.object(f,'get',side_effect=get),patch.object(f,'extreme',return_value=forecast):
            accepted,stats=s.prepare_forecasts(items,{**s.DEFAULT,'forecast_mode':'forecast'},1000)
        self.assertEqual(sum(permit for _,permit in seen),2);self.assertEqual(stats['fallback'],1)
        self.assertEqual(accepted[-1]['forecast_info']['mode'],'fallback');self.assertEqual(accepted[0]['label'],'31°C')
    def test_forecast_rules_do_not_create_probability(self):
        for invalid in ({'forecast_mode':'live'},{'forecast_gap':'NaN'},{'forecast_gap':'0'},{'forecast_gap_units':'K'}):
            with self.assertRaises(s.ScalpError):s.validate_settings({**s.DEFAULT,**invalid})

if __name__=='__main__':unittest.main()
