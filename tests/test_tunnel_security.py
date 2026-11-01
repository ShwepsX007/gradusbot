import os
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from web import app as web


class TunnelSecurityTests(unittest.TestCase):
    def setUp(self):
        self.previous = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = web.db.DB_FILE
        web.db.DB_FILE = os.path.join(self.temp.name, 'test.db')
        web.db.init_db(); web.init_web_db()
        web.app.config.update(WEB_AUTH_CONFIGURED=True, WEB_USERNAME='test',
            WEB_PASSWORD_HASH=generate_password_hash('old password is long enough'),
            SECRET_KEY='a'*48, SESSION_COOKIE_SECURE=False, WEB_TUNNEL_ONLY=True,
            WEB_TRUST_PROXY=False, TESTING=True)
        web._login_attempts.clear()
        self.client = web.app.test_client()
        self.client.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108',
            'REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
        self.base = 'http://127.0.0.1:8108'

    def tearDown(self):
        web.db.DB_FILE = self.old_db; self.temp.cleanup()
        web.app.config.clear(); web.app.config.update(self.previous)

    def get(self, path, **kw): return self.client.get(path,base_url=self.base,**kw)
    def post(self, path, **kw): return self.client.post(path,base_url=self.base,**kw)
    def login(self, password):
        self.get('/login')
        with self.client.session_transaction(base_url=self.base) as sess: token=sess['csrf']
        return self.post('/login',data={'username':'test','password':password,'csrf_token':token})

    def test_tunnel_only_fail_closed_and_headers_do_not_bypass(self):
        self.assertEqual(self.get('/login').status_code,200)
        bad = [({'SERVER_NAME':'0.0.0.0'},{}),({'REMOTE_ADDR':'203.0.113.2'},{}),
               ({'gunicorn.socket':None},{}),({}, {'Host':'evil.example:8108'}),
               ({'SERVER_NAME':'0.0.0.0'},{'X-Forwarded-Proto':'https','X-Forwarded-For':'127.0.0.1'})]
        for env,headers in bad:
            with self.subTest(env=env,headers=headers):
                self.assertEqual(self.get('/login',environ_overrides=env,headers=headers).status_code,403)
        web.app.config['WEB_TRUST_PROXY']=True
        self.assertEqual(self.get('/login').status_code,403)

    def test_password_change_requires_current_csrf_and_tunnel_and_revokes_sessions(self):
        self.assertEqual(self.login('old password is long enough').status_code,302)
        with self.client.session_transaction(base_url=self.base) as sess: token=sess['csrf']
        data={'current_password':'old password is long enough','new_password':'new password is long enough'}
        self.assertEqual(self.post('/api/change-password',json=data).status_code,403)
        self.assertEqual(self.post('/api/change-password',json={**data,'current_password':'wrong'},headers={'X-CSRF-Token':token}).status_code,400)
        self.assertEqual(self.post('/api/change-password',json={**data,'new_password':'short'},headers={'X-CSRF-Token':token}).status_code,400)
        self.assertEqual(self.post('/api/change-password',json=data,environ_overrides={'SERVER_NAME':'0.0.0.0'},headers={'X-CSRF-Token':token}).status_code,403)
        # A second browser's previously issued cookie must stop working too.
        other=web.app.test_client();other.environ_base.update(self.client.environ_base)
        with other.session_transaction(base_url=self.base) as sess:
            sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='other'
        self.assertEqual(other.get('/api/session',base_url=self.base).status_code,200)
        self.assertEqual(self.post('/api/change-password',json=data,headers={'X-CSRF-Token':token}).status_code,200)
        self.assertEqual(other.get('/api/session',base_url=self.base).status_code,401)
        self.assertEqual(self.get('/api/session').status_code,401)
        self.assertEqual(self.login('old password is long enough').status_code,401)
        self.assertEqual(self.login('new password is long enough').status_code,302)
        with web.db.get_db() as conn:
            stored=conn.execute('SELECT password_hash FROM web_auth_password WHERE id=1').fetchone()[0]
        self.assertNotIn(data['new_password'], stored)
        # Persistent hash works after worker restart/config reload.
        web.app.config['WEB_PASSWORD_HASH']=generate_password_hash('another old env password')
        self.assertEqual(self.get('/api/session').status_code,200)

    def test_password_change_denied_without_opt_in(self):
        web.app.config['WEB_TUNNEL_ONLY']=False
        self.assertEqual(self.login('old password is long enough').status_code,302)
        with self.client.session_transaction(base_url=self.base) as sess: token=sess['csrf']
        result=self.post('/api/change-password',json={'current_password':'old password is long enough','new_password':'new password is long enough'},headers={'X-CSRF-Token':token})
        self.assertEqual(result.status_code,403)


if __name__=='__main__': unittest.main()
