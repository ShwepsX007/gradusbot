#!/bin/sh
# REAL SELL orders, but only for explicitly configured BUY plans.
set -eu
cd /opt/gradusbot-web
for python in /opt/gradusbot-web/.venv/bin/python /opt/gradusbot-web/venv/bin/python; do
    if [ -x "$python" ] && "$python" -c 'import requests, dotenv, py_clob_client_v2, eth_account' >/dev/null 2>&1; then
        exec "$python" -m web.auto_sell_service
    fi
done
echo 'Python-окружение веб-терминала не найдено в .venv или venv.' >&2
exit 1
