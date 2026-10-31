"""Интеграция с CRM по её API (api.yml): вход /new-order и /scoring,
идемпотентность по orderId, исходящая доставка (result, passport-result) с
конфликтом/outbox-повтором."""

import hashlib

from sqlalchemy import select

from app.integrations.adapter import write_decision_status
from app.integrations.partner_notifier import PartnerNotifier
from app.models import Application, Document, IntegrationEvent, PartnerOutboxEvent
from tests._crm import (
    NEW_ORDER_PATH,
    PHOTO_MAIN_B64,
    PHOTO_REG_B64,
    SCORING_PATH,
    crm_headers,
    new_order_body,
    new_order_id,
    scoring_body,
)
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


async def test_repeated_new_order_does_not_duplicate_application(
    partner_admin, partner_client, storage, session_factory,
):
    """Идемпотентность по orderId (правило 11 CLAUDE.md) — тот же /new-order
    повторно (их ретрай на таймауте) не создаёт вторую заявку/документы."""
    from app.main import app as fastapi_app
    from httpx import ASGITransport, AsyncClient

    fastapi_app.state.storage = storage
    external_id = new_order_id()
    payload = new_order_body(external_id)
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        first = await client.post(NEW_ORDER_PATH, json=payload, headers=crm_headers())
        assert first.status_code == 200, first.text
        second = await client.post(NEW_ORDER_PATH, json=payload, headers=crm_headers())
        assert second.status_code == 200, second.text

    async with session_factory() as session:
        applications = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalars().all()
        assert len(applications) == 1

        documents = (
            await session.execute(select(Document).where(Document.application_id == applications[0].id))
        ).scalars().all()
        assert len(documents) == 2  # не выросло на второй доставке


async def test_new_order_needs_two_photos_and_accepts_data_uri(storage, session_factory):
    from app.main import app as fastapi_app
    from httpx import ASGITransport, AsyncClient

    fastapi_app.state.storage = storage
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        one_photo = await client.post(
            NEW_ORDER_PATH, json=new_order_body(new_order_id(), photos=[PHOTO_MAIN_B64]), headers=crm_headers(),
        )
        assert one_photo.status_code == 422, one_photo.text

        with_prefix = await client.post(
            NEW_ORDER_PATH,
            json=new_order_body(new_order_id(), photos=[f"data:image/png;base64,{PHOTO_MAIN_B64}", PHOTO_REG_B64]),
            headers=crm_headers(),
        )
        assert with_prefix.status_code == 200, with_prefix.text


async def test_scoring_same_body_once_changed_body_again(
    partner_admin, partner_client, storage, session_factory, monkeypatch,
):
    """/scoring: тот же запрос повторно — ничего нового; поправили анкету —
    новый снимок и новая стадия Б. Сырое тело CRM — рядом с разобранным."""
    import app.tasks.stage_b as stage_b_tasks
    from app.main import app as fastapi_app
    from app.models import ExternalSnapshot
    from httpx import ASGITransport, AsyncClient

    started: list[str] = []
    monkeypatch.setattr(stage_b_tasks.process_single_application_task, "delay", lambda app_id: started.append(app_id))
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    body = scoring_body(
        external_id, income=80000, obligations=5000, guarantors=[{"lastName": "Поручителев", "firstName": "П"}],
    )
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        for _ in range(2):
            response = await client.post(SCORING_PATH, json=body, headers=crm_headers())
            assert response.status_code == 200, response.text
        changed = await client.post(SCORING_PATH, json={**body, "clientIncome": 95000}, headers=crm_headers())
        assert changed.status_code == 200, changed.text

    assert started == [str(application_id), str(application_id)]
    async with session_factory() as session:
        snapshots = (
            await session.execute(
                select(ExternalSnapshot)
                .where(ExternalSnapshot.application_id == application_id, ExternalSnapshot.stage == "B")
                .order_by(ExternalSnapshot.received_at)
            )
        ).scalars().all()
        application = await session.get(Application, application_id)
    assert len(snapshots) == 2
    first = snapshots[0].payload_raw
    assert first["stage_b"]["income"] == {"declared_monthly": "80000"}
    assert first["stage_b"]["obligations"] == {"monthly_payment_total": "5000"}
    assert first["stage_b"]["deal"]["goods_category_code"] == "household_appliances"
    assert first["stage_b"]["guarantor"] == {"last_name": "Поручителев", "first_name": "П"}
    assert first["crm_raw"]["orderId"] == int(external_id)
    assert application.term_months == 12
    assert application.requested_amount == 54000
    assert application.consent_bki_at is not None


async def test_scoring_for_unknown_order_is_404(partner_webhook_client):
    response = await partner_webhook_client.post(SCORING_PATH, json=scoring_body(new_order_id()), headers=crm_headers())
    assert response.status_code == 404


async def test_manual_status_change_causes_conflict_from_409_body(
    partner_admin, partner_client, storage, session_factory,
):
    """409 с их стороны (в API CRM его пока нет — на случай, если появится):
    current_status/reason в теле ответа. Запись решения отменяется, событие
    conflict (правило 12 CLAUDE.md)."""
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
    # PassportRecognitionResult: их имена полей, значения без уверенности.
    assert items[0]["orderId"] == int(external_id)
    assert items[0]["lastName"] == "Тестов"
    assert items[0]["passportSeries"] is None


async def test_result_body_is_their_scoring_result(partner_admin, partner_client, storage, session_factory):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    result = await write_decision_status(
        session_factory, partner_client, application_id=application_id, external_id=external_id,
        expected_current_status="new", new_status="approved", comment="Одобрено", score=731.6,
    )
    assert result == "ok"
    sent = (await _all_decisions(partner_admin, external_id))[-1]
    assert {k: sent[k] for k in ("orderId", "status", "score", "reason")} == {
        "orderId": int(external_id), "status": "approved", "score": 731, "reason": "Одобрено",
    }
    assert "prev_status" not in sent
