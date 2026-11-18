"""Устанавливает пароли для существующих аккаунтов без изменения структуры ак.db.

Запуск: py set_passwords.py
Сброс одного пароля: py set_passwords.py --user student1 --reset
"""

import argparse
import getpass
import sqlite3
from pathlib import Path

from argon2 import PasswordHasher

DB_PATH = Path(__file__).resolve().parent / "ак.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Орнату / құпиясөздерді өзгерту")
    parser.add_argument("--user", help="Тек осы логиннің құпиясөзін орнату")
    parser.add_argument("--reset", action="store_true", help="Орнатылған құпиясөзді жаңарту")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        raise SystemExit(f"База данных не найдена: {DB_PATH}")

    hasher = PasswordHasher()
    conn = sqlite3.connect(DB_PATH)
    try:
        if args.user:
            users = conn.execute(
                "SELECT username, password_hash FROM users WHERE username = ?", (args.user,)
            ).fetchall()
        else:
            users = conn.execute(
                "SELECT username, password_hash FROM users ORDER BY id"
            ).fetchall()
        if not users:
            raise SystemExit("Аккаунт не найден")

        print("Пароли вводятся невидимо. Минимум 8 символов.")
        count = 0
        for username, current_hash in users:
            if current_hash.startswith("$argon2") and not args.reset:
                print(f"{username}: пароль уже задан (для замены используйте --reset)")
                continue
            while True:
                password = getpass.getpass(f"Новый пароль для {username}: ")
                if not 8 <= len(password) <= 200:
                    print("Пароль должен содержать 8–200 символов")
                    continue
                repeated = getpass.getpass("Повторите пароль: ")
                if password != repeated:
                    print("Пароли не совпадают. Повторите")
                    continue
                break
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hasher.hash(password), username),
            )
            count += 1
        conn.commit()
        print(f"Готово. Обновлено аккаунтов: {count}.")
    except (KeyboardInterrupt, EOFError):
        conn.rollback()
        print("\nОтменено, изменения не сохранены")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
