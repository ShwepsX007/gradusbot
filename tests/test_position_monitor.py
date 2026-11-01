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

    def test_legacy_monitor_table_is_migrated_without_losing_snapshot(self):
        with web.db.get_db() as conn:
            conn.execute('DROP TABLE web_account_monitor')
            conn.execute("CREATE TABLE web_account_monitor(id INTEGER PRIMARY KEY, checked_at REAL NOT NULL, status TEXT NOT NULL, wallet TEXT, fingerprint TEXT, payload TEXT)")
            conn.execute("INSERT INTO web_account_monitor(id,checked_at,status) VALUES(1,1,'error')")
        monitor.init_db();monitor.init_db()
        with web.db.get_db() as conn:
            row=conn.execute('SELECT * FROM web_account_monitor WHERE id=1').fetchone()
            self.assertEqual(row['status'],'error');self.assertIsNone(row['interval_seconds'])
        self.assertEqual(monitor.get_interval(),30)

    def test_interval_validation_and_snapshot_freshness_follow_saved_interval(self):
        self.assertEqual(monitor.get_interval(),30)
        for value in (True,14,301,30.0,'30',None):
            with self.assertRaises(ValueError):monitor.set_interval(value)
        with patch.object(account,'account_snapshot',return_value=self.snapshot):monitor.poll_once()
        previous=monitor.read_status(self.c)['checked_at']
        monitor.set_interval(300)
        # Increasing an interval must never revive a snapshot collected at 30 s.
        with patch.object(monitor.time,'time',return_value=previous+100):self.assertEqual(monitor.read_status(self.c)['status'],'stale')
        with patch.object(account,'account_snapshot',return_value=self.snapshot):monitor.poll_once()
        updated=monitor.read_status(self.c)['checked_at']
        with patch.object(monitor.time,'time',return_value=updated+100):self.assertEqual(monitor.read_status(self.c)['status'],'ok')
        monitor.set_interval(15)
        with patch.object(monitor.time,'time',return_value=updated+100):self.assertEqual(monitor.read_status(self.c)['status'],'stale')

    def test_wait_updates_interval_and_avoids_busy_loop(self):
        class Clock:
            value=0
            def monotonic(self):return self.value
            def wait(self,n):self.value+=n;monitor.set_interval(15)
            def is_set(self):return False
        clock=Clock();monitor.set_interval(300)
        with patch.object(monitor.time,'monotonic',side_effect=clock.monotonic):monitor.wait_until_next(clock,0)
        self.assertEqual(clock.value,15)
        clock.value=100
        with patch.object(monitor.time,'monotonic',side_effect=clock.monotonic):monitor.wait_until_next(clock,0)
        self.assertEqual(clock.value,101)

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
            url='/api/trading/monitor-interval'
            self.assertEqual(client.get(url,base_url=base).get_json()['interval_seconds'],30)
            self.assertEqual(client.get(url,base_url=base,environ_overrides={'SERVER_NAME':'0.0.0.0'}).status_code,403)
            self.assertEqual(client.post(url,base_url=base,json={'interval_seconds':45,'confirm':True}).status_code,403)
            for value in [True,14,301,'30',30.0,None]:
                result=client.post(url,base_url=base,json={'interval_seconds':value,'confirm':True},headers={'X-CSRF-Token':'token'})
                self.assertEqual(result.status_code,400)
            result=client.post(url,base_url=base,json={'interval_seconds':45,'confirm':True},headers={'X-CSRF-Token':'token'})
            self.assertEqual(result.get_json()['interval_seconds'],45)
            self.assertEqual(client.get(url,base_url=base).get_json()['interval_seconds'],45)
            self.assertEqual(client.get('/api/trading/monitor',base_url=base).get_json()['interval_seconds'],45)

        finally:web.app.config.clear();web.app.config.update(config)

if __name__=='__main__':unittest.main()
