import json
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests
from werkzeug.security import generate_password_hash
from web import app as web
from web import ai_forecast as ai

K1='AIza'+'a'*30
K2='AIza'+'b'*30
K3='AIza'+'c'*30

class Response:
    def __init__(self,status=200,data=None,headers=None):
        self.status_code=status;self.data=data or {};self.headers=headers or {};self.closed=False
    def json(self):return self.data
    def iter_content(self,chunk_size=1):yield json.dumps(self.data).encode()
    def close(self):self.closed=True


def answer(text='Температура 20°C',reason='STOP'):
    return Response(200,{'candidates':[{'finishReason':reason,'content':{'parts':[{'text':text}]}}],
        'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':30,'totalTokenCount':130}})

class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE;self.old_base=ai.config.BASE_DIR
        web.db.DB_FILE=os.path.join(self.tmp.name,'db.sqlite');ai.config.BASE_DIR=self.tmp.name
        web.db.init_db();web.init_web_db();web._cache.clear()
        self.sid=web.db.add_station('London','','','EGLL','metar')
        self.now=int(time.time())
        self.weather={'station':{'name':'London','station_code':'EGLL','enabled':1,'private_key':'LEAK'},
          'metar_history':[[self.now-60,12.5]],'metar_archive':{'status':'partial','last_success':self.now,'reports':[{'obs_time':self.now-60,'temp':12.5,'raw':'EGLL 101200Z'}]},
          'metar':{'temp':12.5,'raw_metar':'EGLL 101200Z','wallet':'LEAK'},'info':{'lat':51.4,'lon':0.1},
          'forecast_ensemble':{'timezone':'Europe/London','points':[{'time':self.now+3600,'temp':15,'sources':{'Open-Meteo':14,'MET Norway':16},'wallet':'LEAK'}]},
          'forecast_status':{'reason':'ok','open_meteo':'ok','met_norway':'ok','wallet':'LEAK'},
          'forecast':{'hourly':{'epoch':[self.now+3600],'precipitation_probability':[80]},'daily':{'time':['2026-11-03'],'temperature_2m_max':[15],'secret':['LEAK']}},
          'positions':[{'wallet':'LEAK'}],'private_key':'LEAK'}
    def tearDown(self):
        web.db.DB_FILE=self.old_db;ai.config.BASE_DIR=self.old_base;self.tmp.cleanup()
    def add(self,p,k):return ai.config_update({'action':'add_key','project':p,'key':k})
    def enable_switch(self,enable=True):
        return ai.config_update({'action':'save','model':ai.DEFAULT_MODEL,'prompt':ai.DEFAULT_PROMPT,'switch_authorized':enable})
    def test_storage_permissions_and_no_key_in_public_or_results(self):
        public=self.add('project-alpha',K1)
        self.assertNotIn(K1,json.dumps(public));self.assertEqual(public['keys'][0]['project'],'project-alpha')
        directory=os.path.join(self.tmp.name,'.gradus-private');path=os.path.join(directory,'gemini.json')
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode),0o700);self.assertEqual(stat.S_IMODE(os.stat(path).st_mode),0o600)
        with patch.object(ai.requests,'post',return_value=answer('не показывать '+K1)) as post:
            result=ai.generate(self.sid,self.weather)
        self.assertNotIn(K1,json.dumps(result,ensure_ascii=False));self.assertIn('[скрыто]',result['text'])
        self.assertEqual(ai.latest(self.sid)['input_summary']['sources']['MET Norway'],1)
        args,kw=post.call_args;self.assertNotIn(K1,args[0]);self.assertEqual(kw['headers']['x-goog-api-key'],K1)
        body=json.dumps(kw['json'],ensure_ascii=False);self.assertNotIn(K1,body);self.assertNotIn('LEAK',body)
        self.assertNotIn('tools',kw['json']);self.assertFalse(kw['allow_redirects']);self.assertEqual(kw['timeout'],(3,45))
    def test_authorized_switch_skips_same_project_and_remembers_429(self):
        self.add('project-alpha',K1);self.add('project-alpha',K2);self.add('project-beta',K3);self.enable_switch()
        r=Response(429,{'error':{'details':[{'retryDelay':'600s'}]}},headers={'Retry-After':'900'})
        with patch.object(ai.requests,'post',side_effect=[r,answer()]) as post:
            result=ai.generate(self.sid,self.weather)
        self.assertEqual(result['project'],'project-beta');self.assertTrue(r.closed)
        self.assertEqual([x.kwargs['headers']['x-goog-api-key'] for x in post.call_args_list],[K1,K3])
        with web.db.get_db() as c:blocked=c.execute('SELECT blocked_until FROM web_ai_quota WHERE project=?',('project-alpha',)).fetchone()[0]
        self.assertGreaterEqual(blocked,time.time()+890)
        with patch.object(ai.requests,'post',return_value=answer()) as post:
            ai.generate(self.sid,self.weather)
        self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'],K3)
    def test_default_429_does_not_switch_or_retry_during_cooldown(self):
        self.add('project-alpha',K1);self.add('project-beta',K3)
        with patch.object(ai.requests,'post',return_value=Response(429)) as post:
            with self.assertRaises(ai.AIError) as err:ai.generate(self.sid,self.weather)
            self.assertEqual(err.exception.status,429);self.assertEqual(post.call_count,1)
            with self.assertRaises(ai.AIError):ai.generate(self.sid,self.weather)
            self.assertEqual(post.call_count,1)
    def test_timeout_denial_and_invalid_responses_never_switch(self):
        self.add('project-alpha',K1);self.add('project-beta',K3);self.enable_switch()
        for reply in [requests.Timeout('SECRET'),Response(403),Response(500),Response(200,{}),answer('','MAX_TOKENS')]:
            with self.subTest(reply=reply),patch.object(ai.requests,'post',side_effect=reply if isinstance(reply,Exception) else [reply]) as post:
                with self.assertRaises(ai.AIError) as e:ai.generate(self.sid,self.weather)
                self.assertNotIn('SECRET',str(e.exception));self.assertEqual(post.call_count,1)
    def test_result_truncation_and_per_station(self):
        self.add('project-alpha',K1)
        with patch.object(ai.requests,'post',return_value=answer('ok','MAX_TOKENS')):result=ai.generate(self.sid,self.weather)
        self.assertTrue(result['truncated']);self.assertIsNone(ai.latest(self.sid+100))
        with web.db.get_db() as c:c.execute('DELETE FROM stations WHERE id=?',(self.sid,))
        with patch.object(ai.requests,'post',return_value=answer()):
            with self.assertRaises(ai.AIError) as e:ai.generate(self.sid,self.weather)
        self.assertEqual(e.exception.status,404)
    def test_serialization_lock_even_with_other_thread(self):
        first=ai._claim();errors=[]
        def work():
            try:ai._claim()
            except ai.AIError as e:errors.append(e.status)
        t=threading.Thread(target=work);t.start();t.join();self.assertEqual(errors,[409]);ai._release(first)
        second=ai._claim();ai._release(second)
    def test_unsafe_permissions_and_symlinks_rejected(self):
        self.add('project-alpha',K1);path=os.path.join(self.tmp.name,'.gradus-private','gemini.json')
        os.chmod(path,0o644)
        with self.assertRaises(ai.AIError):ai.public_config()
        os.chmod(path,0o600);os.unlink(path);os.symlink('/etc/passwd',path)
        with self.assertRaises(ai.AIError):ai.public_config()
    def test_input_empty_and_prompt_secret_rejected(self):
        self.add('project-alpha',K1)
        with self.assertRaises(ai.AIError):ai.config_update({'action':'save','model':ai.DEFAULT_MODEL,'prompt':K1,'switch_authorized':False})
        with patch.object(ai.requests,'post') as post:
            with self.assertRaises(ai.AIError):ai.generate(self.sid,{})
        post.assert_not_called()
    def test_concurrent_key_changes_are_not_lost(self):
        errors=[]
        def add(i):
            try:self.add('project-%03d'%i,'AIza'+str(i)*30)
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=add,args=(i,)) for i in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[]);self.assertEqual(len(ai.public_config()['keys']),4)
    def test_long_429_and_truncated_archive(self):
        self.add('project-alpha',K1)
        with patch.object(ai.requests,'post',return_value=Response(429,headers={'Retry-After':'172800'})):
            with self.assertRaises(ai.AIError):ai.generate(self.sid,self.weather)
        with web.db.get_db() as c:until=c.execute('SELECT blocked_until FROM web_ai_quota').fetchone()[0]
        self.assertGreater(until,time.time()+172700)
        self.weather['metar_history']=[[self.now-i*30,10] for i in range(1500)]
        context=ai.build_context(self.weather)
        self.assertEqual(len(context['archive']['time_temperature_c_utc']),1000)
        self.assertTrue(context['archive']['temperature_points_truncated'])
        self.assertEqual(context['archive']['time_temperature_c_utc'][-1][0],self.now)
    def test_response_size_limit_never_switches(self):
        self.add('project-alpha',K1);self.add('project-beta',K3);self.enable_switch()
        oversized=answer('x'*(ai.MAX_BODY+1))
        with patch.object(ai.requests,'post',return_value=oversized) as post:
            with self.assertRaises(ai.AIError):ai.generate(self.sid,self.weather)
        self.assertEqual(post.call_count,1);self.assertTrue(oversized.closed)
    def test_gemini_429_exposed_to_browser_without_retry_or_secrets(self):
        self.add('project-alpha',K1)
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='testuser',WEB_PASSWORD_HASH=generate_password_hash('password'))
        client=web.app.test_client()
        with client.session_transaction() as sess:sess.update(auth_user='testuser',auth_version=web.auth_version(),csrf='csrf')
        client.environ_base['HTTP_X_CSRF_TOKEN']='csrf'
        with patch.object(web,'trusted_ssh_tunnel',return_value=True),patch.object(web,'weather',side_effect=lambda *a,**kw:web.jsonify(self.weather)),patch.object(ai.requests,'post',return_value=Response(429,headers={'Retry-After':'600'})) as post:
            response=client.post(f'/api/stations/{self.sid}/ai-forecast',json={'confirm':True})
        self.assertEqual(response.status_code,429);self.assertIn('Лимит Gemini',response.get_json()['error'])
        self.assertNotIn(K1,response.get_data(as_text=True));self.assertEqual(post.call_count,1)

    def test_real_weather_snapshot_gemini_succeeds_without_model_providers(self):
        # Exercise the real weather->Gemini route, not only a mocked snapshot.
        # A METAR is sufficient even when both forecast providers are down.
        self.add('project-alpha',K1)
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='testuser',WEB_PASSWORD_HASH=generate_password_hash('password'))
        client=web.app.test_client()
        with client.session_transaction() as sess:sess.update(auth_user='testuser',auth_version=web.auth_version(),csrf='csrf')
        client.environ_base['HTTP_X_CSRF_TOKEN']='csrf'
        metar={'temp':12.5,'obs_time':self.now-60,'raw_metar':'EGLL 101200Z'}
        with patch.object(web,'trusted_ssh_tunnel',return_value=True),patch.object(web.metar_archive,'refresh'),patch.object(web.metar_archive,'get',return_value={'status':'error','reports':[]}),patch.object(web,'station_info',return_value={'lat':51.47,'lon':-.45}),patch.object(web.utils,'fetch_metar',return_value=metar),patch.object(web,'forecast',return_value=None),patch.object(web.forecast_ensemble,'get_metno',return_value={'status':'error','data':None}),patch.object(ai.requests,'post',return_value=answer()) as post:
            result=client.post(f'/api/stations/{self.sid}/ai-forecast',json={'confirm':True})
        self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        self.assertEqual(result.get_json()['forecast']['text'],'Температура 20°C')
        sent=json.dumps(post.call_args.kwargs['json'],ensure_ascii=False)
        self.assertIn('EGLL 101200Z',sent);self.assertNotIn(K1,sent)
        self.assertEqual(result.get_json()['forecast']['input_summary']['sources'],{'Open-Meteo':0,'MET Norway':0})

    def test_web_tunnel_password_csrf_and_server_snapshot(self):
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='testuser',WEB_PASSWORD_HASH=generate_password_hash('password'))
        client=web.app.test_client()
        with client.session_transaction() as sess:sess.update(auth_user='testuser',auth_version=web.auth_version(),csrf='csrf')
        client.environ_base['HTTP_X_CSRF_TOKEN']='csrf'
        data={'action':'add_key','project':'project-alpha','key':K1,'current_password':'password'}
        self.assertEqual(client.get('/api/ai/config').status_code,403)
        with patch.object(web,'trusted_ssh_tunnel',return_value=True):
            bad=client.post('/api/ai/config',json={**data,'current_password':'wrong'});self.assertEqual(bad.status_code,400)
            ok=client.post('/api/ai/config',json=data);self.assertEqual(ok.status_code,200);self.assertNotIn(K1,ok.get_data(as_text=True))
            self.assertEqual(client.post(f'/api/stations/{self.sid}/ai-forecast',json={}).status_code,400)
            with patch.object(web,'weather',side_effect=lambda sid,active_view=False:web.jsonify(self.weather)) as weather,patch.object(ai.requests,'post',return_value=answer()) as post:
                res=client.post(f'/api/stations/{self.sid}/ai-forecast',json={'confirm':True,'private_key':'LEAK'})
            self.assertEqual(res.status_code,200,res.get_data(as_text=True));self.assertNotIn('LEAK',json.dumps(post.call_args.kwargs['json']))
            weather.assert_called_once_with(self.sid,active_view=True)
            self.assertEqual(client.get(f'/api/stations/{self.sid}/ai-forecast').get_json()['forecast']['station_id'],self.sid)
            client.environ_base['HTTP_X_CSRF_TOKEN']='bad'
            self.assertEqual(client.post('/api/ai/config',json=data).status_code,403)
