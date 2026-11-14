import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from web import app as web

class WebCacheConcurrencyTests(unittest.TestCase):
    def setUp(self):
        with web._lock:web._cache.clear();web._cache_inflight.clear()
    def test_distinct_keys_do_not_wait_for_slow_loader(self):
        started=threading.Event();release=threading.Event()
        def slow():started.set();self.assertTrue(release.wait(3));return 'slow'
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(web.cached,('forecast','slow'),60,slow)
                self.assertTrue(started.wait(2))
                second=pool.submit(web.cached,('market','fast'),60,lambda:'fast')
                self.assertEqual(second.result(timeout=1),'fast')
                release.set();self.assertEqual(first.result(timeout=2),'slow')
        finally:release.set()
    def test_same_key_is_coalesced_and_cached(self):
        started=threading.Event();release=threading.Event();calls=[]
        def load():
            calls.append(1);started.set();self.assertTrue(release.wait(3));return 'ok'
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                a=pool.submit(web.cached,'same',60,load);self.assertTrue(started.wait(2))
                b=pool.submit(web.cached,'same',60,load);c=pool.submit(web.cached,'same',60,load)
                release.set();self.assertEqual([x.result(timeout=2) for x in (a,b,c)],['ok']*3)
            self.assertEqual(calls,[1]);self.assertEqual(web.cached('same',60,lambda:'wrong'),'ok')
        finally:release.set()
    def test_failure_is_cached_and_inflight_released(self):
        with patch.object(web.app.logger,'warning'):
            self.assertIsNone(web.cached('failure',60,lambda:1/0))
        self.assertIsNone(web.cached('failure',60,lambda:'not-retried'))
        self.assertNotIn('failure',web._cache_inflight)
