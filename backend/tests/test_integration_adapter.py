import hashlib
import uuid

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.integrations.adapter import sync_applications, write_decision_status
from app.integrations.fake_partner_client import FakePartnerClient
from app.models import Application, Document, ExternalSnapshot, IntegrationEvent


def _unique_name() -> str:
    return f"Тест-{uuid.uuid4().hex[:8]}"


async def _create_partner_application(partner_admin: httpx.AsyncClient, **overrides) -> str:
    payload = {"last_name": _unique_name(), "first_name": "Клиент", **overrides}
    response = await partner_admin.post("/_test/applications", json=payload)
    response.raise_for_status()
    return response.json()["id"]


async def test_new_application_creates_snapshot_and_documents(
    partner_admin, partner_client, storage, session_factory
):
    external_id = await _create_partner_application(partner_admin)

    await sync_applications(session_factory, partner_client, storage)

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        assert application.status == "received"
        assert application.client_id is None  # клиент появится только после OCR (T05)

        snapshots = (
            await session.execute(
                select(ExternalSnapshot).where(ExternalSnapshot.application_id == application.id)
            )
        ).scalars().all()
        assert len(snapshots) == 1
        assert snapshots[0].stage == "A"

        documents = (
            await session.execute(select(Document).where(Document.application_id == application.id))
        ).scalars().all()
        assert {d.kind for d in documents} == {"selfie_passport", "passport_main", "passport_reg"}
        for document in documents:
            stored = await storage.read(document.storage_key)
            assert hashlib.sha256(stored).hexdigest() == document.sha256


async def test_repeated_poll_does_not_duplicate(partner_admin, partner_client, storage, session_factory):
    external_id = await _create_partner_application(partner_admin)

    await sync_applications(session_factory, partner_client, storage)
    await sync_applications(session_factory, partner_client, storage)

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()

        snapshots = (
            await session.execute(
                select(ExternalSnapshot).where(ExternalSnapshot.application_id == application.id)
            )
        ).scalars().all()
        assert len(snapshots) == 1

        documents = (
            await session.execute(select(Document).where(Document.application_id == application.id))
        ).scalars().all()
        assert len(documents) == 3


async def test_manual_status_change_causes_conflict(
    partner_admin, partner_client, storage, session_factory
):
    external_id = await _create_partner_application(partner_admin)
    await sync_applications(session_factory, partner_client, storage)

    # Оператор вручную меняет статус в их системе в обход нашего сервиса.
    response = await partner_admin.post(
        f"/_test/applications/{external_id}/manual-status", json={"status": "on_call"}
    )
    response.raise_for_status()

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        application_id = application.id

    # Мы всё ещё думаем, что статус "new" (как было на момент опроса) — и пытаемся
    # записать своё решение поверх него.
    result = await write_decision_status(
        session_factory,
        partner_client,
        application_id=application_id,
        external_id=external_id,
        expected_current_status="new",
        new_status="rejected",
        comment="тестовый отказ",
    )

    assert result == "conflict"

    detail = await partner_client.get_application(external_id)
    assert detail.status == "on_call"  # запись отменена, статус не тронут

    async with session_factory() as session:
        events = (
            await session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.application_id == application_id,
                    IntegrationEvent.operation == "write_status",
                )
            )
        ).scalars().all()
        assert any(e.status == "conflict" for e in events)


async def test_partner_unavailable_retries_and_application_is_not_lost(
    partner_admin, storage, session_factory
):
    external_id = await _create_partner_application(partner_admin)

    unreachable_client = FakePartnerClient("http://fake_partner:9", get_settings().partner_token)
    try:
        summary = await sync_applications(session_factory, unreachable_client, storage)
    finally:
        await unreachable_client.aclose()

    assert summary["errors"] >= 1

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one_or_none()
        assert application is None  # ничего не создалось наполовину

        events = (
            await session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.operation == "list_applications:new"
                )
                .order_by(IntegrationEvent.created_at.desc())
                .limit(get_settings().integration_retry_max_attempts)
            )
        ).scalars().all()
        attempts = sorted(e.attempt for e in events)
        assert attempts == list(range(1, get_settings().integration_retry_max_attempts + 1))
        assert events[0].status == "error"  # последняя (самая новая) попытка

    working_client = FakePartnerClient(get_settings().partner_base_url, get_settings().partner_token)
    try:
        await sync_applications(session_factory, working_client, storage)
    finally:
        await working_client.aclose()

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        assert application.status == "received"
