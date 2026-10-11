"""T18: интеграция по новой схеме (push) — вебхуки .../api/v1/partner/*,
дедупликация по event_id, исходящая доставка с конфликтом/outbox-повтором.
Замена старого test_integration_adapter.py, который тестировал polling
(sync_applications) — его больше нет, заявки приходят вебхуками."""

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.integrations.adapter import write_decision_status
from app.integrations.partner_notifier import PartnerNotifier
from app.models import Application, Document, IntegrationEvent, PartnerOutboxEvent
from tests.test_engine_pipeline import _all_decisions, _ingest


async def test_new_application_webhook_creates_application_and_two_documents(
    partner_admin, partner_client, storage, session_factory,
):
    """Селфи не принимаем (ваше явное указание) — ровно 2 документа, не 3."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        assert application.status == "received"
        assert application.client_id is None  # клиент появится только после OCR (T05)
        assert application.external_id == external_id

        documents = (
            await session.execute(select(Document).where(Document.application_id == application_id))
        ).scalars().all()
        assert {d.kind for d in documents} == {"passport_main", "passport_reg"}
        for document in documents:
            stored = await storage.read(document.storage_key)
            assert hashlib.sha256(stored).hexdigest() == document.sha256


async def test_repeated_event_id_does_not_duplicate_application(
    partner_admin, partner_client, storage, session_factory,
):
    """Идемпотентность по event_id (ТЗ T18) — тот же вебхук (их ретрай на
    таймауте, например) с ТЕМ ЖЕ event_id не создаёт вторую заявку/документы."""
    from app.main import app as fastapi_app
    from httpx import ASGITransport, AsyncClient

    fastapi_app.state.storage = storage
    external_id = f"EXT-{uuid.uuid4().hex[:10]}"
    event_id = str(uuid.uuid4())
    payload = {
        "external_id": external_id, "event_id": event_id,
        "last_name": "Тестов", "first_name": "Тест", "middle_name": "Тестович", "phone": "+70000000000",
        "photos": {
            "passport_main": {"base64": "iVBORw0KGgpmYWtlLXBhc3Nwb3J0LW1haW4="},  # PNG-сигнатура (T21: только JPEG/PNG)
            "passport_reg": {"base64": "iVBORw0KGgpmYWtlLXBhc3Nwb3J0LXJlZw=="},
        },
    }
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        headers = {"Authorization": f"Bearer {get_settings().partner_inbound_token}"}
        first = await client.post("/api/v1/partner/applications", json=payload, headers=headers)
        assert first.status_code == 202, first.text
        second = await client.post("/api/v1/partner/applications", json=payload, headers=headers)
        assert second.status_code == 202, second.text

    async with session_factory() as session:
        applications = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalars().all()
        assert len(applications) == 1

        documents = (
            await session.execute(select(Document).where(Document.application_id == applications[0].id))
        ).scalars().all()
        assert len(documents) == 2  # не выросло на второй доставке


async def test_manual_status_change_causes_conflict_from_409_body(
    partner_admin, partner_client, storage, session_factory,
):
    """T18: 409 с их стороны — current_status/reason прямо в теле ответа (наш
    же контракт), без отдельного запроса «а какой у вас статус» (раньше —
    get_application). Запись решения отменяется, событие conflict (правило 12 CLAUDE.md)."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    (await partner_admin.post(
        "/_test/configure",
        json={"mode": "conflict", "conflict_current_status": "rejected", "conflict_reason": "оператор отказал сам"},
    )).raise_for_status()

    result = await write_decision_status(
        session_factory, partner_client,
        application_id=application_id, external_id=external_id,
        expected_current_status="new", new_status="approved", comment="тестовое одобрение",
    )

    assert result == "conflict"

    async with session_factory() as session:
        events = (
            await session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.application_id == application_id, IntegrationEvent.operation == "write_status",
                )
            )
        ).scalars().all()
        assert any(e.status == "conflict" for e in events)

        from app.models import ManualOverride

        override = (
            await session.execute(select(ManualOverride).where(ManualOverride.application_id == application_id))
        ).scalar_one()
        assert override.to_outcome == "rejected"
        assert override.comment == "оператор отказал сам"


async def test_partner_unavailable_defers_to_outbox_and_drain_delivers_later(
    partner_admin, partner_client, storage, session_factory,
):
    """«Ничего не теряем» (ваше явное указание): их эндпоинт недоступен — запись
    не падает и не теряется, остаётся pending в partner_outbox_events; когда
    эндпоинт снова доступен, app/tasks/partner_outbox.py её доставляет."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    (await partner_admin.post("/_test/configure", json={"mode": "unavailable"})).raise_for_status()

    result = await write_decision_status(
        session_factory, partner_client,
        application_id=application_id, external_id=external_id,
        expected_current_status="new", new_status="rejected", comment="тестовый отказ",
    )
    assert result == "deferred"

    async with session_factory() as session:
        outbox_rows = (
            await session.execute(
                select(PartnerOutboxEvent).where(PartnerOutboxEvent.application_id == application_id)
            )
        ).scalars().all()
        assert len(outbox_rows) == 1
        assert outbox_rows[0].status == "pending"
        assert outbox_rows[0].event_type == "decision"

    sent = await _all_decisions(partner_admin, external_id)
    assert sent == []  # заглушка ничего не получила — недоступна

    # Эндпоинт снова доступен — drain подбирает отложенную запись.
    (await partner_admin.post("/_test/configure", json={"mode": "ok"})).raise_for_status()

    from app.tasks.partner_outbox import _drain

    summary = await _drain()
    assert summary["sent"] == 1

    async with session_factory() as session:
        row = (
            await session.execute(
                select(PartnerOutboxEvent).where(PartnerOutboxEvent.application_id == application_id)
            )
        ).scalar_one()
        assert row.status == "sent"
        assert row.sent_at is not None

    sent_after = await _all_decisions(partner_admin, external_id)
    assert len(sent_after) == 1
    assert sent_after[0]["status"] == "rejected"


async def test_ocr_result_sent_after_recognition(partner_admin, partner_client, storage, session_factory):
    """ТЗ T18: «после OCR — распознанные паспортные поля», отдельно от решения."""
    from app.integrations.adapter import send_ocr_result

    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    fields = {"last_name": {"value": "Тестов", "confidence": 0.98}}

    await send_ocr_result(
        session_factory, partner_client, application_id=application_id, external_id=external_id, fields=fields,
    )

    response = await partner_admin.get(f"/_test/received/{external_id}")
    response.raise_for_status()
    items = [item for item in response.json()["items"] if item["type"] == "ocr_result"]
    assert len(items) == 1
    assert items[0]["fields"]["last_name"]["value"] == "Тестов"
