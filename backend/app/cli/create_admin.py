"""Создание первого (или любого) администратора — консольная команда, не
эндпоинт и не миграция (T12, ваше уточнение). Пароль вводится через
getpass — не эхается на экран и не попадает в историю шелла, потому что это
не аргумент командной строки, а промпт самой программы.

Запуск: docker compose exec api python -m app.cli.create_admin
"""

import asyncio
import getpass
import sys

from sqlalchemy import select

from app.db import new_sessionmaker
from app.models import Employee
from app.security.passwords import hash_password


async def _run() -> int:
    print("Создание администратора DIALOG Scoring")
    full_name = input("ФИО: ").strip()
    email = input("Email: ").strip()
    if not full_name or not email:
        print("ФИО и email обязательны", file=sys.stderr)
        return 1

    password = getpass.getpass("Пароль: ")
    password_confirm = getpass.getpass("Пароль ещё раз: ")
    if password != password_confirm:
        print("Пароли не совпадают", file=sys.stderr)
        return 1
    if len(password) < 12:
        print("Пароль должен быть не короче 12 символов", file=sys.stderr)
        return 1

    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            existing = (
                await session.execute(select(Employee).where(Employee.email == email))
            ).scalar_one_or_none()
            if existing is not None:
                print(f"Сотрудник с email {email!r} уже существует", file=sys.stderr)
                return 1

            employee = Employee(
                full_name=full_name, email=email, role="admin", password_hash=hash_password(password),
            )
            session.add(employee)
            await session.commit()
            print(f"Администратор создан: {employee.id}")
            return 0
    finally:
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
