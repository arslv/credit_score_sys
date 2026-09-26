"""T12: все списки — с постраничной выдачей (ваше уточнение)."""

from tests._admin_helpers import create_employee, login


async def test_applications_list_is_paginated_with_default_page_size(admin_client, session_factory):
    await create_employee(session_factory, email="rm@example.com", role="risk_manager")
    await login(admin_client, "rm@example.com")

    response = await admin_client.get("/api/applications")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size"}
    assert body["page"] == 1
    assert body["page_size"] == 25


async def test_page_size_is_capped(admin_client, session_factory):
    await create_employee(session_factory, email="rm2@example.com", role="risk_manager")
    await login(admin_client, "rm2@example.com")

    response = await admin_client.get("/api/applications", params={"page_size": 1000})
    assert response.status_code == 422  # больше MAX_PAGE_SIZE — отклоняется на уровне схемы


async def test_employees_list_is_paginated(admin_client, session_factory):
    await create_employee(session_factory, email="admin@example.com", role="admin")
    await login(admin_client, "admin@example.com")

    response = await admin_client.get("/api/employees", params={"page": 1, "page_size": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 5
