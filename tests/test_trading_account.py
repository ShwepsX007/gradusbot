import json
import os
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
from werkzeug.security import generate_password_hash
from web import app as web
from web import trading_account as account

# Publicly known test scalar, no wallet funds and never submitted to a service.
CREDS={'private_key':'0x'+'0'*63+'1','api_key':'key-test', 'api_secret':'secret-test',
       'api_passphrase':'passphrase-test','signature_type':'3','funder':'0x'+'2'*40}


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(account.config,'BASE_DIR',self.tmp.name);self.patch.start()
    def tearDown(self): self.patch.stop();self.tmp.cleanup()

    def test_store_permissions_redaction_and_validation(self):
        info=account.save(CREDS)
        self.assertEqual(info['wallet'],CREDS['funder']);self.assertNotIn('key-test',str(info))
        path=os.path.join(self.tmp.name,'.gradus-private','account.json')
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode),0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode),0o700)
        self.assertEqual(account.load()['api_secret'],'secret-test')
        self.assertNotIn('secret-test',str(account.public_config()))
        for bad in ({**CREDS,'private_key':'bad'},{**CREDS,'signature_type':'9'},
                    {**CREDS,'funder':'https://evil.example'}, {**CREDS,'funder':''},
                    {**CREDS,'signature_type':'0','funder':CREDS['funder']},
                    {**CREDS,'api_secret':''}, {**CREDS,'api_key':'bad\nkey'}):
            with self.subTest(bad_field=True):
                with self.assertRaises(account.AccountError): account.save(bad)
        self.assertEqual(account.load()['api_secret'],'secret-test')

    def test_insecure_directory_file_and_symlink_fail_closed(self):
        account.save(CREDS)
        path=os.path.join(self.tmp.name,'.gradus-private','account.json')
        os.chmod(path,0o644)
        with self.assertRaises(account.AccountError): account.load()
        with self.assertRaises(account.AccountError): account.save(CREDS)
        os.chmod(path,0o600); os.unlink(path);os.symlink('/etc/passwd',path)
        with self.assertRaises(account.AccountError): account.load()
        with self.assertRaises(account.AccountError): account.save(CREDS)
        os.unlink(path);os.chmod(os.path.dirname(path),0o755)
        with self.assertRaises(account.AccountError): account.save(CREDS)

    def test_snapshot_isolates_failures_and_excludes_credentials(self):
        account.save(CREDS)
        client=Mock();client.get_open_orders.return_value=[{'id':'abc','asset_id':'123','side':'BUY','price':'0.4','original_size':'10','size_matched':'2','status':'LIVE','secret':'SECRET'}]
        response=Mock();response.json.return_value=[{'asset':'123','eventSlug':'e','title':'Event','outcome':'Yes','size':3,'avgPrice':0.4,'curPrice':0.5,'cashPnl':0.3,'private':'SECRET'}]
        with patch.object(account,'build_client',return_value=client),patch.object(account.requests,'get',return_value=response) as get:
            result=account.account_snapshot()
        self.assertEqual(result['orders'][0]['size_matched'],2)
        self.assertEqual(result['positions'][0]['event_slug'],'e')
        self.assertNotIn('SECRET',str(result));self.assertNotIn('secret-test',str(result))
        self.assertEqual(get.call_args.kwargs['params']['user'],CREDS['funder'])
        client.get_open_orders.assert_called_once_with(only_first_page=True)
        with patch.object(account,'build_client',side_effect=RuntimeError('private secret-test')),patch.object(account.requests,'get',side_effect=RuntimeError('secret')):
            result=account.account_snapshot()
        self.assertIsNone(result['orders']);self.assertIsNone(result['positions'])
        self.assertEqual(len(result['errors']),2);self.assertNotIn('secret',str(result))


class AccountEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE;self.config=dict(web.app.config)
        web.db.DB_FILE=os.path.join(self.tmp.name,'test.db');web.db.init_db();web.init_web_db()
        self.patch=patch.object(account.config,'BASE_DIR',self.tmp.name);self.patch.start()
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('long password for account'),
          SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False,TESTING=True)
        web._login_attempts.clear();self.client=web.app.test_client();self.base='http://127.0.0.1:8108'
        self.client.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
        with self.client.session_transaction(base_url=self.base) as sess:
            sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='test-token'
    def tearDown(self):
        self.patch.stop();web.db.DB_FILE=self.old_db;web.app.config.clear();web.app.config.update(self.config);self.tmp.cleanup()
    def test_security_and_secret_redaction(self):
        url='/api/trading/account-config'; data={**CREDS,'current_password':'long password for account'}
        self.assertEqual(self.client.post(url,base_url=self.base,json=data).status_code,403)
        self.assertEqual(self.client.post(url,base_url=self.base,json=data,headers={'X-CSRF-Token':'test-token','Origin':'http://evil.example'}).status_code,403)
        self.assertEqual(self.client.post(url,base_url=self.base,json=data,environ_overrides={'SERVER_NAME':'0.0.0.0'},headers={'X-CSRF-Token':'test-token'}).status_code,403)
        self.assertEqual(self.client.post(url,base_url=self.base,json={**data,'current_password':'bad'},headers={'X-CSRF-Token':'test-token'}).status_code,400)
        response=self.client.post(url,base_url=self.base,json=data,headers={'X-CSRF-Token':'test-token'})
        self.assertEqual(response.status_code,200);self.assertNotIn('secret-test',response.get_data(as_text=True))
        response=self.client.get(url,base_url=self.base)
        self.assertEqual(response.status_code,200);self.assertNotIn('secret-test',response.get_data(as_text=True));self.assertNotIn(CREDS['private_key'],response.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/trading/account',base_url=self.base,environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
        response=self.client.delete(url,base_url=self.base,json={'current_password':'long password for account'},headers={'X-CSRF-Token':'test-token'})
        self.assertEqual(response.status_code,200);self.assertFalse(account.public_config()['configured'])


if __name__=='__main__': unittest.main()
