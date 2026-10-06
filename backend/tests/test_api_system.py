"""T12: состояние системы — один эндпоинт, только admin (ваше уточнение).

T13c: stage_b_reprocessing_loop_applications/status — страховка после
расследования петли повторной обработки стадии Б (found bug: content_hash
снимка включал наши же статус/комментарий — исправлено, но причину
конкретного репорта "6 и 5 решений" воспроизвести не удалось; это монитор
на случай, если тот же симптом вернётся другим путём)."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from app.config import get_settings
from app.models import Application, Decision, ExternalSnapshot, StatusHistory
from tests._admin_helpers import create_employee, login
from tests.test_engine_pipeline import active_ruleset  # noqa: F401

_NON_ADMIN_ROLES = ("risk_manager", "viewer", "compliance", "operator")


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
        "status", "registries", "ruleset_active_version", "ruleset_matches_file", "fssp_results_after_decision",
        "bki_configured", "bki_paid_requests_this_month", "partner_integration_errors_last_24h",
        "stuck_applications_over_1h", "stuck_in_ocr", "stage_b_reprocessing_loop_applications",
    ):
        assert key in body
    assert body["bki_configured"] is False  # not_configured по умолчанию (T11)
    assert body["status"] == "ok"  # ничего не насеяно — петли нет
    assert body["stage_b_reprocessing_loop_applications"] == 0
    assert body["stuck_in_ocr"] == 0


async def _application(session, *, external_id: str) -> Application:
    application = Application(
        external_id=external_id, status="gray_zone",
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={}, consent_pd_at=datetime.now(timezone.utc), submitted_at=datetime.now(timezone.utc),
    )
    session.add(application)
    await session.flush()
    return application


async def test_stage_b_reprocessing_loop_flags_degraded_and_logs_error(
    admin_client, session_factory, active_ruleset
):
    """Найденный баг T13c, страховка: больше 3 снимков ИЛИ больше 3 решений
    стадии Б за последний час у одной заявки — признак петли повторной
    обработки, даже если саму причину петли мы не воспроизвели."""
    await create_employee(session_factory, email="admin2@example.com", role="admin")
    await login(admin_client, "admin2@example.com")

    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        application = await _application(session, external_id=f"LOOP-{uuid.uuid4().hex[:8]}")
        for i in range(4):  # больше порога (3)
            payload = {"stage_b": {"n": i}}
            session.add(
                ExternalSnapshot(
                    application_id=application.id, stage="B", payload_raw=payload,
                    content_hash=f"{i:064d}", received_at=now - timedelta(minutes=i),
                )
            )
        await session.commit()

    with capture_logs() as logs:
        response = await admin_client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["stage_b_reprocessing_loop_applications"] == 1

    error_events = [entry for entry in logs if entry.get("event") == "stage_b_reprocessing_loop_detected"]
    assert len(error_events) == 1
    assert error_events[0]["log_level"] == "error"
    assert str(application.id) in error_events[0]["application_ids"]


async def test_stage_b_reprocessing_loop_at_threshold_is_not_degraded(admin_client, session_factory):
    """Ровно 3 — ещё не «больше трёх» (ваша формулировка), не должно шуметь."""
    await create_employee(session_factory, email="admin3@example.com", role="admin")
    await login(admin_client, "admin3@example.com")

    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        application = await _application(session, external_id=f"LOOP-OK-{uuid.uuid4().hex[:8]}")
        for i in range(3):  # ровно порог — не больше
            payload = {"stage_b": {"n": i}}
            session.add(
                ExternalSnapshot(
                    application_id=application.id, stage="B", payload_raw=payload,
                    content_hash=f"{i:064d}", received_at=now - timedelta(minutes=i),
                )
            )
        await session.commit()

    response = await admin_client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["stage_b_reprocessing_loop_applications"] == 0


async def test_stuck_in_ocr_flags_degraded_and_logs_error(admin_client, session_factory):
    """Ваше явное указание после находки с 22 зависшими заявками: заявка
    дольше OCR_STUCK_MINUTES в статусе "ocr" — degraded, не просто строка на
    экране (авто-восстановление должно было её забрать, раз не забрало —
    значит фоновая задача не работает)."""
    await create_employee(session_factory, email="admin4@example.com", role="admin")
    await login(admin_client, "admin4@example.com")

    stuck_minutes = get_settings().ocr_stuck_minutes
    entered_at = datetime.now(timezone.utc) - timedelta(minutes=stuck_minutes + 5)

    async with session_factory() as session:
        application = Application(
            external_id=f"OCR-STUCK-{uuid.uuid4().hex[:8]}", status="ocr",
            goods_name="Товар", goods_price=Decimal("10000.00"), down_payment=Decimal("0"),
            term_months=6, tariff="standard", requested_amount=Decimal("10000.00"),
            profile={}, consent_pd_at=datetime.now(timezone.utc), submitted_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.flush()
        session.add(
            StatusHistory(
                application_id=application.id, from_status="received", to_status="ocr",
                actor_type="system", created_at=entered_at,
            )
        )
        await session.commit()
        application_id = application.id

    with capture_logs() as logs:
        response = await admin_client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["stuck_in_ocr"] == 1

    error_events = [entry for entry in logs if entry.get("event") == "stage_a_ocr_stuck_detected"]
    assert len(error_events) == 1
    assert str(application_id) in error_events[0]["application_ids"]


async def test_fresh_ocr_status_is_not_stuck(admin_client, session_factory):
    """Заявка только что вошла в "ocr" (ещё не прошло OCR_STUCK_MINUTES) —
    не должна считаться зависшей, обработка в самом разгаре."""
    await create_employee(session_factory, email="admin5@example.com", role="admin")
    await login(admin_client, "admin5@example.com")

    async with session_factory() as session:
        application = Application(
            external_id=f"OCR-FRESH-{uuid.uuid4().hex[:8]}", status="ocr",
            goods_name="Товар", goods_price=Decimal("10000.00"), down_payment=Decimal("0"),
            term_months=6, tariff="standard", requested_amount=Decimal("10000.00"),
            profile={}, consent_pd_at=datetime.now(timezone.utc), submitted_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.flush()
        session.add(
            StatusHistory(
                application_id=application.id, from_status="received", to_status="ocr",
                actor_type="system", created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    response = await admin_client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["stuck_in_ocr"] == 0
