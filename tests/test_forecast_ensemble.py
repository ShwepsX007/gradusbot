import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from email.utils import formatdate
from unittest.mock import Mock, patch

from web import app as web, forecast_ensemble as fe


def response(data=None,status=200,headers=None):
    r=Mock();r.status_code=status;r.headers=headers or {}
    r.iter_content.return_value=[json.dumps(data).encode()]
    if status>=400:r.raise_for_status.side_effect=RuntimeError('not public')
    return r


def metno_payload(now,temp=12):
    stamp=datetime.fromtimestamp(int(now//3600*3600),timezone.utc).isoformat().replace('+00:00','Z')
    return {'properties':{'meta':{'updated_at':stamp},'timeseries':[{'time':stamp,'data':{'instant':{'details':{'air_temperature':temp}}}}]}}


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE;self.config=dict(web.app.config)
        web.db.DB_FILE=os.path.join(self.tmp.name,'test.db');web.db.init_db();web.init_web_db();web._cache.clear()
    def tearDown(self):
        web.db.DB_FILE=self.old;web.app.config.clear();web.app.config.update(self.config);self.tmp.cleanup()
    def test_openmeteo_normalizes_true_instants_and_repeated_dst_hour(self):
        first=int(datetime(2026,11,1,5,30,tzinfo=timezone.utc).timestamp());second=first+3600
        raw={'timezone':'America/New_York','hourly':{'time':[first,second],'temperature_2m':[10,20]},
             'daily':{'time':[first-5400],'sunrise':[first+18000],'sunset':[None]}}
        f=fe.normalize_open_meteo(raw)
        self.assertEqual(f['hourly']['time'],['2026-11-01T01:30:00','2026-11-01T01:30:00'])
        self.assertEqual(f['hourly']['epoch'],[first,second]);self.assertEqual(f['daily']['time'],['2026-11-01'])
        self.assertIsNone(f['daily']['sunset'][0]);self.assertEqual(raw['hourly']['time'],[first,second])
        midnight=int(datetime(2026,3,7,4,tzinfo=timezone.utc).timestamp())
        spring={'timezone':'America/New_York','utc_offset_seconds':-14400,'daily':{'time':[midnight]}}
        self.assertEqual(fe.normalize_open_meteo(spring)['daily']['time'],['2026-03-07'])
    def test_average_exact_times_interpolation_and_single_source(self):
        om={'hourly':{'epoch':[10000,11800,13600,17200], 'temperature_2m':[10,10,20,30]}}
        met={'points':[[10000,14],[13600,18],[20800,50]]}
        points=fe.ensemble(om,met,'Asia/Kolkata')['points'];bytime={p['time']:p for p in points}
        self.assertEqual(bytime[10000]['temp'],12);self.assertEqual(bytime[11800]['temp'],13)
        self.assertTrue(bytime[11800]['interpolated']);self.assertEqual(bytime[13600]['temp'],19)
        self.assertEqual(bytime[17200]['temp'],30);self.assertEqual(bytime[17200]['sources'],{'Open-Meteo':30})
        self.assertEqual(bytime[20800]['temp'],50);self.assertEqual(fe.ensemble(None,met,None)['points'][0]['temp'],14)
        self.assertEqual(fe.ensemble({'hourly':{'epoch':[1],'temperature_2m':[float('nan')]}},None,'UTC')['points'],[])
    def test_metno_cache_expiration_conditional_and_headers(self):
        now=time.time();last='Mon, 02 Nov 2026 12:00:00 GMT';expires=formatdate(now+3600,usegmt=True)
        first=response(metno_payload(now),headers={'Expires':expires,'Last-Modified':last})
        with patch.object(fe.requests,'get',return_value=first) as request:
            a=fe.get_metno(51.123456,-0.123456);b=fe.get_metno(51.123459,-0.123459)
            self.assertEqual(a['status'],'ok');self.assertEqual(b['status'],'ok');self.assertEqual(request.call_count,1)
            self.assertEqual(request.call_args.kwargs['params'],{'lat':'51.1235','lon':'-0.1235'})
            self.assertIn('github.com/ShwepsX007/gradusbot',request.call_args.kwargs['headers']['User-Agent'])
        with web.db.get_db() as c:
            row=c.execute('SELECT * FROM web_metno_cache').fetchone();self.assertGreater(row['expires'],now+3500)
            c.execute('UPDATE web_metno_cache SET expires=?,next_attempt=?',(now-1,now-1))
        with patch.object(fe.requests,'get',return_value=response(status=304,headers={'Expires':expires})) as request:
            a=fe.get_metno(51.123456,-0.123456)
            self.assertEqual(a['data']['points'][0][1],12);self.assertEqual(request.call_args.kwargs['headers']['If-Modified-Since'],last)
    def test_throttle_backoff_is_shared_across_locations_and_no_stale_forecast(self):
        with patch.object(fe.requests,'get',return_value=response(status=429,headers={'Retry-After':'1200'})) as request:
            a=fe.get_metno(1,2);b=fe.get_metno(3,4);self.assertEqual(request.call_count,1)
        self.assertEqual(a['status'],'throttled');self.assertIsNone(a['data']);self.assertEqual(b['status'],'throttled')
        with web.db.get_db() as c:self.assertGreater(c.execute('SELECT blocked_until FROM web_metno_control').fetchone()[0],time.time()+1100)
    def test_invalid_large_and_failed_response(self):
        with patch.object(fe.requests,'get') as request:
            self.assertEqual(fe.get_metno(float('nan'),2)['status'],'invalid_coordinates');request.assert_not_called()
        r=response();r.iter_content.return_value=[b'x'*(fe.MAX_BYTES+1)]
        with patch.object(fe.requests,'get',return_value=r) as request:
            self.assertEqual(fe.get_metno(1,2)['status'],'error');self.assertEqual(fe.get_metno(1,2)['status'],'error');self.assertEqual(request.call_count,1)
            r.close.assert_called_once()
    def test_shared_lease_and_expired_data_are_not_falsely_fresh(self):
        now=time.time()
        with web.db.get_db() as c:
            c.execute("INSERT INTO web_metno_cache(lat,lon,payload,expires,next_attempt,status) VALUES(?,?,?,?,?,'loading')",
                      ('1.0000','2.0000',json.dumps({'points':[[int(now),9]]}),now-5,now+30))
        with patch.object(fe.requests,'get') as request:
            result=fe.get_metno(1,2);request.assert_not_called()
        self.assertEqual(result['status'],'loading');self.assertIsNone(result['data'])
    def test_malformed_timezones_are_not_accepted(self):
        for name in (None,'','Not/AZone','../Europe/London'):
            self.assertFalse(fe.valid_timezone(name))
        self.assertTrue(fe.valid_timezone('Europe/London'))
    def test_weather_falls_back_to_archive_coordinates_and_handles_single_source(self):
        now=int(time.time());sid=web.db.add_station('x','','','EGLL','metar');web.db.update_station(sid,enabled=1)
        with web.db.get_db() as c:
            c.execute('INSERT INTO web_metar_archive_station VALUES(?,?,?,?)',('EGLL',51.47,-0.45,now-10))
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH='hash')
        client=web.app.test_client()
        with client.session_transaction() as s:s['auth_user']='test';s['auth_version']=web.auth_version();s['csrf']='csrf'
        sample={'timezone':'Europe/London','hourly':{'time':['2026-11-02T12:00'],'epoch':[now-3600],'temperature_2m':[10]}}
        met={'status':'ok','data':{'points':[[now-3600,14]],'updated_at':'2026-11-02T10:00:00Z'},'fetched_at':now}
        with patch.object(web.metar_archive,'refresh'),patch.object(web,'station_info',return_value=None),patch.object(web.utils,'fetch_metar',return_value=None),patch.object(web,'forecast',return_value=sample),patch.object(fe,'get_metno',return_value=met):
            data=client.get(f'/api/stations/{sid}/weather').get_json()
        self.assertEqual(data['forecast_status']['coordinate_source'],'metar_archive')
        self.assertEqual(data['forecast_status']['met_norway'],'ok');self.assertEqual(data['forecast_ensemble']['points'][0]['temp'],12)
        # No local zone means an honest indication rather than treating UTC as station day.
        with patch.object(web.metar_archive,'refresh'),patch.object(web,'station_info',return_value=None),patch.object(web.utils,'fetch_metar',return_value=None),patch.object(web,'forecast',return_value=None),patch.object(fe,'get_metno',return_value=met):
            data=client.get(f'/api/stations/{sid}/weather').get_json()
        self.assertEqual(data['forecast_status']['reason'],'no_timezone');self.assertIsNone(data['forecast_ensemble']['timezone'])

if __name__=='__main__':unittest.main()
