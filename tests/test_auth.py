import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from web import app as web


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = generate_password_hash('a very long test password')

    def setUp(self):
        self.previous = dict(web.app.config)
        web.app.config.update(WEB_AUTH_CONFIGURED=True, WEB_USERNAME='testuser', WEB_PASSWORD_HASH=self.password_hash,
                              SECRET_KEY='a' * 48, SESSION_COOKIE_SECURE=False, TESTING=True)
        web._login_attempts.clear()
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.previous)

    def login(self, password='a very long test password', username='testuser'):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            csrf = sess['csrf']
        return self.client.post('/login', data={'username': username, 'password': password, 'csrf_token': csrf})

    def test_missing_environment_fails_closed_at_startup(self):
        env = os.environ.copy()
        for name in ('GRADUS_USERNAME', 'GRADUS_PASSWORD_HASH', 'GRADUS_SESSION_SECRET'):
            env.pop(name, None)
        code = "from web.app import app; assert app.test_client().get('/api/stations').status_code == 503"
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_anonymous_protected_and_fail_closed(self):
        for path in ('/api/stations', '/api/settings', '/api/markets', '/api/session'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/stations', json={}).status_code, 401)
        for path in ('/', '/static/index.html', '/static/app.js'):
            self.assertEqual(self.client.get(path).status_code, 302)
        response = self.client.get('/static/login.css')
        self.assertEqual(response.status_code, 200)
        response.close()
        web.app.config['WEB_AUTH_CONFIGURED'] = False
        for path in ('/', '/login', '/api/stations', '/static/login.css'):
            self.assertEqual(self.client.get(path).status_code, 503)

    def test_login_logout_csrf_cookie_and_origin(self):
        self.assertEqual(self.client.post('/login', data={'username': 'testuser', 'password': 'a very long test password'}).status_code, 403)
        self.assertEqual(self.login('wrong').status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 302)
        cookie = response.headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        response.close()
        response = self.client.get('/static/app.js')
        self.assertEqual(response.status_code, 200)
        response.close()
        token = self.client.get('/api/session').get_json()['csrf']
        self.assertEqual(self.client.post('/api/logout', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', json={}, headers={'X-CSRF-Token': 'неверный'}).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', json={}, headers={'X-CSRF-Token': token, 'Origin': 'http://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', json={}, headers={'X-CSRF-Token': token, 'Origin': 'https://localhost'}).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', json={}, headers={'X-CSRF-Token': token, 'Origin': 'http://localhost'}).status_code, 200)
        self.assertEqual(self.client.get('/api/stations').status_code, 401)

    def test_attempt_limit_and_unicode_username(self):
        for _ in range(5):
            self.assertEqual(self.login('bad', username='юзер').status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        with patch.object(web.time, 'monotonic', return_value=web.time.monotonic() + 901):
            self.assertEqual(self.login().status_code, 302)

    def test_password_change_invalidates_existing_cookie(self):
        self.assertEqual(self.login().status_code, 302)
        web.app.config['WEB_PASSWORD_HASH'] = generate_password_hash('another long test password')
        self.assertEqual(self.client.get('/api/stations').status_code, 401)

    def test_secure_cookie_default_and_insecure_warning(self):
        web.app.config['SESSION_COOKIE_SECURE'] = True
        response = self.client.get('/login')
        self.assertIn('Secure', response.headers['Set-Cookie'])
        self.assertIn('без шифрования', response.get_data(as_text=True))

    def test_create_config_script_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'auth.env')
            # Run with getpass mocked rather than exposing a password in shell history.
            code = "from unittest.mock import patch; from web.create_login import main; " \
                   "import sys; sys.argv=['create_login', sys.argv[1], '--insecure-http']; " \
                   "p=patch('builtins.input',return_value='testuser'); p.start(); " \
                   "q=patch('getpass.getpass',return_value='a very long test password'); q.start(); main()"
            result = subprocess.run([sys.executable, '-c', code, path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path) as f:
                content = f.read()
            self.assertNotIn('a very long test password', content)
            self.assertIn('WEB_COOKIE_SECURE=0', content)
            result = subprocess.run([sys.executable, '-c', code, path], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            with open(path) as f:
                self.assertEqual(f.read(), content)


if __name__ == '__main__':
    unittest.main()
