#!/bin/sh
# Public data and hypothetical ledger only. Does not submit real trades.
set -eu
cd /opt/gradusbot-web
for python in /opt/gradusbot-web/.venv/bin/python /opt/gradusbot-web/venv/bin/python; do
    if [ -x "$python" ] && "$python" -c 'import requests, dotenv, py_clob_client_v2, eth_account' >/dev/null 2>&1; then
        exec "$python" -m web.auto_scalp
    fi
done
echo 'Python-окружение веб-терминала не найдено в .venv или venv.' >&2
exit 1
