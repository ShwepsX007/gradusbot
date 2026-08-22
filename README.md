# gradusbot

Телеграм-бот для торговли погодными рынками Polymarket по данным метеостанций
(Wunderground / METAR / CheckWX).

## Структура

Все модули лежат **плоско, в одном каталоге** — никаких пакетов `bot/` и `bot/handlers/`.
Импорты между файлами прямые: `from state import us`, `from keyboards import back`,
`from jobs import schedule_jobs` и т.д.

| Файл | Назначение |
|---|---|
| `main.py` | точка входа, сборка Application, регистрация хендлеров |
| `handlers.py` | диспетчер `on_callback` — раскидывает нажатия кнопок по модулям |
| `config.py` / `database.py` | конфиг из `.env` и SQLite-слой |
| `jobs.py` | периодические задачи: опрос станций, рынки, вход по стратегии, авто-SL/TP |
| `strategies.py` | торговые стратегии (Метео Снайпер, заготовка маркет-мейкера) |
| `polymarket_trading.py` | CLOB-клиент: стакан, баланс, лимитные и рыночные (FOK/FAK) ордера |
| `common.py`, `menu.py`, `stations.py`, `markets.py`, `checks.py`, `trade.py`, `orders.py`, `settings.py`, `poly_api.py`, `plots.py`, `text.py` | обработчики интерфейса |
| `state.py`, `keyboards.py`, `formatters.py`, `utils.py` | состояние сессий, клавиатуры, форматирование, хелперы |

## Запуск

```bash
cd /root/bot_private
pip install -r requirements.txt
python3 main.py
```

systemd:

```ini
WorkingDirectory=/root/bot_private
ExecStart=/usr/bin/python3 /root/bot_private/main.py
```

## Переход со старой структуры (`bot/` + `bot/handlers/`)

На сервере, где файлы ещё разложены по подпапкам:

```bash
cd /root/bot_private
bash flatten_structure.sh            # пробный прогон, ничего не меняет
bash flatten_structure.sh --apply    # перенос + бэкап + проверка импортов
```

Скрипт делает `.tar.gz` бэкап, поднимает файлы из `bot/handlers/` и `bot/` в корень,
превращает `bot/handlers/__init__.py` в `handlers.py`, чистит `__pycache__`,
удаляет пустые каталоги и проверяет, что все модули импортируются.

## Исполнение ордеров

* **Вход** — настоящий рыночный ордер `FOK` (fill-or-kill): либо исполняется целиком
  сразу, либо отменяется. Позиция записывается в БД только по факту подтверждённого филла.
* **Выход** (SL/TP и ручное закрытие) — рыночный `FAK` (fill-and-kill): забирает
  доступный объём, остаток отменяет; недоисполненный остаток остаётся в позиции
  и добивается на следующей проверке.
* `thresh` в связке = worst-price limit, потолок цены исполнения (защита от проскальзывания).
* Ручная торговля через меню «Купить/Продать» по-прежнему ставит лимитку `GTC` в стакан.

## Один экземпляр за раз

`main.py` берёт эксклюзивный файловый лок `bot.lock` в своём каталоге. Если бот уже
запущен, второй процесс завершится с понятным сообщением, а не будет драться за
`getUpdates` (ошибка `telegram.error.Conflict`). Если такая ошибка всё же появилась:

```bash
systemctl stop <ваш-юнит>
pkill -f 'python3 .*main.py'
ps aux | grep -c '[m]ain.py'      # должно быть 0
systemctl start <ваш-юнит>
```

## Токены без стакана

`No orderbook exists for the requested token id` — это не сбой, а закрытый/разрешённый
рынок либо `condition_id` вместо CLOB `token_id`. Такие токены запоминаются на 10 минут
и не опрашиваются повторно; для позиций с SL/TP после трёх промахов подряд авто-выход
отключается с уведомлением в Telegram.
