"""Поиск в списках заявок и клиентов (решение владельца 16.11.2026): слова
запроса ищутся в ФИО, телефоне, номерах; каждое слово должно найтись."""

import uuid
from datetime import date

from app.api.search import like_pattern, search_tokens
from app.models import Client
from tests._admin_helpers import create_employee, login
from tests.test_engine_pipeline import _ingest, active_ruleset  # noqa: F401


def test_tokens_and_pattern():
    assert search_tokens(None) == []
    assert search_tokens("  иванов   79 ") == ["иванов", "79"]
    assert len(search_tokens("a b c d e f g")) == 5
    # % и _ из ввода — буквальные символы, не шаблон.
    assert like_pattern("50%_") == "%50\\%\\_%"


async def _unique_client(session_factory) -> tuple[uuid.UUID, str]:
    marker = f"Поисков{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        client = Client(
            last_name=marker, first_name="Тест", middle_name="Тестович", birth_date=date(1990, 1, 1),
            passport_series=f"{uuid.uuid4().int % 9000 + 1000}", passport_number=f"{uuid.uuid4().int % 900000 + 100000}",
            phone="+79995550011",
        )
        session.add(client)
        await session.commit()
        return client.id, marker


async def test_applications_search_by_name_number_and_external_id(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    other_external_id, _other_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    client_id, marker = await _unique_client(session_factory)
    from app.models import Application

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        application.client_id = client_id
        number = application.number
        await session.commit()

    await create_employee(session_factory, email="search_admin@example.com", role="admin")
    await login(admin_client, "search_admin@example.com")

    async def found(q: str) -> set[str]:
        response = await admin_client.get("/api/applications", params={"q": q, "page_size": 100})
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    by_name = await found(marker.lower())
    assert by_name == {str(application_id)}
    # Каждое слово должно найтись: фамилия + имя — да, фамилия + чужое слово — нет.
    assert await found(f"{marker} Тест") == {str(application_id)}
    assert await found(f"{marker} несуществующее") == set()
    assert str(application_id) in await found(f"№{number}")
    assert str(application_id) in await found(external_id)
    assert str(application_id) not in await found(other_external_id)


async def test_clients_search(admin_client, session_factory):
    client_id, marker = await _unique_client(session_factory)
    await create_employee(session_factory, email="search_clients@example.com", role="admin")
    await login(admin_client, "search_clients@example.com")

    response = await admin_client.get("/api/clients", params={"q": marker[:-2], "page_size": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(client_id)]
    assert body["total"] == 1

    nothing = await admin_client.get("/api/clients", params={"q": f"{marker} несуществующее"})
    assert nothing.status_code == 200, nothing.text
    assert nothing.json()["total"] == 0
