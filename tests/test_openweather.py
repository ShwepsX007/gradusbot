import json
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from email.utils import formatdate
from werkzeug.security import generate_password_hash
from web import app as web, openweather as ow, forecast_ensemble as fe, ai_forecast as ai

KEY='abcd'+'0123456789abcdef'*2

def response(lat=51.47,lon=-.45,now=None,status=200,headers=None):
    now=now or time.time();start=int(now//3600*3600)
    r=Mock();r.status_code=status;r.headers=headers or {}
    body={'lat':lat,'lon':lon,'timezone':'Europe/London','hourly':[{'dt':start+i*3600,'temp':10+i,'humidity':75,'clouds':10,'wind_speed':3,'pop':.5,'secret':'DO_NOT_RETURN'} for i in range(3)]}
    r.iter_content.return_value=[json.dumps(body).encode()];return r

class OpenWeatherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE;self.base=ow.trading_account.config.BASE_DIR;self.config=dict(web.app.config)
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');ow.trading_account.config.BASE_DIR=self.tmp.name
        web.db.init_db();web.init_web_db();web._cache.clear()
    def tearDown(self):
        web.db.DB_FILE=self.old;ow.trading_account.config.BASE_DIR=self.base;web.app.config.clear();web.app.config.update(self.config);self.tmp.cleanup()
    def test_key_permissions_not_public_and_no_symlink(self):
        self.assertFalse(ow.public_config()['configured']);out=ow.save_key(KEY)
        self.assertTrue(out['configured']);self.assertNotIn(KEY,str(out))
        path=os.path.join(self.tmp.name,'.gradus-private','openweather.json')
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode),0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode),0o700)
        os.chmod(path,0o644)
        with self.assertRaises(ow.OpenWeatherError):ow.public_config()
        os.chmod(path,0o600);os.unlink(path);os.symlink('/etc/passwd',path)
        with self.assertRaises(Exception):ow.save_key(KEY)
        os.unlink(path);ow.save_key(KEY);ow.delete_key();self.assertFalse(ow.public_config()['configured'])
    def test_two_hour_cache_persists_and_no_secret_in_result(self):
        ow.save_key(KEY);now=time.time();r=response(now=now)
        with patch.object(ow.requests,'get',return_value=r) as get:
            a=ow.get_forecast(51.47,-.45);b=ow.get_forecast(51.470001,-.450001)
            self.assertEqual(get.call_count,1);self.assertEqual(a['status'],'ok');self.assertEqual(a['data'],b['data'])
            self.assertEqual(get.call_args.kwargs['params']['exclude'],'current,minutely,daily,alerts')
            self.assertEqual(get.call_args.kwargs['params']['appid'],KEY);self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertNotIn(KEY,json.dumps(a));self.assertNotIn('DO_NOT_RETURN',json.dumps(a))
            web.init_web_db();ow.get_forecast(51.47,-.45);self.assertEqual(get.call_count,1)
            with web.db.get_db() as c:c.execute('UPDATE web_openweather_cache SET next_attempt=?',(now-1,))
            ow.get_forecast(51.47,-.45);self.assertEqual(get.call_count,2)
            self.assertGreaterEqual(a['next_attempt'],now+7199)
    def test_errors_never_retry_within_two_hours_and_do_not_leak(self):
        ow.save_key(KEY)
        with patch.object(ow.requests,'get',side_effect=TimeoutError(KEY)) as get:
            a=ow.get_forecast(1,2);b=ow.get_forecast(1,2)
        self.assertEqual(get.call_count,1);self.assertEqual(a['status'],'error');self.assertIsNone(b['data']);self.assertNotIn(KEY,str(a))
        with patch.object(ow.requests,'get',return_value=response(status=401)) as get:
            self.assertEqual(ow.get_forecast(3,4)['status'],'unauthorized');self.assertEqual(ow.get_forecast(3,4)['status'],'unauthorized');self.assertEqual(get.call_count,1)
    def test_throttle_is_global_and_respects_retry_after(self):
        ow.save_key(KEY);now=time.time()
        with patch.object(ow.requests,'get',return_value=response(status=429,headers={'Retry-After':'10000'})) as get:
            a=ow.get_forecast(1,2);b=ow.get_forecast(3,4)
        self.assertEqual(get.call_count,1);self.assertEqual(a['status'],'throttled');self.assertEqual(b['status'],'throttled')
        self.assertGreater(a['next_attempt'],now+9900)
    def test_shared_daily_limit_counts_attempts_and_is_rolling(self):
        ow.save_key(KEY);now=time.time()
        with web.db.get_db() as c:c.executemany('INSERT INTO web_openweather_calls(ts) VALUES(?)',[(now,)]*ow.DAILY_LIMIT)
        with patch.object(ow.requests,'get') as get:
            self.assertEqual(ow.get_forecast(1,2)['status'],'local_limit');get.assert_not_called()
        with web.db.get_db() as c:c.execute('UPDATE web_openweather_calls SET ts=?',(now-86401,))
        with patch.object(ow.requests,'get',return_value=response(status=401)) as get:
            self.assertEqual(ow.get_forecast(1,2)['status'],'unauthorized');self.assertEqual(get.call_count,1)
    def test_same_station_concurrent_calls_make_only_one_request(self):
        ow.save_key(KEY);started=threading.Event();finish=threading.Event();out=[]
        def get(*args,**kwargs):started.set();self.assertTrue(finish.wait(5));return response()
        with patch.object(ow.requests,'get',side_effect=get) as mock:
            thread=threading.Thread(target=lambda:out.append(ow.get_forecast(51.47,-.45)));thread.start();self.assertTrue(started.wait(5))
            self.assertEqual(ow.get_forecast(51.47,-.45)['status'],'loading');finish.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(mock.call_count,1);self.assertEqual(out[0]['status'],'ok')
    def test_concurrent_different_locations_cannot_exceed_daily_limit(self):
        ow.save_key(KEY);now=time.time()
        with web.db.get_db() as c:c.executemany('INSERT INTO web_openweather_calls(ts) VALUES(?)',[(now,)]*(ow.DAILY_LIMIT-1))
        started=threading.Event();finish=threading.Event();out=[]
        def get(*args,**kwargs):started.set();self.assertTrue(finish.wait(5));return response(status=401)
        with patch.object(ow.requests,'get',side_effect=get) as mock:
            thread=threading.Thread(target=lambda:out.append(ow.get_forecast(1,2)));thread.start();self.assertTrue(started.wait(5))
            self.assertEqual(ow.get_forecast(3,4)['status'],'local_limit');finish.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(mock.call_count,1)
        with web.db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_openweather_calls').fetchone()[0],ow.DAILY_LIMIT)
    def test_retry_after_http_date(self):
        ow.save_key(KEY);now=time.time()
        with patch.object(ow.requests,'get',return_value=response(status=429,headers={'Retry-After':formatdate(now+10000,usegmt=True)})):
            result=ow.get_forecast(1,2)
        self.assertGreater(result['next_attempt'],now+9900)
    def test_deleted_key_during_request_never_returns_data(self):
        ow.save_key(KEY)
        def get(*args,**kwargs):ow.delete_key();return response()
        with patch.object(ow.requests,'get',side_effect=get):result=ow.get_forecast(51.47,-.45)
        self.assertEqual(result['status'],'config_changed');self.assertIsNone(result['data'])
        self.assertEqual(ow.get_forecast(51.47,-.45)['status'],'not_configured')
    def test_key_rotation_does_not_bypass_failure_interval_or_global_429(self):
        ow.save_key(KEY)
        def get(*args,**kwargs):
            ow.save_key('another0123456789abcdef')
            return response(status=429,headers={'Retry-After':'8000'})
        with patch.object(ow.requests,'get',side_effect=get) as mock:
            self.assertEqual(ow.get_forecast(1,2)['status'],'config_changed')
            self.assertEqual(ow.get_forecast(1,2)['status'],'config_changed')
            self.assertEqual(ow.get_forecast(3,4)['status'],'throttled')
            self.assertEqual(mock.call_count,1)
    def test_weather_only_calls_openweather_for_active_view(self):
        sid=web.db.add_station('London','','','EGLL','metar');web.db.update_station(sid,enabled=1)
        now=time.time();data={'timezone':'UTC','points':[{'time':int(now)+3600,'temp':12}]}
        with web.app.test_request_context('/api/stations/1/weather'), patch.object(web.metar_archive,'refresh'), patch.object(web.metar_archive,'get',return_value={}), patch.object(web,'station_info',return_value={'lat':51.47,'lon':-.45}), patch.object(web,'forecast',return_value=None), patch.object(fe,'get_metno',return_value={'status':'error','data':None}),patch.object(web.utils,'fetch_metar',return_value=None),patch.object(ow,'get_forecast',return_value={'status':'ok','data':data}) as get:
            inactive=web.weather(sid).get_json();get.assert_not_called();self.assertEqual(inactive['forecast_status']['openweather'],'view_inactive')
            active=web.weather(sid,active_view=True).get_json();get.assert_called_once_with('51.4700','-0.4500')
            self.assertEqual(active['forecast_openweather'],data);self.assertEqual(active['forecast_ensemble']['points'][0]['sources']['OpenWeather'],12)
    def test_bad_payload_or_wrong_coordinates_not_used(self):
        ow.save_key(KEY);r=response(lat=20,lon=20)
        with patch.object(ow.requests,'get',return_value=r):self.assertEqual(ow.get_forecast(51.47,-.45)['status'],'error')
    def test_ensemble_uses_owm_hourly_and_gemini_sees_clean_fields(self):
        now=int(time.time());t=now+3600
        opened={'hourly':{'epoch':[t,t+1800,t+7200],'temperature_2m':[10,10,10]}}
        met={'points':[[t,20],[t+3600,20]]}
        data={'points':[{'time':t,'temp':30,'humidity':75,'pop':.5,'private_key':'SECRET'}, {'time':t+3600,'temp':30}]}
        points=fe.ensemble(opened,met,'UTC',data)['points'];lookup={p['time']:p for p in points}
        self.assertEqual(lookup[t]['temp'],20);self.assertEqual(lookup[t]['sources']['OpenWeather'],30)
        self.assertTrue(lookup[t+1800]['interpolated']);self.assertNotIn('OpenWeather',lookup[t+7200]['sources'])
        context=ai.build_context({'station':{},'forecast_ensemble':{'timezone':'UTC','points':points},'forecast_openweather':data,'forecast_status':{'openweather':'ok','openweather_fetched_at':now}})
        self.assertEqual(context['forecast']['hourly_openweather'][0]['humidity'],75)
        self.assertEqual(context['forecast']['hourly_average_and_sources'][0]['sources']['OpenWeather'],30)
        self.assertNotIn('SECRET',json.dumps(context));self.assertEqual(context['forecast']['status']['openweather'],'ok')
    def test_config_route_requires_tunnel_password_and_csrf(self):
        web.app.config.update(TESTING=True,WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('long password for tests'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False)
        c=web.app.test_client();base='http://127.0.0.1:8108';c.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
        with c.session_transaction(base_url=base) as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
        payload={'key':KEY,'current_password':'long password for tests'}
        self.assertEqual(c.post('/api/openweather/config',base_url=base,json=payload).status_code,403)
        headers={'X-CSRF-Token':'token'}
        with patch.object(web,'trusted_ssh_tunnel',return_value=False):self.assertEqual(c.post('/api/openweather/config',base_url=base,json=payload,headers=headers).status_code,403)
        self.assertEqual(c.post('/api/openweather/config',base_url=base,json={**payload,'current_password':'wrong'},headers=headers).status_code,400)
        saved=c.post('/api/openweather/config',base_url=base,json=payload,headers=headers);self.assertEqual(saved.status_code,200);self.assertNotIn(KEY,str(saved.get_json()))
        self.assertTrue(c.get('/api/openweather/config',base_url=base).get_json()['configured'])
        self.assertEqual(c.delete('/api/openweather/config',base_url=base,json={'current_password':payload['current_password']},headers=headers).status_code,200)
