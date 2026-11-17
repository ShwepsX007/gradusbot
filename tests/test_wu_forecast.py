import json
import os
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import config
from web import wu_config as wc, wu_forecast as wu

K1 = 'wu-test-credential-one-123'
K2 = 'wu-test-credential-two-456'


class Response:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self.data = data
        self.headers = headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield json.dumps(self.data).encode()


class WUForecastTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = config.BASE_DIR
        config.BASE_DIR = self.temp.name
        with wu._LOCK:
            wu._CACHE.clear()
            wu._BUSY = False
            wu._BLOCKED_UNTIL = 0
            wu._NEXT_REQUEST = 0
            wu._GENERATION = 0
    def tearDown(self):
        config.BASE_DIR = self.base
        self.temp.cleanup()
    def add(self, key=K1):
        return wc.update({'action':'add_key', 'key':key, 'authorized':True})

    def test_secrets_never_public_permissions_and_delete(self):
        with self.assertRaises(wc.ConfigError):
            wc.update({'action':'add_key', 'key':K1})
        public = self.add()
        self.assertNotIn(K1, json.dumps(public))
        self.assertEqual(wc.private_keys(), [K1])
        path = os.path.join(self.temp.name, '.gradus-private', 'wu.json')
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with self.assertRaises(wc.ConfigError): self.add(K1)
        self.add(K2)
        with self.assertRaises(wc.ConfigError): self.add('third-test-key-123')
        wc.update({'action':'delete_key', 'id':public['keys'][0]['id']})
        self.assertEqual(wc.private_keys(), [K2])
        os.chmod(path, 0o644)
        with self.assertRaises(wc.ConfigError): wc.public_config()

    def test_api_parsing_utc_and_precip_not_temperature_probability(self):
        now = int(time.time())
        data = {'validTimeUtc':[now+3600, now+7200], 'temperature':[20, 21], 'precipChance':[80, 15], 'wxPhraseLong':['Rain','Clear']}
        points = wu.parse_api(data, now)
        self.assertEqual(points[0], {'time':now+3600,'temp':20,'precipitation':80,'condition':'Rain'})
        self.assertEqual(wu.parse_api({**data, 'temperature':[1]},now), [])
        self.assertEqual(wu.parse_api({**data, 'validTimeUtc':[now+3600]*2},now), [])
        self.assertEqual(wu.parse_api({**data, 'temperature':[True, float('nan')]},now), [])

    def test_coverage_for_local_day_and_dst(self):
        day = date(2026, 6, 1)
        stamp = lambda hour: int(datetime(2026, 6, 1, hour, tzinfo=timezone.utc).timestamp())
        now = stamp(12) + 10
        full = [{'time': stamp(h), 'temp': 10} for h in range(13, 24)]
        self.assertTrue(wu.remaining_complete(full, day, 'UTC', now))
        self.assertFalse(wu.remaining_complete(full[:-1], day, 'UTC', now))
        self.assertFalse(wu.remaining_complete(full[:3]+full[4:], day, 'UTC', now))
        # New York fall-back day has two distinct 01:00 UTC instants; no ambiguity.
        start = int(datetime(2026,11,1,4,tzinfo=timezone.utc).timestamp())
        points = [{'time': start+h*3600, 'temp': 10} for h in range(25)]
        self.assertTrue(wu.remaining_complete(points, date(2026,11,1), 'America/New_York', start))
        self.assertFalse(wu.remaining_complete(points[:2]+points[3:], date(2026,11,1), 'America/New_York', start))

    def test_http_rejection_tries_second_but_429_does_not(self):
        now = int(time.time())
        data = {'validTimeUtc':[now+3600], 'temperature':[12]}
        with patch.object(wu.requests,'get',side_effect=[Response(403),Response(200,data)]) as get:
            status, points, _ = wu._fetch('EFHK',[K1,K2])
        self.assertEqual(status,'ok');self.assertEqual(points[0]['temp'],12)
        self.assertEqual(get.call_count,2)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with patch.object(wu.requests,'get',return_value=Response(429,headers={'Retry-After':'3600'})) as get:
            status, points, until = wu._fetch('EFHK',[K1,K2])
        self.assertEqual(status,'throttled');self.assertEqual(get.call_count,1)
        self.assertGreater(until,time.time()+3500)
        with patch.object(wu.requests,'get',side_effect=RuntimeError('secret '+K1)) as get:
            self.assertEqual(wu._fetch('EFHK',[K1,K2])[0],'unavailable')
            self.assertEqual(get.call_count,1)

    def test_invalid_no_key_and_disabled_no_network(self):
        with patch.object(wu.requests,'get') as request:
            self.assertEqual(wu.get('../bad','UTC')['status'],'unsupported')
            self.assertEqual(wu.get('EFHK',None)['status'],'no_timezone')
            self.assertEqual(wu.get('EFHK','UTC')['status'],'no_key')
            with patch.dict('os.environ',{'GRADUS_WU_ENABLED':'0'}):
                self.assertEqual(wu.get('EFHK','UTC')['status'],'disabled')
            request.assert_not_called()

    def test_one_worker_and_invalidated_inflight_result(self):
        self.add(); started = threading.Event(); release = threading.Event()
        def fetch(*args):
            started.set();release.wait(2)
            return 'access_denied',[],0
        with patch.object(wu,'_fetch',side_effect=fetch) as mock:
            self.assertEqual(wu.get('EFHK','UTC')['status'],'loading')
            self.assertTrue(started.wait(2))
            self.assertEqual(wu.get('KMIA','UTC')['status'],'loading')
            wu.invalidate();release.set()
            for _ in range(100):
                with wu._LOCK: done = not wu._BUSY
                if done: break
                time.sleep(.01)
            with wu._LOCK: self.assertEqual(wu._CACHE,{})
            self.assertEqual(mock.call_count,1)


if __name__ == '__main__': unittest.main()
