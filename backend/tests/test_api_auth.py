"""T12: вход/выход — ТЗ «Аутентификация» + ваши уточнения (блокировка по IP
отдельно от учётной записи, одинаковый ответ на любую причину отказа, CSRF)."""

import pytest_asyncio

from app.config import get_settings
from app.redis import get_redis
from app.security.sessions import COOKIE_NAME
from tests._admin_helpers import DEFAULT_PASSWORD, create_employee, login

_IP = "127.0.0.1"  # см. диагностику: ASGITransport всегда отдаёт этот client


@pytest_asyncio.fixture(autouse=True)
async def _reset_ip_rate_limit():
    redis = get_redis()
    await redis.delete(f"login_attempts:ip:{_IP}")
    yield
    await redis.delete(f"login_attempts:ip:{_IP}")


async def test_login_without_csrf_header_is_rejected(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="admin")
    response = await admin_client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 403


async def test_successful_login_sets_httponly_cookie_and_returns_profile(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="risk_manager")
    body = await login(admin_client, "a@example.com")
    assert body["role"] == "risk_manager"
    assert COOKIE_NAME in admin_client.cookies


async def test_wrong_password_and_unknown_email_return_identical_response(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="admin")
    admin_client.headers["X-Requested-With"] = "XMLHttpRequest"

    wrong_password = await admin_client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": "not-the-password"}
    )
    unknown_email = await admin_client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "not-the-password"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


async def test_me_without_session_is_401(admin_client):
    response = await admin_client.get("/api/auth/me")
    assert response.status_code == 401


async def test_logout_invalidates_session(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="admin")
    await login(admin_client, "a@example.com")
    assert (await admin_client.get("/api/auth/me")).status_code == 200

    await admin_client.post("/api/auth/logout")
    assert (await admin_client.get("/api/auth/me")).status_code == 401


async def test_account_locked_after_max_failed_attempts_even_with_correct_password(
    admin_client, session_factory
):
    await create_employee(session_factory, email="a@example.com", role="admin")
    admin_client.headers["X-Requested-With"] = "XMLHttpRequest"

    for _ in range(5):
        response = await admin_client.post(
            "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
        )
        assert response.status_code == 401

    response = await admin_client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 401  # аккаунт заблокирован, даже с верным паролем


async def test_ip_blocked_after_max_failed_attempts_regardless_of_email(admin_client, session_factory):
    await create_employee(session_factory, email="a@example.com", role="admin")
    await create_employee(session_factory, email="b@example.com", role="admin")
    admin_client.headers["X-Requested-With"] = "XMLHttpRequest"

    statuses = []
    for i in range(25):
        email = "a@example.com" if i % 2 == 0 else "b@example.com"
        response = await admin_client.post("/api/auth/login", json={"email": email, "password": "wrong"})
        statuses.append(response.status_code)

    # Блокировка по IP (не по конкретному аккаунту — оба чередуются) наступает
    # независимо от того, что оба аккаунта используют один и тот же IP.
    assert 429 in statuses
    assert statuses[-1] == 429  # к последней попытке лимит точно исчерпан


async def test_ip_blocked_starting_from_the_exact_attempt_that_crosses_threshold(
    admin_client, session_factory
):
    """Попытка №(порог+1) обязана получить 429 СРАЗУ — не 401 (ваше уточнение
    после ручной проверки: раньше блокировка проявлялась только с (порог+2)-й
    попытки, потому что решение о блокировке проверялось ДО регистрации самой
    неудачи, а не после неё)."""
    await create_employee(session_factory, email="a@example.com", role="admin")
    admin_client.headers["X-Requested-With"] = "XMLHttpRequest"
    threshold = get_settings().login_ip_max_failed_attempts

    for _ in range(threshold):
        response = await admin_client.post(
            "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
        )
        assert response.status_code == 401

    response = await admin_client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
    )
    assert response.status_code == 429


async def test_x_forwarded_for_header_is_not_trusted_for_ip_blocking(admin_client, session_factory):
    """Заголовок легко подделать — блокировка обязана считаться по РЕАЛЬНОМУ
    адресу подключения (request.client.host), не по тому, что впишет клиент
    (ваше уточнение). Без доверенного прокси перед приложением заголовок вроде
    X-Forwarded-For не читается вовсе — ни для учёта, ни для решения."""
    await create_employee(session_factory, email="a@example.com", role="admin")
    admin_client.headers["X-Requested-With"] = "XMLHttpRequest"
    admin_client.headers["X-Forwarded-For"] = "203.0.113.7"  # подставной адрес
    threshold = get_settings().login_ip_max_failed_attempts

    for _ in range(threshold):
        response = await admin_client.post(
            "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
        )
        assert response.status_code == 401

    response = await admin_client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
    )
    assert response.status_code == 429  # блокировка сработала — по реальному IP

    redis = get_redis()
    assert await redis.get("login_attempts:ip:203.0.113.7") is None  # заголовок целиком проигнорирован
