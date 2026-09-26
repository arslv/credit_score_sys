"""T12: состояние системы — один эндпоинт, только admin (ваше уточнение)."""

import pytest

from tests._admin_helpers import create_employee, login

_NON_ADMIN_ROLES = ("risk_manager", "viewer", "compliance")


@pytest.mark.parametrize("role", _NON_ADMIN_ROLES)
async def test_non_admin_forbidden_from_system_status(admin_client, session_factory, role):
    await create_employee(session_factory, email=f"{role}@example.com", role=role)
    await login(admin_client, f"{role}@example.com")
    response = await admin_client.get("/api/system/status")
    assert response.status_code == 403


async def test_admin_gets_system_status_shape(admin_client, session_factory):
    await create_employee(session_factory, email="admin@example.com", role="admin")
    await login(admin_client, "admin@example.com")

    response = await admin_client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "registries", "ruleset_active_version", "ruleset_matches_file", "fssp_pending", "fssp_overdue",
        "bki_configured", "bki_paid_requests_this_month", "partner_integration_errors_last_24h",
        "stuck_applications_over_1h",
    ):
        assert key in body
    assert body["bki_configured"] is False  # not_configured по умолчанию (T11)
