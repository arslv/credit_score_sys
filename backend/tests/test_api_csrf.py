"""T12: CSRF — все изменяющие запросы требуют служебный заголовок, ни одно
изменяющее действие не доступно через GET (ваши уточнения)."""

from app.main import app
from tests._admin_helpers import DEFAULT_PASSWORD, create_employee, login


def test_no_mutating_route_is_registered_under_get():
    """Механическая проверка по самому дереву маршрутов — не полагается на то,
    что кто-то не забудет проверить это руками при добавлении эндпоинта."""
    mutating_keywords = ("login", "logout", "lift-freeze", "mark-false-positive")
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if "GET" in methods:
            assert not any(keyword in path for keyword in mutating_keywords), (
                f"изменяющий по смыслу путь {path!r} зарегистрирован под GET"
            )


def test_every_non_get_route_declares_only_expected_methods():
    """Ни одна не-GET ручка не отвечает и на GET тоже (иначе то же самое
    действие было бы доступно и как "безопасный" запрос)."""
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        if methods - {"HEAD", "OPTIONS"} != {"GET"}:
            assert "GET" not in methods, f"{route.path!r} отвечает и на GET, и на изменяющий метод: {methods}"


async def test_post_without_csrf_header_is_403(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="admin")
    response = await admin_client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 403


async def test_patch_without_csrf_header_is_403(admin_client, session_factory):
    employee_id = await create_employee(session_factory, email="a@example.com", role="admin")
    await login(admin_client, "a@example.com")
    del admin_client.headers["X-Requested-With"]  # был выставлен login() для самого входа

    response = await admin_client.patch(f"/api/employees/{employee_id}", json={"full_name": "Новое имя"})
    assert response.status_code == 403


async def test_get_never_requires_csrf_header(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="admin")
    await login(admin_client, "a@example.com")
    del admin_client.headers["X-Requested-With"]

    response = await admin_client.get("/api/employees")
    assert response.status_code == 200
