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

class AviationSourceTests(unittest.TestCase):
    def test_real_manila_and_html_escaped_query(self):
        url='https://aviationweather.gov/api/data/metar?ids=RPLL&amp;format=raw&amp;date=2026-11-02T19:00:00Z&amp;hours=30'
        result=identify_station({'resolutionSource':url, 'markets':[]})
        self.assertEqual(result['status'],'found')
        self.assertEqual(result['candidate']['code'],'RPLL')
        self.assertEqual(result['candidate']['kind'],'Aviation Weather Center')
        self.assertIn('&format=raw',result['candidate']['url'])

    def test_real_source_market_description_and_conflicts(self):
        result=identify_station({'markets':[{'description':'Source: https://aviationweather.gov/api/data/metar?format=raw&ids=KLGA'}]})
        self.assertEqual(result['candidate']['code'],'KLGA')
        conflict=identify_station({'resolutionSource':'https://aviationweather.gov/api/data/metar?ids=KLGA,KJFK'})
        self.assertEqual(conflict['status'],'conflict')
        self.assertIsNone(conflict['candidate'])

    def test_reject_untrusted_urls_and_invalid_codes(self):
        urls=['https://aviationweather.gov.evil.example/api/data/metar?ids=RPLL',
              'https://evil.example/api/data/metar?ids=RPLL',
              'https://aviationweather.gov/api/data/taf?ids=RPLL',
              'https://aviationweather.gov/api/data/metar?ids=RPLL1',
              'https://user@aviationweather.gov/api/data/metar?ids=RPLL']
        for url in urls:
            with self.subTest(url=url): self.assertEqual(identify_station({'resolutionSource':url})['status'],'unknown')


if __name__ == '__main__':
    unittest.main()
