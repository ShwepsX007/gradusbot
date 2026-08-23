#!/usr/bin/env bash
# Поднимает окружение бота на Python 3.11+.
#
# Официальный SDK Polymarket (polymarket-client) требует Python >= 3.11.
# Скрипт находит подходящий интерпретатор (при необходимости ставит его),
# создаёт новое виртуальное окружение venv311, ставит зависимости
# и печатает строку для systemd-юнита.
#
# Запуск из папки бота:
#     bash setup_python311.sh

set -u

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$BOT_DIR/venv311"

echo "=============================================="
echo " Окружение Python 3.11+ для gradusbot"
echo " Папка: $BOT_DIR"
echo "=============================================="

current="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "нет")"
echo "Системный python3: $current"

# 1. Ищем готовый интерпретатор 3.11+
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$PY" ]; then
    ok="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)"
    if [ "$ok" = "1" ]; then
        PY="$(command -v python3)"
    fi
fi

# 2. Не нашли — ставим
if [ -z "$PY" ]; then
    echo
    echo "Python 3.11+ не найден, пробую установить..."

    if [ "$(id -u)" != "0" ]; then
        echo "❌ Нужны права root. Запустите: sudo bash setup_python311.sh"
        exit 1
    fi

    if command -v apt-get >/dev/null 2>&1; then
        . /etc/os-release 2>/dev/null || true
        echo "Дистрибутив: ${PRETTY_NAME:-неизвестен}"

        apt-get update -qq
        if apt-get install -y python3.11 python3.11-venv python3.11-dev >/dev/null 2>&1; then
            PY="$(command -v python3.11)"
        else
            echo "В основном репозитории python3.11 нет, подключаю deadsnakes..."
            apt-get install -y software-properties-common >/dev/null 2>&1
            add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1
            apt-get update -qq
            apt-get install -y python3.11 python3.11-venv python3.11-dev >/dev/null 2>&1
            PY="$(command -v python3.11 || true)"
        fi
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3.11 python3.11-devel >/dev/null 2>&1
        PY="$(command -v python3.11 || true)"
    fi
fi

if [ -z "$PY" ]; then
    echo
    echo "❌ Установить Python 3.11 автоматически не вышло."
    echo "   Поставьте его вручную, например через pyenv:"
    echo "     curl https://pyenv.run | bash"
    echo "     pyenv install 3.11.9 && pyenv local 3.11.9"
    echo "   Затем запустите скрипт ещё раз."
    exit 1
fi

echo
echo "✅ Интерпретатор: $PY ($($PY -V 2>&1))"

# 3. Создаём окружение
if [ -d "$VENV_DIR" ]; then
    echo "Старый $VENV_DIR удаляю..."
    rm -rf "$VENV_DIR"
fi

echo "Создаю $VENV_DIR ..."
"$PY" -m venv "$VENV_DIR" || {
    echo "❌ venv не создался. Поставьте пакет python3.11-venv и повторите."
    exit 1
}

"$VENV_DIR/bin/pip" install --upgrade pip -q

echo "Ставлю зависимости из requirements.txt ..."
if ! "$VENV_DIR/bin/pip" install -r "$BOT_DIR/requirements.txt"; then
    echo "❌ Не все зависимости встали, смотрите вывод выше."
    exit 1
fi

echo
echo "=============================================="
echo " Проверка"
echo "=============================================="
"$VENV_DIR/bin/python" - <<'PYCODE'
import sys
print(f"  Python: {sys.version.split()[0]}")
try:
    import polymarket
    print("  polymarket-client: ✅ установлен")
except Exception as e:
    print(f"  polymarket-client: ❌ {e}")
try:
    import telegram
    print("  python-telegram-bot: ✅ установлен")
except Exception as e:
    print(f"  python-telegram-bot: ❌ {e}")
PYCODE

UNIT="$(systemctl list-units --type=service --all --no-legend 2>/dev/null \
        | grep -iE 'gradus|bot' | awk '{print $1}' | head -1)"

echo
echo "=============================================="
echo " Что дальше"
echo "=============================================="
echo "1. Проверьте кошелёк:"
echo "     $VENV_DIR/bin/python $BOT_DIR/poly_wallet_check.py"
echo
echo "2. Переведите сервис на новое окружение — в юните замените ExecStart на:"
echo "     ExecStart=$VENV_DIR/bin/python $BOT_DIR/main.py"
if [ -n "$UNIT" ]; then
    echo "   Похоже, ваш юнит: $UNIT"
    echo "     systemctl edit --full $UNIT"
    echo "     systemctl daemon-reload && systemctl restart $UNIT"
fi
echo
echo "3. Старый venv можно оставить как есть — он больше не используется."
