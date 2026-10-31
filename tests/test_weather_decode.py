import unittest
from web.weather import cloud_description, precipitation_description


class MetarDecodingTests(unittest.TestCase):
    def test_cloud_coverage_and_codes(self):
        cases = [
            ('EGLL 010000Z FEW020', None, ('Малооблачно', 'FEW')),
            ('EGLL 010000Z SCT020', None, ('Переменная облачность', 'SCT')),
            ('EGLL 010000Z FEW020 BKN040', None, ('Преимущественно облачно', 'BKN')),
            ('EGLL 010000Z SCT020 OVC040', None, ('Пасмурно', 'OVC')),
            ('EGLL 010000Z SKC', None, ('Ясно', 'SKC')),
            ('EGLL 010000Z CAVOK', None, ('Значимой облачности нет', 'CAVOK')),
            ('EGLL 010000Z', 'FEW', ('Малооблачно', 'FEW')),
            ('EGLL 010000Z RMK OVC040', None, (None, None)),
        ]
        for raw, cover, expected in cases:
            with self.subTest(raw=raw, cover=cover):
                value = cloud_description(raw, cover)
                self.assertEqual((value['description'], value['code']), expected)

    def test_precipitation_and_remarks(self):
        cases = [
            (None, None),
            ('EGLL 010000Z SCT040', 'Осадки не указаны'),
            ('EGLL 010000Z -RA SCT040', 'Слабые осадки: дождь'),
            ('EGLL 010000Z +SHRA BKN040', 'Сильные осадки: ливень'),
            ('EGLL 010000Z RASN BKN040', 'Дождь / снег'),
            ('EGLL 010000Z FZRA BKN040', 'Ледяной дождь'),
            ('EGLL 010000Z TSRA BKN040', 'Гроза, дождь'),
            ('EGLL 010000Z VCSH BKN040', 'Ливни поблизости'),
            ('EGLL 010000Z SCT040 RMK -RA', 'Осадки не указаны'),
            ('EGLL 010000Z RETSRA SCT040', 'Осадки не указаны'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(precipitation_description(raw)['description'], expected)


if __name__ == '__main__':
    unittest.main()
