#!/usr/bin/env bash
#
# Разворачивает структуру бота в один плоский каталог.
#
#   БЫЛО:  /root/bot_private/               (часть файлов)
#          /root/bot_private/bot/           (часть файлов)
#          /root/bot_private/bot/handlers/  (остальное)
#
#   СТАЛО: /root/bot_private/               (все .py рядом, как в репозитории)
#
# Использование:
#   bash flatten_structure.sh                 # ПРОБНЫЙ прогон, ничего не меняет
#   bash flatten_structure.sh --apply         # выполнить перенос
#   bash flatten_structure.sh --apply /path   # если каталог не /root/bot_private
#
set -euo pipefail

APPLY=0
ROOT="/root/bot_private"

for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=1 ;;
        -*) echo "Неизвестный ключ: $arg"; exit 1 ;;
        *) ROOT="$arg" ;;
    esac
done

BOT="$ROOT/bot"
HANDLERS="$BOT/handlers"

if [ ! -d "$ROOT" ]; then
    echo "❌ Каталог $ROOT не найден"
    exit 1
fi

if [ "$APPLY" -eq 0 ]; then
    echo "🔎 ПРОБНЫЙ ПРОГОН (ничего не изменяется). Для реального переноса добавьте --apply"
fi
echo "📁 Корень: $ROOT"
echo

# ---------------------------------------------------------------- бэкап
if [ "$APPLY" -eq 1 ]; then
    STAMP="$(date +%Y%m%d_%H%M%S)"
    BACKUP="${ROOT%/}_backup_$STAMP.tar.gz"
    echo "💾 Бэкап: $BACKUP"
    tar -czf "$BACKUP" -C "$(dirname "$ROOT")" "$(basename "$ROOT")"
    echo
fi

WARN=0

move_one() {
    local src="$1" dst="$2"
    local name
    name="$(basename "$src")"

    if [ -e "$dst" ]; then
        if cmp -s "$src" "$dst"; then
            echo "  = $name — уже есть в корне, идентичен → удаляю дубль"
            if [ "$APPLY" -eq 1 ]; then rm -f "$src"; fi
            return
        fi
        echo "  ⚠ $name — УЖЕ ЕСТЬ в корне и ОТЛИЧАЕТСЯ. Кладу рядом как $name.conflict"
        echo "     сравните: diff $dst $dst.conflict"
        WARN=1
        if [ "$APPLY" -eq 1 ]; then mv "$src" "$dst.conflict"; fi
        return
    fi

    echo "  → $name"
    if [ "$APPLY" -eq 1 ]; then mv "$src" "$dst"; fi
}

# ------------------------------------------------- bot/handlers/*  →  корень
if [ -d "$HANDLERS" ]; then
    echo "📦 Переношу bot/handlers/ → корень:"
    shopt -s nullglob
    for f in "$HANDLERS"/*; do
        [ -d "$f" ] && continue
        name="$(basename "$f")"
        case "$name" in
            __init__.py)
                # это диспетчер on_callback — в плоской схеме он становится handlers.py
                echo "  → __init__.py переименован в handlers.py"
                if [ "$APPLY" -eq 1 ]; then
                    if [ -e "$ROOT/handlers.py" ] && ! cmp -s "$f" "$ROOT/handlers.py"; then
                        mv "$f" "$ROOT/handlers.py.conflict"; WARN=1
                        echo "     ⚠ handlers.py уже существует → сохранён как handlers.py.conflict"
                    else
                        mv "$f" "$ROOT/handlers.py"
                    fi
                fi
                ;;
            *.pyc) if [ "$APPLY" -eq 1 ]; then rm -f "$f"; fi ;;
            *) move_one "$f" "$ROOT/$name" ;;
        esac
    done
    shopt -u nullglob
    echo
fi

# ------------------------------------------------- bot/*  →  корень
if [ -d "$BOT" ]; then
    echo "📦 Переношу bot/ → корень:"
    shopt -s nullglob
    for f in "$BOT"/*; do
        [ -d "$f" ] && continue
        name="$(basename "$f")"
        case "$name" in
            __init__.py)
                if [ -s "$f" ]; then
                    echo "  ⚠ bot/__init__.py не пустой — проверьте его вручную, сохраняю как bot__init__.py.bak"
                    WARN=1
                    if [ "$APPLY" -eq 1 ]; then mv "$f" "$ROOT/bot__init__.py.bak"; fi
                else
                    echo "  ✗ bot/__init__.py пустой (маркер пакета) → удаляю"
                    if [ "$APPLY" -eq 1 ]; then rm -f "$f"; fi
                fi
                ;;
            *.pyc) if [ "$APPLY" -eq 1 ]; then rm -f "$f"; fi ;;
            *) move_one "$f" "$ROOT/$name" ;;
        esac
    done
    shopt -u nullglob
    echo
fi

# ------------------------------------------------- чистка
echo "🧹 Чистка кэша и пустых каталогов:"
if [ "$APPLY" -eq 1 ]; then
    find "$ROOT" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
    rmdir "$HANDLERS" 2>/dev/null && echo "  ✗ удалён bot/handlers" || true
    rmdir "$BOT" 2>/dev/null && echo "  ✗ удалён bot/" || true
    if [ -d "$BOT" ]; then
        echo "  ⚠ каталог bot/ не пуст, осталось:"
        ls -A "$BOT" | sed 's/^/     /'
        WARN=1
    fi
else
    echo "  (в пробном прогоне не выполняется)"
fi
echo

# ------------------------------------------------- проверка импортов
if [ "$APPLY" -eq 1 ]; then
    echo "🧪 Проверка импортов:"
    if (cd "$ROOT" && python3 -c "
import importlib, sys
sys.path.insert(0, '.')
mods = ['config','database','utils','state','keyboards','formatters','strategies',
        'polymarket_trading','jobs','common','menu','stations','markets','checks',
        'trade','orders','settings','poly_api','plots','text','handlers','main']
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append((m, e))
if bad:
    for m, e in bad:
        print('  ❌', m, '->', type(e).__name__, e)
    sys.exit(1)
print('  ✅ все модули импортируются из плоской структуры')
"); then
        :
    else
        echo "  ⚠ есть проблемы с импортом — смотрите вывод выше"
        WARN=1
    fi
    echo
fi

echo "────────────────────────────────────────────"
if [ "$APPLY" -eq 0 ]; then
    echo "Пробный прогон завершён. Реальный перенос: bash flatten_structure.sh --apply"
else
    echo "Готово. Запуск бота теперь: cd $ROOT && python3 main.py"
    echo "В systemd-юните: WorkingDirectory=$ROOT, ExecStart=/usr/bin/python3 $ROOT/main.py"
    if [ "$WARN" -eq 1 ]; then echo "⚠ Были предупреждения — просмотрите вывод выше перед стартом."; fi
fi
