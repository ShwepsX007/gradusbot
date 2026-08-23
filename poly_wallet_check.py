#!/usr/bin/env python3
"""
Диагностика кошелька Polymarket.

Запуск на сервере из папки бота:
    python3 poly_wallet_check.py

Скрипт ничего не покупает и не продаёт. Он читает .env, определяет тип
кошелька и говорит, какой конфигурации не хватает, чтобы ордера проходили.

Ключ 'maker address not allowed, please use the deposit wallet flow' означает,
что кошелёк аккаунта — Deposit Wallet (все кошельки, созданные с 4 мая 2026),
и подписывать ордер нужно через официальный SDK polymarket-client.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def env(k, d=""):
    return (os.environ.get(k) or d).strip()


def line(title=""):
    print("\n" + "=" * 62)
    if title:
        print(title)
        print("=" * 62)


def main():
    if sys.version_info < (3, 11):
        line("Версия Python")
        v = ".".join(str(x) for x in sys.version_info[:3])
        print(f"  ❌ Запущено на Python {v}, а polymarket-client требует 3.11+.")
        print("  Поднимите окружение одной командой:")
        print("      bash setup_python311.sh")
        print("  и запустите проверку уже из него:")
        print("      venv311/bin/python poly_wallet_check.py")
        return 1

    pk = env("POLY_PRIVATE_KEY")
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    funder = env("POLY_FUNDER")
    sig = env("POLY_SIGNATURE_TYPE", "1")
    relayer_key = env("POLY_RELAYER_API_KEY")
    relayer_addr = env("POLY_RELAYER_API_KEY_ADDRESS")

    line("1. Что в .env")
    print(f"  POLY_PRIVATE_KEY:     {'задан' if pk else '❌ ПУСТО'}")
    print(f"  POLY_FUNDER:          {funder or '— (пусто)'}")
    print(f"  POLY_SIGNATURE_TYPE:  {sig}")
    print(f"  POLY_API_KEY:         {'есть' if env('POLY_API_KEY') else '— нет'}")
    print(f"  Relayer API key:      {'есть' if relayer_key and relayer_addr else '— нет'}")
    print(f"  POLY_SDK:             {env('POLY_SDK', 'auto')}")

    if not pk:
        print("\n❌ Без приватного ключа подписанта дальше проверять нечего.")
        return 1

    eoa = None
    try:
        from eth_account import Account
        eoa = Account.from_key(pk).address
        print(f"\n  Адрес подписанта (EOA): {eoa}")
    except Exception as e:
        print(f"\n❌ Приватный ключ не читается: {e}")
        return 1

    line("2. Официальный SDK polymarket-client")
    try:
        from polymarket import SecureClient
    except Exception as e:
        print(f"  ❌ Не установлен: {e}")
        print("  Установите:  pip install polymarket-client")
        print("  Это единственный клиент, который умеет Deposit Wallet.")
        return 1

    kwargs = {"private_key": pk}
    if funder:
        kwargs["wallet"] = funder
    if relayer_key and relayer_addr:
        try:
            from polymarket import RelayerApiKey
            kwargs["api_key"] = RelayerApiKey(key=relayer_key, address=relayer_addr)
            print("  Relayer-ключ: применён")
        except Exception as e:
            print(f"  ⚠️ Relayer-ключ не применён: {e}")
            print("     POLY_RELAYER_API_KEY_ADDRESS должен быть полным адресом:")
            print("     0x и ровно 40 символов (Signer Address из окна создания ключа).")
            print("     Без него торговля работает, недоступны только газлесс-операции.")

    try:
        client = SecureClient.create(**kwargs)
    except Exception as e:
        print(f"  ❌ Подключиться не удалось: {e}")
        print("\n  Что обычно помогает:")
        print("   • убрать POLY_FUNDER, чтобы SDK сам нашёл депозит-кошелёк подписанта;")
        print("   • завести Relayer API key: polymarket.com → Settings → API Keys →")
        print("     Relayer API Keys, и положить в POLY_RELAYER_API_KEY и")
        print("     POLY_RELAYER_API_KEY_ADDRESS;")
        print("   • убедиться, что приватный ключ — это ключ подписанта именно того")
        print("     аккаунта Polymarket, где лежат деньги.")
        return 1

    print(f"  ✅ Подключено")
    print(f"  Кошелёк аккаунта: {client.wallet}")
    print(f"  Тип кошелька:     {client.wallet_type}")

    line("3. Деньги и разрешения")
    try:
        ba = client.get_balance_allowance(asset_type="COLLATERAL")
        bal = float(ba.balance) / 1_000_000
        print(f"  Баланс залога: {bal:.2f}")
        allowances = getattr(ba, "allowances", {}) or {}
        zero = [k for k, v in allowances.items() if not v]
        if bal <= 0:
            print("  ⚠️ На кошельке нет залога — ордера будут отбиваться по балансу.")
        if zero:
            print(f"  ⚠️ Нет разрешений для контрактов: {', '.join(zero)}")
            print("     Вызовите client.setup_trading_approvals() или сделайте")
            print("     одну сделку через сайт, чтобы разрешения выставились.")
        elif allowances:
            print("  ✅ Разрешения выданы")
    except Exception as e:
        print(f"  ⚠️ Баланс прочитать не удалось: {e}")

    line("4. Что писать в .env")
    print(f"  POLY_SDK=unified")
    print(f"  POLY_PRIVATE_KEY={pk[:6]}…{pk[-4:]}   (как есть)")
    print(f"  POLY_FUNDER={client.wallet}")
    if str(client.wallet_type) == "DEPOSIT_WALLET":
        print(f"  POLY_SIGNATURE_TYPE=3")
    elif str(client.wallet_type) == "GNOSIS_SAFE":
        print(f"  POLY_SIGNATURE_TYPE=2")
    elif str(client.wallet_type) == "POLY_PROXY":
        print(f"  POLY_SIGNATURE_TYPE=1")
    else:
        print(f"  POLY_SIGNATURE_TYPE=0")
    print("\n  После правки .env перезапустите бота.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
