"""Human-readable descriptions from the latest METAR, not a weather forecast."""
import re

CLOUDS = {
    'SKC': ('Ясно', 0), 'CLR': ('Низкая облачность не обнаружена', 0),
    'NSC': ('Значимой облачности нет', 0), 'NCD': ('Облака не обнаружены', 0),
    'CAVOK': ('Значимой облачности нет', 0),
    'FEW': ('Малооблачно', 1), 'SCT': ('Переменная облачность', 2),
    'BKN': ('Преимущественно облачно', 3), 'OVC': ('Пасмурно', 4),
    'VV': ('Небо закрыто', 5),
}
PRECIP_NAMES = {'RA': 'дождь', 'DZ': 'морось', 'SN': 'снег', 'SG': 'снежные зёрна',
                'IC': 'ледяные кристаллы', 'PL': 'ледяная крупа', 'GR': 'град',
                'GS': 'снежная крупа', 'UP': 'неизвестные осадки'}
WEATHER_GROUP = re.compile(r'^(?:[-+]|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP)+$')


def cloud_description(raw, cover=None):
    """Decode reported sky coverage, choosing the most extensive cloud layer."""
    tokens = str(raw or '').upper().split(' RMK', 1)[0].split()
    codes = []
    for token in [str(cover or '').upper(), *tokens]:
        if token in CLOUDS:
            codes.append(token)
        else:
            match = re.fullmatch(r'(FEW|SCT|BKN|OVC|VV)(?:\d{3}|///)(?:CB|TCU)?', token)
            if match:
                codes.append(match.group(1))
    if not codes:
        return {'description': None, 'code': None}
    code = max(codes, key=lambda c: CLOUDS[c][1])
    return {'description': CLOUDS[code][0], 'code': code}


def precipitation_description(raw):
    """Describe weather groups in the report body, ignoring remarks and recent weather."""
    if not raw or not str(raw).strip():
        return {'description': None, 'codes': []}
    tokens = str(raw).upper().split(' RMK', 1)[0].split()
    entries = []
    codes = []
    for token in tokens:
        if token == 'VCSH':
            entries.append('ливни поблизости')
            codes.append(token)
            continue
        if token in ('TS', 'VCTS'):
            entries.append('гроза' if token == 'TS' else 'гроза поблизости')
            codes.append(token)
            continue
        if not WEATHER_GROUP.fullmatch(token):
            continue
        types = [PRECIP_NAMES[code] for code in re.findall(r'DZ|RA|SN|SG|IC|PL|GR|GS|UP', token)]
        if not types:
            continue
        qualifier = 'слабые осадки: ' if token.startswith('-') else 'сильные осадки: ' if token.startswith('+') else ''
        if token.startswith('VC'):
            qualifier += 'поблизости: '
        if 'FZ' in token:
            types = ['ледяной дождь' if t == 'дождь' else 'переохлаждённая морось' if t == 'морось' else t for t in types]
        if 'SH' in token:
            types = ['ливень' if t == 'дождь' else 'ливневый снег' if t == 'снег' else t for t in types]
        if 'TS' in token:
            qualifier += 'гроза, '
        entries.append(qualifier + ' / '.join(types))
        codes.append(token)
    return {'description': '; '.join(entries).capitalize() if entries else 'Осадки не указаны', 'codes': codes}
