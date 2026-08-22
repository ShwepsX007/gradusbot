import logging

log = logging.getLogger("bot")

user_state = {}


def us(cid):
    if cid not in user_state:
        user_state[cid] = {}
    return user_state[cid]


def icon(s):
    return "✈️" if s.get("station_type") == "metar" else "📡"