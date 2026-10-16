"""T23: банкротство (Федресурс) — ручная отметка сотрудника, app/api/fedresurs.py;
и проверки паспорта стадии А в карточке заявки (passport_checks), видимые и
тогда, когда паспорт в порядке."""

from sqlalchemy import select

from app.api.applications import _passport_checks
from app.models import AuditLog, Check, Guarantor
from tests._admin_helpers import create_employee, login
from tests.test_api_fssp import _rejected_applicant
from tests.test_engine_pipeline import active_ruleset  # noqa: F401


async def test_fedresurs_manual_check_forbidden_for_viewer(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="viewer-fr@example.com", role="viewer")
    await login(admin_client, "viewer-fr@example.com")
    response = await admin_client.post(
        f"/api/applications/{application_id}/fedresurs/manual", json={"result": "not_found"},
    )
    assert response.status_code == 403


async def test_fedresurs_not_found_writes_ok_check_and_audit(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op-fr@example.com", role="operator")
    await login(admin_client, "op-fr@example.com")

    response = await admin_client.post(
        f"/api/applications/{application_id}/fedresurs/manual", json={"result": "not_found"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    async with session_factory() as session:
        check = (await session.execute(
            select(Check).where(Check.application_id == application_id, Check.source == "fedresurs")
        )).scalars().one()
        assert check.subject_type == "applicant" and check.status == "ok"
        assert check.response_raw["manual"] is True and check.response_raw["result"] == "not_found"
        audit = (await session.execute(
            select(AuditLog).where(AuditLog.action == "fedresurs_manual_check", AuditLog.object_id == application_id)
        )).scalars().one()
        assert audit.details["result"] == "not_found"

    card = await admin_client.get(f"/api/applications/{application_id}")
    assert card.status_code == 200
    assert [c["status"] for c in card.json()["checks"] if c["source"] == "fedresurs"] == ["ok"]


async def test_fedresurs_found_requires_comment_and_repeat_appends(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="adm-fr@example.com", role="admin")
    await login(admin_client, "adm-fr@example.com")
    url = f"/api/applications/{application_id}/fedresurs/manual"

    missing = await admin_client.post(url, json={"result": "found", "comment": "   "})
    assert missing.status_code == 422

    first = await admin_client.post(url, json={"result": "not_found"})
    assert first.status_code == 200, first.text
    second = await admin_client.post(url, json={"result": "found", "comment": "дело А15-123/2026, реструктуризация"})
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "hit"

    async with session_factory() as session:
        statuses = (await session.execute(
            select(Check.status).where(Check.application_id == application_id, Check.source == "fedresurs")
            .order_by(Check.started_at)
        )).scalars().all()
    assert statuses == ["ok", "hit"]  # append-only: первая отметка осталась


async def test_fedresurs_guarantor_must_belong_to_application(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    other_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    async with session_factory() as session:
        guarantor = Guarantor(application_id=other_id, profile={"last_name": "Чужой"}, status="used")
        session.add(guarantor)
        await session.commit()
        guarantor_id = guarantor.id
    await create_employee(session_factory, email="op-fr2@example.com", role="operator")
    await login(admin_client, "op-fr2@example.com")

    response = await admin_client.post(
        f"/api/applications/{application_id}/fedresurs/manual",
        json={"subject_type": "guarantor", "subject_id": str(guarantor_id), "result": "not_found"},
    )
    assert response.status_code == 404


async def test_card_shows_passport_checks_even_when_passport_is_fine(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="viewer-pass@example.com", role="viewer")
    await login(admin_client, "viewer-pass@example.com")

    card = await admin_client.get(f"/api/applications/{application_id}")
    assert card.status_code == 200
    checks = {c["key"]: c["status"] for c in card.json()["passport_checks"]}
    # Отказ этой заявки — по возрасту, с паспортом всё в порядке.
    assert checks["format"] == "ok"
    assert checks["expiry"] == "ok"
    assert checks["issue_date"] == "ok"


def test_passport_checks_fail_only_for_hard_stops_warn_otherwise():
    features = {
        "passport_format_valid": True,
        "passport_expired": True,
        "passport_issue_date_invalid": False,
        "passport_blank_year_future": False,
        "passport_blank_year_mismatch": True,
        "passport_department_code_not_found": True,
        "passport_reused": False,
    }
    hard_stops = {"passport_format_valid", "passport_expired", "passport_blank_year_future"}
    result = {c.key: c.status for c in _passport_checks(features, hard_stops)}
    assert result == {
        "format": "ok",
        "expiry": "fail",  # есть стоп-фактор
        "issue_date": "ok",
        "blank_year": "warn",  # несоответствие года бланка — только флаг
        "department_code": "unknown",
        "reuse": "ok",
    }
    # Формат неверен → fail по правилу на passport_format_valid; дат нет → «не проверено».
    broken = {c.key: c.status for c in _passport_checks({"passport_format_valid": False}, hard_stops)}
    assert broken == {"format": "fail", "expiry": "unknown", "issue_date": "unknown", "blank_year": "unknown"}
    assert _passport_checks({}, hard_stops) == []
