import os
import tempfile
import time
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from web import app as web, position_monitor as monitor, trading_account as account

KEY={'private_key':'0x'+'0'*63+'1','api_key':'key','api_secret':'secret','api_passphrase':'pass','signature_type':'0','funder':''}

class PositionMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=web.db.DB_FILE
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db();monitor.init_db()
        self.patch=patch.object(account.config,'BASE_DIR',self.tmp.name);self.patch.start();account.save(KEY)
        self.c=account.load()
        self.snapshot={'wallet':self.c['signer'],'collateral_balance':'1.250000','positions':[{'asset':'123','size':4}],'orders':[],'errors':[]}
    def tearDown(self):
        self.patch.stop();web.db.DB_FILE=self.old_db;self.tmp.cleanup()
    def test_success_partial_error_and_staleness(self):
        self.assertEqual(monitor.read_status(self.c)['status'],'not_running')
        with patch.object(account,'account_snapshot',return_value=self.snapshot): monitor.poll_once()
        result=monitor.read_status(self.c);self.assertEqual(result['status'],'ok');self.assertEqual(result['snapshot']['positions'][0]['size'],4)
        self.assertNotIn(KEY['private_key'],str(result));self.assertNotIn('secret',str(result))
        with patch.object(monitor.time,'time',return_value=result['checked_at']+91):
            stale=monitor.read_status(self.c);self.assertEqual(stale['status'],'stale');self.assertIsNone(stale['snapshot'])
        with patch.object(monitor.time,'time',return_value=result['checked_at']-11):
            self.assertEqual(monitor.read_status(self.c)['status'],'stale')
        with patch.object(account,'account_snapshot',return_value={**self.snapshot,'positions':None,'errors':['Не удалось получить позиции из Data API.']}): monitor.poll_once()
        result=monitor.read_status(self.c);self.assertEqual(result['status'],'partial');self.assertIsNone(result['snapshot']['positions'])
        with patch.object(account,'account_snapshot',side_effect=RuntimeError('private secret')): monitor.poll_once()
        result=monitor.read_status(self.c);self.assertEqual(result['status'],'error');self.assertIsNone(result['snapshot']);self.assertNotIn('secret',str(result))
    def test_snapshot_not_returned_after_key_change_or_account_switch(self):
        with patch.object(account,'account_snapshot',return_value=self.snapshot): monitor.poll_once()
        different={**self.c,'api_key':'another'}
        result=monitor.read_status(different);self.assertEqual(result['status'],'account_changed');self.assertIsNone(result['snapshot'])
        self.assertIsNone(monitor.read_status(None)['snapshot'])
        # A credential replacement during network IO invalidates the in-flight result.
        with patch.object(account,'load',side_effect=[self.c,different]),patch.object(account,'account_snapshot',return_value=self.snapshot): monitor.poll_once()
        self.assertEqual(monitor.read_status(different)['status'],'account_changed')
        self.assertIsNone(monitor.read_status(different)['snapshot'])
    def test_order_reconciliation_runs_independently_and_not_for_replaced_credentials(self):
        from web import order_reconciliation
        with patch.object(order_reconciliation,'reconcile_wallet') as rec, patch.object(account,'account_snapshot',side_effect=RuntimeError('offline')):
            monitor.poll_once();rec.assert_called_once_with(self.c)
        other={**self.c,'api_key':'new'}
        with patch.object(order_reconciliation,'reconcile_wallet') as rec, patch.object(account,'load',side_effect=[self.c,other,other]), patch.object(account,'account_snapshot',return_value=self.snapshot):
            monitor.poll_once();rec.assert_not_called()

    def test_singleton_lock_and_symlink(self):
        fd=monitor.acquire_lock()
        try:
            with self.assertRaises(OSError): monitor.acquire_lock()
        finally:os.close(fd)
        path=os.path.join(self.tmp.name,'.gradus-private','position-monitor.lock');os.unlink(path);os.symlink('/etc/passwd',path)
        with self.assertRaises(OSError):monitor.acquire_lock()
    def test_monitor_endpoint_requires_ssh(self):
        config=dict(web.app.config)
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH=generate_password_hash('password long enough'),SECRET_KEY='a'*48,SESSION_COOKIE_SECURE=False,WEB_TUNNEL_ONLY=True,WEB_TRUST_PROXY=False,TESTING=True)
            client=web.app.test_client();base='http://127.0.0.1:8108'
            client.environ_base.update({'SERVER_NAME':'127.0.0.1','SERVER_PORT':'8108','REMOTE_ADDR':'127.0.0.1','gunicorn.socket':object()})
            with client.session_transaction(base_url=base) as sess:sess['auth_user']='test';sess['auth_version']=web.auth_version();sess['csrf']='token'
            with patch.object(account,'account_snapshot',return_value=self.snapshot):monitor.poll_once()
            self.assertEqual(client.get('/api/trading/monitor',base_url=base).get_json()['status'],'ok')
            self.assertEqual(client.get('/api/trading/monitor',base_url=base,environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
        finally:web.app.config.clear();web.app.config.update(config)

if __name__=='__main__':unittest.main()
