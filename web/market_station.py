"""Extract explicit station evidence from Gamma event resolution rules.

Never guess a station from a city or arbitrary four-letter text: airport choice
can change a market's result. Returned candidates still require human review.
"""
import html
import re
from urllib.parse import urlparse, unquote

import utils

URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
ICAO_RE = re.compile(r'\b(?i:ICAO)(?:\s+(?i:code|station))?\s*[:=]?\s*([A-Z][A-Z0-9]{3})\b')
SITE_RE = re.compile(r'\b(?i:site)=([A-Z][A-Z0-9]{3})\b')


def resolution_fields(event):
    """Read rule and resolution fields, not market titles or unrelated links."""
    if not isinstance(event, dict):
        return []
    fields = []
    for key in ('resolutionSource', 'description'):
        if isinstance(event.get(key), str):
            fields.append(('event.' + key, event[key]))
    markets = event.get('markets')
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            for key in ('resolutionSource', 'description'):
                if isinstance(market.get(key), str):
                    fields.append(('market.' + key, market[key]))
    return fields


def wu_evidence(text):
    for match in URL_RE.finditer(html.unescape(text)):
        url = match.group(0).rstrip('.,);]')
        try:
            parsed = urlparse(url)
            if parsed.scheme.lower() not in ('https', 'http') or parsed.hostname not in ('wunderground.com', 'www.wunderground.com') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
                continue
            path = unquote(parsed.path)
            if not (path.lower().startswith('/history/daily/') or path.lower().startswith('/weather/')):
                continue
            # WU can append /date/YYYY-MM-DD to the station URL.
            path = re.split(r'/date(?:/|$)', path, maxsplit=1, flags=re.I)[0].rstrip('/')
            candidate = path.rsplit('/', 1)[-1]
            # Use the bot's existing extraction helper, but require a four-character
            # uppercase ICAO-shaped final component; never treat a city as a station.
            code = utils.extract_code_from_wu('https://www.wunderground.com' + path)
            if code and code == candidate and re.fullmatch(r'[A-Z][A-Z0-9]{3}', code):
                yield code, url
        except ValueError:
            continue


def identify_station(event):
    found = {}
    for field, text in resolution_fields(event):
        evidence = [(code, 'Weather Underground', url) for code, url in wu_evidence(text)]
        evidence += [(m.group(1), 'ICAO в правилах', None) for m in ICAO_RE.finditer(text)]
        evidence += [(m.group(1), 'site в правилах', None) for m in SITE_RE.finditer(text)]
        for code, kind, url in evidence:
            # Prefer a clickable, identifiable resolution source over a textual hint.
            if code not in found or (url and not found[code]['url']):
                found[code] = {'code': code, 'kind': kind, 'url': url, 'field': field}
    candidates = sorted(found.values(), key=lambda item: item['code'])
    status = 'found' if len(candidates) == 1 else 'conflict' if candidates else 'unknown'
    return {'status': status, 'candidates': candidates,
            'candidate': candidates[0] if status == 'found' else None}
