"""Запрос в БКИ по кнопке сотрудника (решение владельца): стадия Б после
дешёвых проверок ждёт в awaiting_bki, CRM ничего не получает; сотрудник
выбирает «обычный» или «подробный» отчёт; бюро вызывается только после
заказа и с кодом выбранного вида."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.engine.stage_b_pipeline import finalize_stage_b, run_stage_b
from app.models import AuditLog, BkiReport, BkiRequestOrder, Decision, StatusHistory
from app.models import Application
from app.sources.bki import FakeBkiProvider, good_history_report
from tests._admin_helpers import create_employee, login
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _all_decisions, active_ruleset  # noqa: F401
from tests.test_engine_stage_b import (
    _bki_reports_for_applicant,
    _link_applicant_to_client,
    _mark_fssp_checked,
    _prepare_application_at_stage_b,
)


@pytest.fixture
def manual_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "bki_request_mode", "manual")


@pytest.fixture
def queued(monkeypatch):
    import app.tasks.stage_b as stage_b_tasks

    calls: list[str] = []
    monkeypatch.setattr(stage_b_tasks.request_bki_report_task, "delay", lambda app_id: calls.append(app_id))
    return calls


async def _ready_application(partner_admin, partner_client, storage, session_factory):
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    await _mark_fssp_checked(session_factory, application_id)
    return external_id, application_id


async def _order(admin_client, session_factory, application_id, report_type: str, *, email: str):
    await create_employee(session_factory, email=email, role="operator")
    await login(admin_client, email)
    return await admin_client.post(f"/api/applications/{application_id}/bki-request", json={"report_type": report_type})


async def test_stage_b_waits_for_order_and_sends_nothing_to_crm(
    manual_mode, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    external_id, application_id = await _ready_application(partner_admin, partner_client, storage, session_factory)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    status = await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    assert status == "awaiting_bki"
    assert provider.requested_subjects == []  # бюро не трогали
    assert await _all_decisions(partner_admin, external_id) == []  # CRM ничего не получила
    # Повторный подбор без заказа — по-прежнему ждём, бюро не трогаем.
    assert await finalize_stage_b(
        session_factory, application_id, partner_client=partner_client, bki_provider=provider,
    ) == "awaiting_bki"
    assert provider.requested_subjects == []
    async with session_factory() as session:
        assert (await session.execute(select(Decision).where(Decision.application_id == application_id))).first() is None


async def test_detailed_order_requests_full_report_and_approves(
    manual_mode, queued, admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    external_id, application_id = await _ready_application(partner_admin, partner_client, storage, session_factory)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))
    assert await run_stage_b(
        session_factory, application_id, partner_client=partner_client, bki_provider=provider,
    ) == "awaiting_bki"

    response = await _order(admin_client, session_factory, application_id, "detailed", email="bki-operator@example.com")
    assert response.status_code == 202, response.text
    assert queued == [str(application_id)]
    again = await admin_client.post(f"/api/applications/{application_id}/bki-request", json={"report_type": "basic"})
    assert again.status_code == 409  # уже заказан — второго платного запроса нет

    status = await finalize_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    assert status == "approved"
    assert provider.requested_codes == ["1"]  # полный кредитный отчёт у бюро
    async with session_factory() as session:
        report = (await session.execute(select(BkiReport).where(BkiReport.application_id == application_id))).scalar_one()
        assert report.report_type == "detailed"
        order = (
            await session.execute(select(BkiRequestOrder).where(BkiRequestOrder.application_id == application_id))
        ).scalar_one()
        resumed = (
            await session.execute(
                select(StatusHistory).where(
                    StatusHistory.application_id == application_id,
                    StatusHistory.from_status == "awaiting_bki", StatusHistory.to_status == "stage_b",
                )
            )
        ).scalar_one()
        assert resumed.actor_type == "employee" and resumed.employee_id == order.employee_id
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "request_bki_report", AuditLog.object_id == application_id)
            )
        ).scalar_one()
        assert audit.details == {"report_type": "detailed"}
    sent = await _all_decisions(partner_admin, external_id)
    assert [d["status"] for d in sent] == ["approved"]


async def test_basic_order_never_approves(
    manual_mode, queued, admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    """«Обычный» — информационная часть КИ без кредитов: долговую нагрузку не
    посчитать, одобряет человек (allows_approval: false в правилах)."""
    external_id, application_id = await _ready_application(partner_admin, partner_client, storage, session_factory)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))
    await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    response = await _order(admin_client, session_factory, application_id, "basic", email="bki-basic@example.com")
    assert response.status_code == 202, response.text
    status = await finalize_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    assert status == "gray_zone"
    assert provider.requested_codes == ["2"]
    async with session_factory() as session:
        decision = (await session.execute(select(Decision).where(Decision.application_id == application_id))).scalar_one()
        assert decision.outcome == "manual_review"
        assert decision.reason_code == "BKI_REPORT_TYPE_NO_APPROVAL"
        assert decision.score == Decimal("780.00")
    sent = await _all_decisions(partner_admin, external_id)
    assert "информационная часть" in sent[-1]["reason"]


async def test_order_rules(
    manual_mode, queued, admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    _external_id, application_id = await _ready_application(partner_admin, partner_client, storage, session_factory)

    # Стадия Б ещё не дошла до БКИ — заказывать нечего.
    early = await _order(admin_client, session_factory, application_id, "detailed", email="bki-early@example.com")
    assert early.status_code == 409

    await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=FakeBkiProvider())
    unknown = await admin_client.post(f"/api/applications/{application_id}/bki-request", json={"report_type": "gold"})
    assert unknown.status_code == 422

    await create_employee(session_factory, email="bki-viewer@example.com", role="viewer")
    await login(admin_client, "bki-viewer@example.com")
    forbidden = await admin_client.post(f"/api/applications/{application_id}/bki-request", json={"report_type": "basic"})
    assert forbidden.status_code == 403
    assert queued == []

    detail = await admin_client.get(f"/api/applications/{application_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "awaiting_bki"
    assert [(t["code"], t["label"], t["allows_approval"]) for t in body["bki_report_types"]] == [
        ("basic", "Обычный", False), ("detailed", "Подробный", True),
    ]
    assert body["bki_order"] is None
    async with session_factory() as session:
        assert (await session.get(Application, application_id)).status == "awaiting_bki"
