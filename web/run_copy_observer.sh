#!/bin/sh
# This service only observes public trades and order books; it never trades.
set -eu
cd /opt/gradusbot-web
for python in /opt/gradusbot-web/.venv/bin/python /opt/gradusbot-web/venv/bin/python; do
    if [ -x "$python" ] && "$python" -c 'import requests, dotenv, py_clob_client_v2, eth_account' >/dev/null 2>&1; then
        exec "$python" -m web.copy_observer
    fi
done
echo 'Python-окружение веб-терминала не найдено в .venv или venv.' >&2
exit 1
