import threading
import time
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from web import wu_forecast as wu


class WUForecastTests(unittest.TestCase):
    def setUp(self):
        with wu._LOCK:
            wu._CACHE.clear()
            wu._BUSY = False

    def test_units_and_separate_precipitation(self):
        rows = [
            {'time': '12:00 am', 'temperature': '76 °F', 'precipitation': '80%', 'condition': 'Rain'},
            {'time': '1:00 am', 'temperature': '20°C', 'precipitation': '0%'},
            {'time': '2:00 am', 'temperature': '30%', 'precipitation': '99%'},
            {'time': '3:00 am', 'temperature': '75', 'precipitation': '100%'},
        ]
        points = wu.parse_rows(rows, date(2026, 6, 1), 'America/New_York')
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0]['temp'], (76-32)*5/9, places=4)
        self.assertEqual(points[0]['precipitation'], 80)
        self.assertEqual(points[1]['temp'], 20)
        self.assertEqual(points[0]['time'], int(datetime(2026, 6, 1, 4, tzinfo=timezone.utc).timestamp()))

    def test_dst_repeated_and_nonexistent_hours_are_rejected(self):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('America/New_York')
        self.assertIsNone(wu._instant(date(2026, 11, 1), '1:00 am', zone))
        self.assertIsNone(wu._instant(date(2026, 3, 8), '2:00 am', zone))
        self.assertIsNotNone(wu._instant(date(2026, 3, 8), '3:00 am', zone))

    def test_coverage_requires_all_remaining_hours(self):
        day = date(2026, 6, 1)
        stamp = lambda hour: int(datetime(2026, 6, 1, hour, tzinfo=timezone.utc).timestamp())
        now = stamp(12) + 10
        full = [{'time': stamp(h), 'temp': 10} for h in range(13, 24)]
        self.assertTrue(wu.remaining_complete(full, day, 'UTC', now))
        self.assertFalse(wu.remaining_complete(full[:-1], day, 'UTC', now))
        self.assertFalse(wu.remaining_complete(full[:3]+full[4:], day, 'UTC', now))
        self.assertFalse(wu.remaining_complete(full, date(2026, 6, 2), 'UTC', now))

    def test_invalid_inputs_and_opt_out_never_start_browser(self):
        with patch.object(wu, '_browser_rows') as browser:
            self.assertEqual(wu.get('../bad', 'UTC')['status'], 'unsupported')
            self.assertEqual(wu.get('KMIA', None)['status'], 'no_timezone')
            with patch.dict('os.environ', {'GRADUS_WU_ENABLED': '0'}):
                self.assertEqual(wu.get('KMIA', 'UTC')['status'], 'disabled')
            browser.assert_not_called()

    def test_station_redirect_and_date_mismatch_are_rejected(self):
        page = {'url': 'https://www.wunderground.com/hourly/us/fl/miami/KMIA',
                'station': 'https://www.wunderground.com/history/daily/us/fl/miami/KMIA',
                'text': 'Miami Intl Airport Station\nWed, Nov 18, 2026\nHourly Forecast'}
        wu._validate_page(page, 'KMIA', date(2026, 11, 18))
        for changed in ({'url': 'https://www.wunderground.com/hourly/KOPF'},
                        {'station': 'https://www.wunderground.com/history/daily/KOPF'},
                        {'text': 'Tue, Nov 17, 2026'},
                        {'url': 'https://evil.example/hourly/KMIA'},
                        {'text': 'Access denied'}):
            with self.assertRaises(ValueError):
                wu._validate_page({**page, **changed}, 'KMIA', date(2026, 11, 18))

    def test_failure_is_cached_and_one_worker_serves_all_stations(self):
        started = threading.Event()
        release = threading.Event()
        def fail(*args):
            started.set()
            release.wait(2)
            raise RuntimeError('blocked')
        with patch.object(wu, '_browser_rows', side_effect=fail) as browser:
            self.assertEqual(wu.get('KMIA', 'UTC')['status'], 'loading')
            self.assertTrue(started.wait(2))
            self.assertEqual(wu.get('EGLL', 'UTC')['status'], 'loading')
            self.assertEqual(wu.get('KMIA', 'UTC')['status'], 'loading')
            release.set()
            for _ in range(100):
                with wu._LOCK:
                    done = not wu._BUSY
                if done:
                    break
                time.sleep(.01)
            self.assertEqual(wu.get('KMIA', 'UTC')['status'], 'unavailable')
            self.assertEqual(browser.call_count, 1)


if __name__ == '__main__':
    unittest.main()
