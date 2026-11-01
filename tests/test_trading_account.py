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

    def test_balance_validation_and_v2_pagination(self):
        account.save(CREDS);client=Mock()
        with patch.object(account,'build_client',return_value=client):
            for bad in (None, '-1', '1.1', True, 'nan', '9'*41):
                client.get_balance_allowance.return_value={'balance':bad}
                with self.subTest(bad=bad),self.assertRaises(account.AccountError):account._balance(account.load())
            client.get_balance_allowance.return_value={'balance':'0'}
            self.assertEqual(account._balance(account.load()),'0.000000')
        response=Mock();response.json.return_value={'data':[],'pagination':{'has_more':True,'next_cursor':'abc'}}
        with patch.object(account.requests,'get',return_value=response):
            positions,partial=account._positions(account.load());self.assertEqual(positions,[]);self.assertTrue(partial)
            response.json.return_value={'data':[], 'pagination':{'has_more':True,'next_cursor':None}}
            with self.assertRaises(account.AccountError): account._positions(account.load())
            response.json.return_value=[]  # retired v1 response must not look like an empty v2 account
            with self.assertRaises(account.AccountError): account._positions(account.load())

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
        client=Mock();client.get_balance_allowance.return_value={'balance':'12345678'};client.get_open_orders.return_value=[{'id':'abc','asset_id':'123','side':'BUY','price':'0.4','original_size':'10','size_matched':'2','status':'LIVE','secret':'SECRET'}]
        response=Mock();response.json.return_value={'data':[{'token_id':'123','event_slug':'e','title':'Event','outcome':'Yes','current_size':3,'avg_price':0.4,'current_price':0.5,'unrealized_pnl':0.3,'private':'SECRET'}],'pagination':{'has_more':False,'next_cursor':None}}
        with patch.object(account,'build_client',return_value=client),patch.object(account.requests,'get',return_value=response) as get:
            result=account.account_snapshot()
        self.assertEqual(result['orders'][0]['size_matched'],2)
        self.assertEqual(result['positions'][0]['event_slug'],'e');self.assertEqual(result['collateral_balance'],'12.345678')
        client.update_balance_allowance.assert_called_once();client.get_balance_allowance.assert_called_once()
        self.assertNotIn('SECRET',str(result));self.assertNotIn('secret-test',str(result))
        self.assertEqual(get.call_args.kwargs['params']['user'],CREDS['funder']);self.assertEqual(get.call_args.args[0],account.DATA_API)
        self.assertNotIn('offset',get.call_args.kwargs['params'])
        client.get_open_orders.assert_called_once_with(only_first_page=True)
        with patch.object(account,'build_client',side_effect=RuntimeError('private secret-test')),patch.object(account.requests,'get',side_effect=RuntimeError('secret')):
            result=account.account_snapshot()
        self.assertIsNone(result['orders']);self.assertIsNone(result['positions'])
        self.assertEqual(len(result['errors']),3);self.assertIsNone(result['collateral_balance']);self.assertNotIn('secret',str(result))


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
    def test_valid_password_wallet_errors_do_not_lock_out(self):
        url='/api/trading/account-config';headers={'X-CSRF-Token':'test-token'}
        data={**CREDS,'current_password':'long password for account','funder':'bad-address'}
        for _ in range(8):
            response=self.client.post(url,base_url=self.base,json=data,headers=headers)
            self.assertEqual(response.status_code,400)
            self.assertIn('funder',response.get_json()['error'])
        good=self.client.post(url,base_url=self.base,json={**data,'funder':CREDS['funder']},headers=headers)
        self.assertEqual(good.status_code,200)
        self.assertNotIn('trading-config:127.0.0.1',web._login_attempts)

    def test_clob_failure_does_not_lock_out(self):
        url='/api/trading/account-config';headers={'X-CSRF-Token':'test-token'}
        data={**CREDS,'auto_api':True,'current_password':'long password for account'}
        with patch.object(account,'derive_api_credentials',side_effect=account.AccountError('CLOB недоступен')):
            for _ in range(8):
                response=self.client.post(url,base_url=self.base,json=data,headers=headers)
                self.assertEqual(response.status_code,400)
                self.assertIn('CLOB',response.get_json()['error'])
        self.assertNotIn('trading-config:127.0.0.1',web._login_attempts)

    def test_incorrect_password_still_rate_limited(self):
        url='/api/trading/account-config';headers={'X-CSRF-Token':'test-token'}
        data={**CREDS,'current_password':'wrong'}
        for _ in range(web.LOGIN_ATTEMPTS):
            self.assertEqual(self.client.post(url,base_url=self.base,json=data,headers=headers).status_code,400)
        blocked=self.client.post(url,base_url=self.base,json={**data,'current_password':'long password for account'},headers=headers)
        self.assertEqual(blocked.status_code,429)
        self.assertFalse(account.public_config()['configured'])

    def test_successful_password_resets_prior_failures_before_validation(self):
        url='/api/trading/account-config';headers={'X-CSRF-Token':'test-token'}
        data={**CREDS,'current_password':'wrong'}
        for _ in range(web.LOGIN_ATTEMPTS-1):
            self.assertEqual(self.client.post(url,base_url=self.base,json=data,headers=headers).status_code,400)
        self.assertEqual(self.client.post(url,base_url=self.base,json={**data,'current_password':'long password for account','funder':'bad'},headers=headers).status_code,400)
        self.assertEqual(self.client.post(url,base_url=self.base,json=data,headers=headers).status_code,400)
        self.assertEqual(len(web._login_attempts['trading-config:127.0.0.1']),1)

    def test_snapshot_balance_works_while_trading_is_off(self):
        account.save(CREDS);client=Mock();client.get_balance_allowance.return_value={'balance':'2500000'};client.get_open_orders.return_value=[]
        response=Mock();response.json.return_value={'data':[],'pagination':{'has_more':False,'next_cursor':None}}
        with patch.object(account,'build_client',return_value=client),patch.object(account.requests,'get',return_value=response):
            result=self.client.get('/api/trading/account',base_url=self.base)
        self.assertEqual(result.status_code,200);self.assertEqual(result.get_json()['collateral_balance'],'2.500000')
        self.assertEqual(result.get_json()['errors'],[]);client.post_order.assert_not_called()

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
