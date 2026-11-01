"""Create a root-owned systemd EnvironmentFile without echoing the password."""
import argparse
import getpass
import os
import re
import secrets
import sys

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description='Создание учётной записи веб-терминала')
    parser.add_argument('path', help='Новый файл настроек (не должен существовать)')
    parser.add_argument('--insecure-http', action='store_true', help='Разрешить cookie по HTTP (данные не шифруются)')
    args = parser.parse_args()
    if os.path.lexists(args.path):
        sys.exit('Файл уже существует; настройки не изменены.')
    username = input('Логин (латинские буквы, цифры, . _ -, от 3 до 64 символов): ').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username):
        sys.exit('Некорректный логин.')
    password = getpass.getpass('Пароль (не менее 16 символов, ввод не отображается): ')
    confirmation = getpass.getpass('Повторите пароль: ')
    if password != confirmation or not 16 <= len(password) <= 256:
        sys.exit('Пароли не совпадают либо длина не от 16 до 256 символов. Файл не создан.')
    password_hash = generate_password_hash(password)
    secret = secrets.token_urlsafe(48)
    content = (f'GRADUS_USERNAME="{username}"\nGRADUS_PASSWORD_HASH="{password_hash}"\n'
               f'GRADUS_SESSION_SECRET="{secret}"\nWEB_COOKIE_SECURE={0 if args.insecure_http else 1}\n')
    # O_EXCL prevents accidentally overwriting existing credentials or following a symlink.
    fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(content)
    print('Создан файл настроек с правами 0600:', args.path)
    if args.insecure_http:
        print('ВНИМАНИЕ: HTTP не шифрует пароль и сессию.')


if __name__ == '__main__':
    main()
