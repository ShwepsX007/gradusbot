import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from web import app as web, metar_archive as archive


def report(code, timestamp, temp=10, **extra):
    return {'icaoId':code,'obsTime':timestamp,'temp':temp,'rawOb':f'METAR {code} TEST',**extra}


def response(data=None,status=200):
    r=Mock();r.status_code=status;r.iter_content.return_value=[json.dumps(data).encode()]
    if status>=400:r.raise_for_status.side_effect=RuntimeError('network private detail')
    return r


class MetarArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db();web._cache.clear()
        self.sid=web.db.add_station('London','','','EGLL','metar')
        self.config=dict(web.app.config);web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH='hash')
        self.client=web.app.test_client()
        with self.client.session_transaction() as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
        self.client.environ_base['HTTP_X_CSRF_TOKEN']='token'
        self.limiter=patch.object(archive.utils.AWC_LIMITER,'allow',return_value=True);self.limiter.start()
        self.block=patch.dict(archive.utils._AWC_STATE,{'blocked_until':0.0,'strikes':0,'last_error':''});self.block.start()
    def tearDown(self):
        self.block.stop();self.limiter.stop();web.db.DB_FILE=self.old_db
        web.app.config.clear();web.app.config.update(self.config);self.tmp.cleanup()
    def test_parse_validates_station_time_temperature_and_duplicates(self):
        now=int(time.time());t=now-60
        data=[report('EGLL',t,11,dewp=6,wspd=10,wdir='VRB',wgst=15),report('EGLL',t,99),
              report('EGLL',now-169*3600,2),report('EGLL',now+60,3),report('KJFK',now-30,20),
              report('EGLL',now-120,None),report('EGLL',True,9),report('EGLL',now-180,float('nan'),rawOb='')]
        rows=archive.parse_reports(data,'EGLL',now)
        self.assertEqual(len(rows),2);self.assertEqual(rows[0][2],11);self.assertEqual(rows[0][6],'VRB')
        self.assertIsNone(rows[1][2]);self.assertEqual(rows[1][1],now-120)
        with self.assertRaises(ValueError):archive.parse_reports({},'EGLL',now)
        with self.assertRaises(ValueError):archive.parse_reports([{}]*(archive.MAX_REPORTS+1),'EGLL',now)
    def test_disabled_station_backfills_and_shares_cache_without_live_polling(self):
        now=int(time.time());old=now-30*3600;recent=now-3600
        other=web.db.add_station('Same ICAO','','','EGLL','metar')
        rows=[report('EGLL',recent,18.3,dewp=5.6,wspd=11,wdir=170),report('EGLL',old,9.4)]
        with patch.object(archive.utils.SESSION,'get',return_value=response(rows)) as request, patch.object(web.utils,'fetch_metar') as live:
            first=self.client.get(f'/api/stations/{self.sid}/weather').get_json()
            second=self.client.get(f'/api/stations/{other}/weather').get_json()
            live.assert_not_called();self.assertEqual(request.call_count,1)
            self.assertEqual(request.call_args.kwargs['params'],{'ids':'EGLL','format':'json','hours':168})
        self.assertEqual(first['station']['enabled'],0);self.assertIn('выключен',first['message'])
        self.assertEqual(first['metar_archive']['status'],'ok')
        self.assertEqual(first['metar_history'],[[old,9.4],[recent,18.3]])
        self.assertEqual(second['metar_history'],first['metar_history'])
        self.assertEqual(first['metar_archive']['reports'][0]['wind_speed'],11)
        self.assertEqual(first['metar_archive']['reports'][0]['raw'],'METAR EGLL TEST')
        self.assertEqual(web.db.get_station_history(self.sid),[])
    def test_local_points_are_preserved_and_override_duplicate_archive(self):
        now=int(time.time());last=now-1800;old=now-6*86400
        web.db.update_station(self.sid,enabled=1)
        web.record_web_metar(self.sid,{'obs_time':last,'temp':17})
        web.record_web_metar(self.sid,{'obs_time':old,'temp':3})
        with patch.object(archive.utils.SESSION,'get',return_value=response([report('EGLL',last,16),report('EGLL',now-30*3600,8)])), \
             patch.object(web.utils,'fetch_metar',return_value=None),patch.object(web,'station_info',return_value=None):
            result=self.client.get(f'/api/stations/{self.sid}/weather').get_json()
        self.assertEqual(result['metar_history'],[[old,3.0],[now-30*3600,8.0],[last,17.0]])
        self.assertIn([old,3.0],result['history']);self.assertEqual(result['history'].count([last,17.0]),1)
        self.assertEqual(result['metar_archive']['reports'][0]['temp'],16.0)
    def test_cache_expiry_global_pruning_and_station_deletion(self):
        now=int(time.time());other=web.db.add_station('other','','','KJFK','metar')
        with web.db.get_db() as conn:
            conn.executemany('INSERT INTO web_metar_archive(icao,obs_time,temp) VALUES(?,?,?)',[
                ('EGLL',now-169*3600,1),('EGLL',now-3600,2),('KJFK',now-169*3600,3),('KJFK',now-3600,4)])
        archive.prune()
        with web.db.get_db() as conn:
            records=conn.execute('SELECT icao,temp FROM web_metar_archive ORDER BY icao').fetchall()
            self.assertEqual([(r['icao'],r['temp']) for r in records],[('EGLL',2),('KJFK',4)])
        duplicate=web.db.add_station('Same','','','EGLL','metar')
        self.assertEqual(self.client.delete(f'/api/stations/{self.sid}',json={}).status_code,200)
        self.assertEqual(len(archive.get('EGLL')['reports']),1)
        self.assertEqual(self.client.delete(f'/api/stations/{duplicate}',json={}).status_code,200)
        self.assertEqual(archive.get('EGLL')['reports'],[]);self.assertEqual(len(archive.get('KJFK')['reports']),1)
    def test_failures_are_visible_keep_cache_and_backoff(self):
        now=int(time.time());item=report('EGLL',now-60,8)
        with patch.object(archive.utils.SESSION,'get',return_value=response([item])):archive.refresh('EGLL')
        with web.db.get_db() as conn:conn.execute('UPDATE web_metar_archive_status SET last_attempt=? WHERE icao=?',(now-archive.SUCCESS_TTL-1,'EGLL'))
        with patch.object(archive.utils.SESSION,'get',side_effect=TimeoutError('secret')) as request:
            archive.refresh('EGLL');archive.refresh('EGLL');self.assertEqual(request.call_count,1)
        result=archive.get('EGLL');self.assertEqual(result['status'],'error');self.assertEqual(result['reports'][0]['temp'],8)
        self.assertNotIn('secret',str(result));self.assertIsNotNone(result['last_success'])
        with web.db.get_db() as conn:conn.execute('UPDATE web_metar_archive_status SET last_attempt=? WHERE icao=?',(now-archive.ERROR_TTL-1,'EGLL'))
        with patch.object(archive.utils.SESSION,'get',return_value=response(status=429)),patch.object(archive.utils,'_awc_penalty') as penalty:
            archive.refresh('EGLL');penalty.assert_called_once_with(429)
        self.assertEqual(archive.get('EGLL')['status'],'throttled')
    def test_empty_and_invalid_responses_are_distinct(self):
        with patch.object(archive.utils.SESSION,'get',return_value=response(status=204)):archive.refresh('EGLL')
        self.assertEqual(archive.get('EGLL')['status'],'empty')
        with web.db.get_db() as conn:conn.execute('DELETE FROM web_metar_archive_status')
        with patch.object(archive.utils.SESSION,'get',return_value=response([report('KJFK',int(time.time())-1)])):archive.refresh('EGLL')
        self.assertEqual(archive.get('EGLL')['status'],'error')
    def test_rate_limits_and_response_size_are_bounded(self):
        with patch.object(archive.utils.SESSION,'get',return_value=response(status=403)) as request, patch.object(archive.utils,'_awc_penalty') as penalty:
            archive.refresh('EGLL');penalty.assert_called_once_with(403);request.return_value.close.assert_called_once()
        self.assertEqual(archive.get('EGLL')['status'],'throttled')
        with web.db.get_db() as conn:conn.execute('DELETE FROM web_metar_archive_status')
        with patch.dict(archive.utils._AWC_STATE,{'blocked_until':time.time()+60}):
            with patch.object(archive.utils.SESSION,'get') as request:archive.refresh('EGLL');request.assert_not_called()
        self.assertEqual(archive.get('EGLL')['status'],'throttled')
        with web.db.get_db() as conn:conn.execute('DELETE FROM web_metar_archive_status')
        too_big=response([]);too_big.iter_content.return_value=[b'x'*(archive.MAX_BYTES+1)]
        with patch.object(archive.utils.SESSION,'get',return_value=too_big):archive.refresh('EGLL')
        self.assertEqual(archive.get('EGLL')['status'],'error');too_big.close.assert_called_once()
    def test_independent_icao_and_startup_cleanup(self):
        web.db.add_station('New York','','','KJFK','metar');now=int(time.time())
        with patch.object(archive.utils.SESSION,'get',side_effect=[response([report('EGLL',now-1,1)]),response([report('KJFK',now-1,2)])]) as request:
            archive.refresh('EGLL');archive.refresh('KJFK');archive.refresh('EGLL')
            self.assertEqual(request.call_count,2)
        self.assertEqual(archive.get('EGLL')['reports'][0]['temp'],1)
        self.assertEqual(archive.get('KJFK')['reports'][0]['temp'],2)
        with web.db.get_db() as conn:conn.execute('INSERT INTO web_metar_archive(icao,obs_time,temp) VALUES(?,?,?)',('EGLL',now-archive.WINDOW-1,10))
        archive.init_db()
        with web.db.get_db() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM web_metar_archive').fetchone()[0],2)
    def test_pages_backwards_when_provider_hits_400_and_keeps_7_days(self):
        now=int(time.time()//60*60)-60
        newest=[report('EGLL',now-i*900,i,lat=51.47,lon=-0.45) for i in range(400)]
        # Use realistic temperatures, including an overlap at the page boundary.
        for row in newest:row['temp']=10
        oldest=newest[-1]['obsTime']
        older=[report('EGLL',oldest-i*900,9,lat=51.47,lon=-0.45) for i in range(0,260)]
        with patch.object(archive.utils.SESSION,'get',side_effect=[response(newest),response(older)]) as request:
            archive.refresh('EGLL');self.assertEqual(request.call_count,2)
            self.assertIn('date',request.call_args_list[1].kwargs['params'])
        result=archive.get('EGLL');self.assertEqual(result['status'],'ok')
        self.assertEqual(len(result['reports']),659);self.assertEqual(result['location'],{'lat':51.47,'lon':-0.45})
        self.assertLess(result['reports'][-1]['obs_time'],now-6*86400)
    def test_partial_page_failure_preserves_usable_data_and_shows_partial(self):
        now=int(time.time()//60*60)-60
        first=[report('EGLL',now-i*900,10) for i in range(400)]
        with patch.object(archive.utils.SESSION,'get',side_effect=[response(first),TimeoutError('offline')]):archive.refresh('EGLL')
        result=archive.get('EGLL');self.assertEqual(result['status'],'partial');self.assertEqual(len(result['reports']),400)
        with patch.object(archive.utils.SESSION,'get') as request:archive.refresh('EGLL');request.assert_not_called()
    def test_migrates_48_hour_cache_and_refreshes_7_days_immediately(self):
        now=time.time()
        with web.db.get_db() as c:
            c.execute('DROP TABLE web_metar_archive_status')
            c.execute("CREATE TABLE web_metar_archive_status (icao TEXT PRIMARY KEY,last_attempt REAL NOT NULL,last_success REAL,status TEXT NOT NULL)")
            c.execute("INSERT INTO web_metar_archive_status VALUES(?,?,?,'ok')",('EGLL',now,now))
        archive.init_db()
        with web.db.get_db() as c:self.assertEqual(c.execute('SELECT range_hours FROM web_metar_archive_status').fetchone()[0],48)
        with patch.object(archive.utils.SESSION,'get',return_value=response([report('EGLL',int(now)-5*86400,4)])) as request:
            archive.refresh('EGLL');self.assertEqual(request.call_count,1)
        self.assertEqual(archive.get('EGLL')['reports'][0]['temp'],4)
    def test_database_lease_prevents_concurrent_duplicate_and_respects_deletion(self):
        entered=threading.Event();release=threading.Event();now=int(time.time())
        def slow(*_args,**_kwargs):entered.set();self.assertTrue(release.wait(5));return response([report('EGLL',now-60)])
        with patch.object(archive.utils.SESSION,'get',side_effect=slow) as request:
            thread=threading.Thread(target=archive.refresh,args=('EGLL',));thread.start();self.assertTrue(entered.wait(5))
            archive.refresh('EGLL');self.assertEqual(request.call_count,1);self.assertEqual(archive.get('EGLL')['status'],'loading')
            self.assertEqual(self.client.delete(f'/api/stations/{self.sid}',json={}).status_code,200)
            release.set();thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(archive.get('EGLL')['reports'],[]);self.assertEqual(archive.get('EGLL')['status'],'not_loaded')

if __name__=='__main__':unittest.main()
