"""T12: сотрудники — CRUD (admin), последнего активного admin нельзя понизить
или деактивировать, смена пароля/роли/деактивации закрывает все сессии
(ваши уточнения)."""

from sqlalchemy import select

from app.models import Employee, Session
from tests._admin_helpers import DEFAULT_PASSWORD, create_employee, login


async def _admin_client(admin_client, session_factory, email="root@example.com"):
    await create_employee(session_factory, email=email, role="admin")
    await login(admin_client, email)
    return admin_client


async def test_admin_can_create_employee(admin_client, session_factory):
    await _admin_client(admin_client, session_factory)
    response = await admin_client.post(
        "/api/employees",
        json={"full_name": "Новый Сотрудник", "email": "new@example.com", "role": "viewer", "password": "abc12345678"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "viewer"


async def test_non_admin_cannot_create_employee(admin_client, session_factory):
    await create_employee(session_factory, email="rm@example.com", role="operator")
    await login(admin_client, "rm@example.com")
    response = await admin_client.post(
        "/api/employees",
        json={"full_name": "X", "email": "new2@example.com", "role": "viewer", "password": "abc12345678"},
    )
    assert response.status_code == 403


async def test_cannot_create_employee_with_unknown_role(admin_client, session_factory):
    await _admin_client(admin_client, session_factory)
    response = await admin_client.post(
        "/api/employees",
        json={"full_name": "X", "email": "new3@example.com", "role": "superuser", "password": "abc12345678"},
    )
    assert response.status_code == 400


async def test_cannot_deactivate_last_active_admin(admin_client, session_factory):
    admin_id = await create_employee(session_factory, email="only-admin@example.com", role="admin")
    await login(admin_client, "only-admin@example.com")

    response = await admin_client.patch(f"/api/employees/{admin_id}", json={"active": False})
    assert response.status_code == 409

    async with session_factory() as session:
        row = await session.get(Employee, admin_id)
        assert row.active is True


async def test_cannot_demote_last_active_admin(admin_client, session_factory):
    admin_id = await create_employee(session_factory, email="only-admin@example.com", role="admin")
    await login(admin_client, "only-admin@example.com")

    response = await admin_client.patch(f"/api/employees/{admin_id}", json={"role": "viewer"})
    assert response.status_code == 409


async def test_can_deactivate_admin_when_another_active_admin_exists(admin_client, session_factory):
    acting_admin = await create_employee(session_factory, email="acting@example.com", role="admin")
    other_admin_id = await create_employee(session_factory, email="other@example.com", role="admin")
    await login(admin_client, "acting@example.com")

    response = await admin_client.patch(f"/api/employees/{other_admin_id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False


async def test_changing_password_revokes_all_sessions(admin_client, session_factory):
    target_id = await create_employee(session_factory, email="target@example.com", role="viewer")

    # Целевой сотрудник сам логинится (создаёт себе сессию).
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as target_client:
        await login(target_client, "target@example.com")
        assert (await target_client.get("/api/auth/me")).status_code == 200

        async with session_factory() as session:
            sessions_before = (
                await session.execute(select(Session).where(Session.employee_id == target_id))
            ).scalars().all()
        assert len(sessions_before) == 1

        await _admin_client(admin_client, session_factory, email="root2@example.com")
        response = await admin_client.patch(f"/api/employees/{target_id}", json={"password": "brand-new-password-1"})
        assert response.status_code == 200

        # Старая сессия сотрудника больше не действует.
        assert (await target_client.get("/api/auth/me")).status_code == 401


async def test_changing_role_revokes_all_sessions(admin_client, session_factory):
    target_id = await create_employee(session_factory, email="target2@example.com", role="viewer")

    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as target_client:
        await login(target_client, "target2@example.com")

        await _admin_client(admin_client, session_factory, email="root3@example.com")
        response = await admin_client.patch(f"/api/employees/{target_id}", json={"role": "operator"})
        assert response.status_code == 200

        assert (await target_client.get("/api/auth/me")).status_code == 401
