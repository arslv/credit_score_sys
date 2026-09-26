"""Общие хелперы для тестов админ-API (T12) — не тестовый модуль сам по себе
(имя с подчёркиванием, pytest его не собирает)."""

from app.models import Employee
from app.security.passwords import hash_password

DEFAULT_PASSWORD = "TestPassword123!"


async def create_employee(session_factory, *, email: str, role: str, password: str = DEFAULT_PASSWORD, active: bool = True):
    async with session_factory() as session:
        employee = Employee(
            full_name="Тест Тестов", email=email, role=role, password_hash=hash_password(password), active=active,
        )
        session.add(employee)
        await session.commit()
        return employee.id


async def login(client, email: str, password: str = DEFAULT_PASSWORD):
    client.headers["X-Requested-With"] = "XMLHttpRequest"
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()
