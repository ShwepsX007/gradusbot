import unittest
from web.market_station import identify_station


class ResolutionParsingTests(unittest.TestCase):
    def test_resolution_source_link_and_date_suffix(self):
        url = 'https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-11-1'
        data = identify_station({'resolutionSource': url, 'description': f'Information at {url}.', 'markets': []})
        self.assertEqual(data['status'], 'found')
        self.assertEqual(data['candidate']['code'], 'KLGA')
        self.assertEqual(data['candidate']['url'], url)

    def test_market_level_description_and_plain_icao(self):
        event = {'markets': [{'description': 'Resolution source: ICAO code EGLC'}, {'description': 'ICAO: EGLC'}]}
        result = identify_station(event)
        self.assertEqual(result['status'], 'found')
        self.assertEqual(result['candidate']['code'], 'EGLC')
        self.assertEqual(result['candidate']['field'], 'market.description')

    def test_conflicting_stations_are_never_silently_selected(self):
        result = identify_station({'resolutionSource': 'https://www.wunderground.com/history/daily/gb/london/EGLC',
                                   'markets': [{'description':'ICAO: EGLL'}]})
        self.assertEqual(result['status'], 'conflict')
        self.assertIsNone(result['candidate'])
        self.assertEqual({c['code'] for c in result['candidates']}, {'EGLC', 'EGLL'})

    def test_no_guess_by_city_titles_or_unrelated_links(self):
        event = {'title': 'Highest temperature in London EGLL', 'description': 'Temperature in London will be measured.'}
        self.assertEqual(identify_station(event)['status'], 'unknown')
        for url in ('https://wunderground.com.attacker.test/history/daily/us/ny/new-york/KLGA',
                    'https://www.wunderground.com/history/daily/us/ny/new-york-city',
                    'https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA1',
                    'https://example.com/history/daily/us/ny/KLGA'):
            with self.subTest(url=url):
                self.assertEqual(identify_station({'resolutionSource': url})['status'], 'unknown')

    def test_explicit_query_and_non_dictionary_items(self):
        result = identify_station({'markets': [None, {'resolutionSource':'https://example.test/?site=KSEA'}]})
        self.assertEqual(result['candidate']['code'], 'KSEA')
        self.assertEqual(identify_station(None)['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
