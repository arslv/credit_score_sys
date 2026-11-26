"""Удаление заявки из админки (решение владельца 26.11.2026): только
администратор, с подтверждением номером; заявка удаляется целиком, в журнале
аудита остаётся запись без персданных."""

from sqlalchemy import func, select

from app.models import Application, AuditLog, StatusHistory
from tests._admin_helpers import create_employee, login
from tests.test_engine_pipeline import _ingest, active_ruleset  # noqa: F401


async def test_admin_deletes_application_with_number_confirmation(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    async with session_factory() as session:
        number = (await session.get(Application, application_id)).number

    employee_id = await create_employee(session_factory, email="delete-admin@example.com", role="admin")
    await login(admin_client, "delete-admin@example.com")

    wrong = await admin_client.delete(f"/api/applications/{application_id}", params={"confirm_number": number + 1})
    assert wrong.status_code == 400, wrong.text
    async with session_factory() as session:
        assert await session.get(Application, application_id) is not None

    response = await admin_client.delete(f"/api/applications/{application_id}", params={"confirm_number": number})
    assert response.status_code == 204, response.text

    async with session_factory() as session:
        assert await session.get(Application, application_id) is None
        history = (
            await session.execute(
                select(func.count()).select_from(StatusHistory).where(StatusHistory.application_id == application_id)
            )
        ).scalar_one()
        assert history == 0
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "delete_application"))
        ).scalar_one()
        assert audit.employee_id == employee_id
        assert audit.object_id == application_id
        assert audit.details == {"number": number, "external_id": external_id}

    gone = await admin_client.get(f"/api/applications/{application_id}")
    assert gone.status_code == 404


async def test_only_admin_can_delete(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    _external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    async with session_factory() as session:
        number = (await session.get(Application, application_id)).number

    for role in ("operator", "viewer"):
        await create_employee(session_factory, email=f"delete-{role}@example.com", role=role)
        await login(admin_client, f"delete-{role}@example.com")
        response = await admin_client.delete(f"/api/applications/{application_id}", params={"confirm_number": number})
        assert response.status_code == 403, response.text

    async with session_factory() as session:
        assert await session.get(Application, application_id) is not None
