"""Создание первого (или любого) администратора — консольная команда, не
эндпоинт и не миграция (T12, ваше уточнение). Пароль вводится через
getpass — не эхается на экран и не попадает в историю шелла, потому что это
не аргумент командной строки, а промпт самой программы.

Запуск: docker compose exec api python -m app.cli.create_admin
T20: любой другой роли — тем же способом, --role (по умолчанию admin):
    docker compose exec api python -m app.cli.create_admin --role operator
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.db import new_sessionmaker
from app.models import Employee
from app.models.employee import EMPLOYEE_ROLES
from app.security.passwords import hash_password


def _read_clean(prompt: str, *, field_label: str) -> str:
    """input() на POSIX никогда не падает сам на битых байтах — стандартный
    обработчик ошибок stdin на этой платформе — surrogateescape (PEP 528):
    непонятный байт тихо становится суррогатной парой, строка собирается
    без исключения. Проблема всплывает значительно позже и значительно
    глубже — когда эту строку пытается уйти в Postgres asyncpg (ему нужен
    честный UTF-8 на проводе) — трассировка через SQLAlchemy, далеко от
    места, где на самом деле что-то пошло не так (битая кодировка терминала
    при вводе, не сама команда). Проверяем кодируемость сразу при вводе и
    просим ввести заново, не давая этой строке дойти до БД вовсе."""
    while True:
        value = input(prompt).strip()
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            print(f"{field_label} введено с ошибкой кодировки, введите ещё раз", file=sys.stderr)
            continue
        return value


async def _run(role: str = "admin") -> int:
    if role not in EMPLOYEE_ROLES:
        print(f"Неизвестная роль {role!r}; допустимые: {', '.join(EMPLOYEE_ROLES)}", file=sys.stderr)
        return 1
    print(f"Создание сотрудника DIALOG Scoring (роль {role})")
    full_name = _read_clean("ФИО: ", field_label="Имя")
    email = _read_clean("Email: ", field_label="Email")
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
                full_name=full_name, email=email, role=role, password_hash=hash_password(password),
            )
            session.add(employee)
            await session.commit()
            print(f"Сотрудник создан ({role}): {employee.id}")
            return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание сотрудника DIALOG Scoring")
    parser.add_argument("--role", default="admin", choices=EMPLOYEE_ROLES)
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.role)))


if __name__ == "__main__":
    main()
