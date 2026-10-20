"""Тумблер распознавания фото паспорта в админке. Выключено (сейчас так
попросила CRM-сторона): новая заявка ждёт в awaiting_data, фото провайдеру не
уходят; паспортные данные приходят от CRM в /review (блок applicant), по ним
стадия А и сразу стадия Б."""

import random
import uuid
from datetime import date, datetime, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.config import get_settings
from app.engine.pipeline import run_stage_a
from app.models import (
    Application,
    AuditLog,
    Decision,
    ExternalSnapshot,
    OcrResult,
    PartnerInboundEvent,
    StatusHistory,
    SystemSetting,
)
from app.ocr.service import CRM_FORM_PROVIDER
from app.system_settings import is_ocr_enabled, set_ocr_enabled
from app.tasks.stage_a import _stage_a_then_b_from_crm_data
from tests._admin_helpers import create_employee, login
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _all_decisions, _ingest, active_ruleset  # noqa: F401
from tests.test_engine_stage_b import _DEFAULT_STAGE_B_PAYLOAD


async def _disable_ocr(session_factory) -> None:
    async with session_factory() as session:
        await set_ocr_enabled(session, False, employee_id=None)
        await session.commit()


def _applicant(age: int = 30) -> dict:
    today = date.today()
    return {
        "birth_date": today.replace(year=today.year - age).isoformat(),
        "birth_place": "г. Махачкала",
        "passport_series": unique_valid_passport_series(),
        "passport_number": f"{random.randint(0, 999999):06d}",
        "issue_date": today.replace(year=today.year - (age - 25)).isoformat(),
        "issued_by": "МВД по Республике Дагестан",
        "department_code": "050-001",
        "registration_address": "г. Махачкала, ул. Тестовая, д. 1",
    }


async def _post_review(external_id: str, *, applicant: dict | None):
    from app.main import app as fastapi_app

    body = {
        "external_id": external_id,
        "event_id": str(uuid.uuid4()),
        "status": "on_review",
        "deal": _DEFAULT_STAGE_B_PAYLOAD["deal"],
        "employment": _DEFAULT_STAGE_B_PAYLOAD["employment"],
        "income": _DEFAULT_STAGE_B_PAYLOAD["income"],
        "bki_consent": {"given": True, "at": datetime.now(timezone.utc).isoformat()},
    }
    if applicant is not None:
        body["applicant"] = applicant
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        return await client.post(
            "/api/v1/partner/review", json=body,
            headers={"Authorization": f"Bearer {get_settings().partner_inbound_token}"},
        )


class _MustNotBeCalledOcr:
    name = "must-not-be-called"

    async def recognize(self, passport_main: bytes, passport_reg: bytes):
        raise AssertionError("распознавание выключено — провайдер вызываться не должен")


# --- админка ---------------------------------------------------------------


