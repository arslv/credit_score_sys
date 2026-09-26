"""T12: критерий приёмки — "тест на каждую роль: разрешённое работает,
запрещённое -> 403", плюс viewer/admin без ПД, фото и кредитный отчёт только
risk_manager/compliance, просмотр фото и карточки с ПД — в audit_log."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Application,
    AuditLog,
    BkiReport,
    Client,
    Decision,
    Document,
    RulesetVersion,
)
from tests._admin_helpers import create_employee, login

ALL_ROLES = ("risk_manager", "admin", "viewer", "compliance")


async def _seed_ruleset(session_factory) -> uuid.UUID:
    async with session_factory() as session:
        row = RulesetVersion(
            version=1, config={"x": 1}, config_hash="a" * 64, comment=None, author_id=None,
            effective_from=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()
        return row.id


async def _seed_application_with_pii(session_factory):
    ruleset_id = await _seed_ruleset(session_factory)
    async with session_factory() as session:
        client = Client(
            last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1990, 1, 1),
            passport_series="7700", passport_number="123456", phone="+79990000000",
        )
        session.add(client)
        await session.flush()

        application = Application(
            client_id=client.id, external_id=f"ext-{uuid.uuid4().hex[:8]}", status="to_operator",
            goods_name="Холодильник", goods_price=Decimal("50000"), down_payment=Decimal("0"),
            term_months=12, tariff="standard", requested_amount=Decimal("50000"),
            profile={}, consent_pd_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.flush()

        decision = Decision(
            application_id=application.id, stage="A", outcome="manual_review", reason_code=None,
            triggered_rules=[], ruleset_version_id=ruleset_id,
        )
        session.add(decision)

        document = Document(
            application_id=application.id, kind="passport_main", storage_key=f"{application.id}/passport_main.bin",
            sha256="0" * 64, mime="image/png", size_bytes=10,
        )
        session.add(document)

        report = BkiReport(
            client_id=client.id, application_id=application.id, status="ok",
            report_raw={"contracts": []}, features={"pti": 0.1}, score=Decimal("700"),
        )
        session.add(report)
        await session.commit()
        return application.id, client.id, document.id


class _FakeStorage:
    async def save(self, key, data):
        return key

    async def read(self, key):
        return b"fake-photo-bytes"


@pytest.fixture(autouse=True)
def _install_fake_storage(monkeypatch):
    from app.main import app

    app.state.storage = _FakeStorage()


async def _client_for_role(admin_client, session_factory, role: str, email: str | None = None):
    email = email or f"{role}@example.com"
    await create_employee(session_factory, email=email, role=role)
    await login(admin_client, email)
    return admin_client


# --- список/карточка заявок: доступно всем ролям, ПД — только risk_manager/compliance ---


@pytest.mark.parametrize("role", ALL_ROLES)
async def test_every_role_can_list_applications(admin_client, session_factory, role):
    await _client_for_role(admin_client, session_factory, role)
    response = await admin_client.get("/api/applications")
    assert response.status_code == 200


@pytest.mark.parametrize("role", ("viewer", "admin"))
async def test_viewer_and_admin_do_not_see_client_name(admin_client, session_factory, role):
    application_id, _client_id, _doc_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    list_response = await admin_client.get("/api/applications")
    item = next(i for i in list_response.json()["items"] if i["id"] == str(application_id))
    assert item["client_name"] != "Иванов Иван Иванович"
    assert "Иванов" not in (item["client_name"] or "")

    detail_response = await admin_client.get(f"/api/applications/{application_id}")
    assert "Иванов" not in (detail_response.json()["client_name"] or "")


@pytest.mark.parametrize("role", ("risk_manager", "compliance"))
async def test_risk_manager_and_compliance_see_full_client_name(admin_client, session_factory, role):
    application_id, _client_id, _doc_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    detail_response = await admin_client.get(f"/api/applications/{application_id}")
    assert detail_response.json()["client_name"] == "Иванов Иван Иванович"


@pytest.mark.parametrize("role", ("risk_manager", "compliance"))
async def test_viewing_application_with_pii_writes_audit_log(admin_client, session_factory, role):
    application_id, _client_id, _doc_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    await admin_client.get(f"/api/applications/{application_id}")

    async with session_factory() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "view_application"))
        ).scalars().all()
    assert any(r.object_id == application_id for r in rows)


# --- фото: только risk_manager/compliance, с записью в аудит --------------------


@pytest.mark.parametrize("role", ("risk_manager", "compliance"))
async def test_photo_accessible_and_audited_for_allowed_roles(admin_client, session_factory, role):
    application_id, _client_id, document_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    response = await admin_client.get(f"/api/applications/{application_id}/documents/{document_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"fake-photo-bytes"

    async with session_factory() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "view_document"))
        ).scalars().all()
    assert any(r.object_id == document_id for r in rows)


@pytest.mark.parametrize("role", ("admin", "viewer"))
async def test_photo_forbidden_for_disallowed_roles(admin_client, session_factory, role):
    application_id, _client_id, document_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    response = await admin_client.get(f"/api/applications/{application_id}/documents/{document_id}")
    assert response.status_code == 403


# --- кредитный отчёт: только risk_manager/compliance ----------------------------


@pytest.mark.parametrize("role", ("risk_manager", "compliance"))
async def test_credit_report_accessible_for_allowed_roles(admin_client, session_factory, role):
    application_id, _client_id, _doc_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    response = await admin_client.get(f"/api/applications/{application_id}/credit-report")
    assert response.status_code == 200
    assert response.json()["score"] == "700.00" or float(response.json()["score"]) == 700.0

    async with session_factory() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "view_credit_report"))
        ).scalars().all()
    assert any(r.object_id == application_id for r in rows)


@pytest.mark.parametrize("role", ("admin", "viewer"))
async def test_credit_report_forbidden_for_disallowed_roles(admin_client, session_factory, role):
    application_id, _client_id, _doc_id = await _seed_application_with_pii(session_factory)
    await _client_for_role(admin_client, session_factory, role)

    response = await admin_client.get(f"/api/applications/{application_id}/credit-report")
    assert response.status_code == 403


# --- эндпоинты только для admin: сотрудники, аудит, состояние системы -----------


@pytest.mark.parametrize("role", ("risk_manager", "viewer", "compliance"))
async def test_non_admin_roles_forbidden_from_employees_endpoint(admin_client, session_factory, role):
    await _client_for_role(admin_client, session_factory, role)
    response = await admin_client.get("/api/employees")
    assert response.status_code == 403


async def test_admin_can_access_employees_endpoint(admin_client, session_factory):
    await _client_for_role(admin_client, session_factory, "admin")
    response = await admin_client.get("/api/employees")
    assert response.status_code == 200


@pytest.mark.parametrize("role", ("risk_manager", "admin", "viewer"))
async def test_non_compliance_roles_forbidden_from_compliance_endpoints(admin_client, session_factory, role):
    await _client_for_role(admin_client, session_factory, role)
    response = await admin_client.get("/api/compliance/matches")
    assert response.status_code == 403


async def test_compliance_can_access_compliance_endpoint(admin_client, session_factory):
    await _client_for_role(admin_client, session_factory, "compliance")
    response = await admin_client.get("/api/compliance/matches")
    assert response.status_code == 200
