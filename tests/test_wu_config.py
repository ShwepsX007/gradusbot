import os
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
import config
from web import app as web, wu_config, wu_forecast


class WUConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=config.BASE_DIR;config.BASE_DIR=self.temp.name
        self.old_db=web.db.DB_FILE;web.db.DB_FILE=os.path.join(self.temp.name,'db.sqlite');web.db.init_db();web.init_web_db()
        self.old_config=dict(web.app.config);web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password'))
        self.client=web.app.test_client()
        with self.client.session_transaction() as sess:sess.update(auth_user='test',auth_version=web.auth_version(),csrf='csrf')
        self.client.environ_base['HTTP_X_CSRF_TOKEN']='csrf'
    def tearDown(self):
        web.db.DB_FILE=self.old_db;web.app.config.clear();web.app.config.update(self.old_config);config.BASE_DIR=self.base;self.temp.cleanup()
    def test_tunnel_password_csrf_and_no_key_leak(self):
        secret='WU_fake_token_123456789'
        payload={'action':'add_key','key':secret,'authorized':True,'current_password':'password'}
        self.assertEqual(self.client.get('/api/wu/config').status_code,403)
        with patch.object(web,'trusted_ssh_tunnel',return_value=True):
            self.assertEqual(self.client.post('/api/wu/config',json={**payload,'current_password':'bad'}).status_code,400)
            self.assertEqual(self.client.post('/api/wu/config',json={**payload,'authorized':False}).status_code,400)
            with patch.object(wu_forecast,'invalidate') as invalidated:
                response=self.client.post('/api/wu/config',json=payload)
                self.assertEqual(response.status_code,200);invalidated.assert_called_once()
            self.assertNotIn(secret,response.get_data(as_text=True))
            get=self.client.get('/api/wu/config');self.assertEqual(get.status_code,200);self.assertNotIn(secret,get.get_data(as_text=True))
            kid=get.get_json()['keys'][0]['id']
            self.client.environ_base.pop('HTTP_X_CSRF_TOKEN')
            self.assertEqual(self.client.post('/api/wu/config',json={'action':'delete_key','id':kid,'current_password':'password'}).status_code,403)
            self.client.environ_base['HTTP_X_CSRF_TOKEN']='csrf'
            response=self.client.post('/api/wu/config',json={'action':'delete_key','id':kid,'current_password':'password'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.get_json()['keys'],[])

if __name__=='__main__':unittest.main()