async def test_settings_default_enabled_and_toggle_is_audited(admin_client, session_factory):
    await create_employee(session_factory, email="ocr-admin@example.com", role="admin")
    await login(admin_client, "ocr-admin@example.com")

    response = await admin_client.get("/api/settings/ocr")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True  # без записи — прежнее поведение
    assert body["api_key_configured"] == bool(get_settings().anthropic_api_key.strip())
    assert "api_key" not in body  # сам ключ не отдаём никогда

    response = await admin_client.put("/api/settings/ocr", json={"enabled": False})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["changed_by"] == "Тест Тестов"

    # Повтор того же значения — без новой записи.
    response = await admin_client.put("/api/settings/ocr", json={"enabled": False})
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        assert await is_ocr_enabled(session) is False
        assert (await session.execute(select(func.count()).select_from(SystemSetting))).scalar_one() == 1
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "change_setting"))
        ).scalars().all()
        assert len(audit) == 1
        assert audit[0].details == {"key": "ocr_enabled", "from": True, "to": False}

    response = await admin_client.put("/api/settings/ocr", json={"enabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True


async def test_settings_admin_only(admin_client, session_factory):
    await create_employee(session_factory, email="ocr-operator@example.com", role="operator")
    await login(admin_client, "ocr-operator@example.com")

    assert (await admin_client.get("/api/settings/ocr")).status_code == 403
    assert (await admin_client.put("/api/settings/ocr", json={"enabled": False})).status_code == 403


# --- приём заявки ----------------------------------------------------------


async def test_new_application_waits_for_crm_data_when_ocr_disabled(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    await _disable_ocr(session_factory)

    _external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        assert application.status == "awaiting_data"
        history = (
            await session.execute(select(StatusHistory).where(StatusHistory.application_id == application_id))
        ).scalars().all()
        assert [(h.from_status, h.to_status) for h in history] == [(None, "awaiting_data")]
        assert (
            await session.execute(select(func.count()).select_from(OcrResult).where(OcrResult.application_id == application_id))
        ).scalar_one() == 0


async def test_queued_application_is_not_recognized_after_ocr_disabled(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Заявка пришла при включённом распознавании, но до очереди дело дошло
    уже после выключения — фото провайдеру не уходят."""
    _external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    await _disable_ocr(session_factory)

    status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=_MustNotBeCalledOcr(), partner_client=partner_client, storage=storage,
    )

    assert status == "awaiting_data"


# --- /review с паспортными данными -----------------------------------------


async def test_review_without_passport_data_is_rejected_while_awaiting(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    await _disable_ocr(session_factory)
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    response = await _post_review(external_id, applicant=None)
    assert response.status_code == 422
    assert "passport_series" in response.json()["detail"]

    bad_series = {**_applicant(), "passport_series": "12"}
    response = await _post_review(external_id, applicant=bad_series)
    assert response.status_code == 422
    assert "passport_series" in response.json()["detail"]

    async with session_factory() as session:
        # Ничего не записали: CRM поправит и пришлёт заново тем же или новым event_id.
        for model, condition in (
            (ExternalSnapshot, ExternalSnapshot.stage == "B"),
            (PartnerInboundEvent, PartnerInboundEvent.endpoint == "review"),
            (OcrResult, OcrResult.application_id.is_not(None)),
        ):
            count = (
                await session.execute(
                    select(func.count()).select_from(model).where(model.application_id == application_id, condition)
                )
            ).scalar_one()
            assert count == 0, model.__name__
        assert (await session.get(Application, application_id)).status == "awaiting_data"


async def test_review_with_passport_data_runs_stage_a_then_stage_b(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    await _disable_ocr(session_factory)
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    response = await _post_review(external_id, applicant=_applicant(age=30))
    assert response.status_code == 202, response.text

    async with session_factory() as session:
        ocr_result = (
            await session.execute(select(OcrResult).where(OcrResult.application_id == application_id))
        ).scalar_one()
        assert ocr_result.provider == CRM_FORM_PROVIDER
        assert ocr_result.response_raw["applicant"]["birth_place"] == "г. Махачкала"  # сырое — как пришло
        assert ocr_result.fields["fields"]["department_code"]["value"] == "050-001"

    # Задача из вебхука (Celery) — здесь напрямую.
    await _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        # БКИ в тестах не подключено -> серая зона (как и с распознаванием).
        assert application.status == "gray_zone"
        assert application.client_id is not None
        stages = (
            await session.execute(
                select(Decision.stage).where(Decision.application_id == application_id).order_by(Decision.created_at)
            )
        ).scalars().all()
        assert stages == ["A", "B"]
        history = (
            await session.execute(
                select(StatusHistory.to_status)
                .where(StatusHistory.application_id == application_id)
                .order_by(StatusHistory.created_at)
            )
        ).scalars().all()
        assert history == ["awaiting_data", "stage_a", "to_operator", "stage_b", "gray_zone"]

    # В CRM — одно решение (стадии Б), без промежуточного «передать оператору».
    decisions = await _all_decisions(partner_admin, external_id)
    assert len(decisions) == 1

    # Повторный запуск (вебхук и периодическая задача одновременно) — ничего не делает.
    await _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client)
    async with session_factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(Decision).where(Decision.application_id == application_id))
        ).scalar_one() == 2


async def test_review_with_passport_data_stage_a_reject_skips_stage_b(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    await _disable_ocr(session_factory)
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    response = await _post_review(external_id, applicant=_applicant(age=19))  # младше минимального возраста
    assert response.status_code == 202, response.text

    await _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        assert application.status == "rejected_a"
        stages = (
            await session.execute(select(Decision.stage).where(Decision.application_id == application_id))
        ).scalars().all()
        assert stages == ["A"]

    decisions = await _all_decisions(partner_admin, external_id)
    assert len(decisions) == 1  # отказ стадии А в CRM уходит как обычно
