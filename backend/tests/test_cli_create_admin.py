"""T12: создание первого администратора — консольная команда, не эндпоинт и
не миграция, пароль через getpass (ваше уточнение)."""

from sqlalchemy import select

import app.cli.create_admin as create_admin_module
from app.models import Employee
from app.security.passwords import verify_password


class _FakeEngine:
    async def dispose(self) -> None:
        pass


def _patch_session_factory(monkeypatch, session_factory) -> None:
    # create_admin импортирует new_sessionmaker по имени (from app.db import
    # new_sessionmaker) — патчить нужно саму ссылку в его модуле, не app.db.
    monkeypatch.setattr(create_admin_module, "new_sessionmaker", lambda: (_FakeEngine(), session_factory))


async def test_create_admin_creates_employee_with_admin_role(session_factory, monkeypatch):
    inputs = iter(["Админ Админов", "cli-admin@example.com"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    passwords = iter(["a-very-strong-password-123", "a-very-strong-password-123"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    _patch_session_factory(monkeypatch, session_factory)

    exit_code = await create_admin_module._run()
    assert exit_code == 0

    async with session_factory() as session:
        employee = (
            await session.execute(select(Employee).where(Employee.email == "cli-admin@example.com"))
        ).scalar_one()
    assert employee.role == "admin"
    assert verify_password(employee.password_hash, "a-very-strong-password-123")


async def test_create_admin_rejects_mismatched_passwords(session_factory, monkeypatch):
    inputs = iter(["Админ Админов", "cli-admin2@example.com"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    passwords = iter(["password-one-123456", "password-two-123456"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    _patch_session_factory(monkeypatch, session_factory)

    exit_code = await create_admin_module._run()
    assert exit_code == 1

    async with session_factory() as session:
        rows = (
            await session.execute(select(Employee).where(Employee.email == "cli-admin2@example.com"))
        ).scalars().all()
    assert rows == []


async def test_create_admin_rejects_short_password(session_factory, monkeypatch):
    inputs = iter(["Админ Админов", "cli-admin3@example.com"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    passwords = iter(["short", "short"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    _patch_session_factory(monkeypatch, session_factory)

    exit_code = await create_admin_module._run()
    assert exit_code == 1


async def test_create_admin_reprompts_on_broken_encoding_name(session_factory, monkeypatch, capsys):
    """Найденный баг: input() на POSIX не падает сам на битых байтах
    (surrogateescape, PEP 528) — непроверенная строка долетала до asyncpg и
    роняла команду трассировкой через SQLAlchemy. Проверяем здесь, что битое
    ФИО переспрашивается с понятным сообщением, а не пишется в БД."""
    broken_name = "Иван\udcc0\udcc1ов"
    inputs = iter([broken_name, "Админ Админов", "cli-admin4@example.com"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    passwords = iter(["a-very-strong-password-123", "a-very-strong-password-123"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    _patch_session_factory(monkeypatch, session_factory)

    exit_code = await create_admin_module._run()
    assert exit_code == 0
    assert "Имя введено с ошибкой кодировки, введите ещё раз" in capsys.readouterr().err

    async with session_factory() as session:
        employee = (
            await session.execute(select(Employee).where(Employee.email == "cli-admin4@example.com"))
        ).scalar_one()
    assert employee.full_name == "Админ Админов"


async def test_create_admin_reprompts_on_broken_encoding_email(session_factory, monkeypatch, capsys):
    broken_email = "admin\udcc0@example.com"
    inputs = iter(["Админ Админов", broken_email, "cli-admin5@example.com"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    passwords = iter(["a-very-strong-password-123", "a-very-strong-password-123"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    _patch_session_factory(monkeypatch, session_factory)

    exit_code = await create_admin_module._run()
    assert exit_code == 0
    assert "Email введено с ошибкой кодировки, введите ещё раз" in capsys.readouterr().err

    async with session_factory() as session:
        employee = (
            await session.execute(select(Employee).where(Employee.email == "cli-admin5@example.com"))
        ).scalar_one()
    assert employee is not None
