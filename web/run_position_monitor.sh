#!/bin/sh
# The repository stays the working directory; reuse the virtualenv installed for the web service.
set -eu
cd /opt/gradusbot-web
for python in /opt/gradusbot-web/.venv/bin/python /opt/gradusbot-web/venv/bin/python; do
    if [ -x "$python" ] && "$python" -c 'import requests, dotenv, py_clob_client_v2, eth_account' >/dev/null 2>&1; then
        exec "$python" -m web.position_monitor
    fi
done
echo 'Python-окружение с зависимостями веб-терминала не найдено в .venv или venv.' >&2
exit 1
