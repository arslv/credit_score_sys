"""T13a: карточка заявки — человекочитаемые правила, обезличивание для
admin/viewer, повтор проверки недоступного источника (только risk_manager,
только ФНС/Федресурс, ваше уточнение)."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.engine.pipeline import run_stage_a
from app.integrations.adapter import _content_hash
from app.models import AuditLog, Check, Decision, ExternalSnapshot, ManualOverride
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.sources.fedresurs import FakeFedresursSource
from app.sources.fns import FakeFnsSource
from app.sources.fssp import FakeFsspSource
from tests._admin_helpers import create_employee, login
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _age_overrides, _ingest, active_ruleset  # noqa: F401


async def _good_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset, **fns_kwargs):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    final_status = await run_stage_a(
        session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
        fns_source=FakeFnsSource(**fns_kwargs), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )
    return application_id, final_status


async def test_risk_manager_sees_human_readable_rules_and_raw_checks(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))  # AGE_BELOW_MIN — hard_stop
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    await create_employee(session_factory, email="rm@example.com", role="risk_manager")
    await login(admin_client, "rm@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    body = response.json()

    decision = next(d for d in body["decisions"] if d["reason_code"] == "AGE_BELOW_MIN")
    assert decision["triggered_rules_text"]
    assert all(isinstance(t, str) and t for t in decision["triggered_rules_text"])
    assert not any("AGE_BELOW_MIN" in t for t in decision["triggered_rules_text"])  # без служебного кода

    mvd_check = next(c for c in body["checks"] if c["source"] == "mvd")
    assert mvd_check["parsed"] is not None or mvd_check["response_raw"] is not None or mvd_check["status"] == "ok"
    assert body["profile"] is not None
    assert body["ocr_result"] is not None
    assert body["ocr_result"]["fields"]


async def test_stage_b_snapshot_history_exposed_for_full_pii_roles(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Снимки анкеты по стадиям (T04/T10, external_snapshots) — карточка
    отдаёт ВСЮ историю по порядку получения, а не только последний снимок;
    фронт строит подсветку правок оператора между запусками стадии Б, сравнивая
    соседние снимки с stage="B" (ваше уточнение — вы были правы, снимки
    сохраняются, моя прошлая формулировка в отчёте была ошибочной)."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    first_payload = {"stage_b": {"income": {"declared_monthly": "50000"}}}
    second_payload = {"stage_b": {"income": {"declared_monthly": "70000"}}}
    async with session_factory() as session:
        session.add(
            ExternalSnapshot(
                application_id=application_id, stage="B",
                payload_raw=first_payload, content_hash=_content_hash(first_payload),
                received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.add(
            ExternalSnapshot(
                application_id=application_id, stage="B",
                payload_raw=second_payload, content_hash=_content_hash(second_payload),
                received_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        await session.commit()

    await create_employee(session_factory, email="rm5@example.com", role="risk_manager")
    await login(admin_client, "rm5@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    snapshots = response.json()["external_snapshots"]
    assert len(snapshots) >= 3  # stage A (из sync при _ingest) + два stage B выше
    stage_b_snapshots = [s for s in snapshots if s["stage"] == "B"]
    assert len(stage_b_snapshots) == 2
    assert stage_b_snapshots[0]["payload_raw"]["stage_b"]["income"]["declared_monthly"] == "50000"
    assert stage_b_snapshots[1]["payload_raw"]["stage_b"]["income"]["declared_monthly"] == "70000"
    assert stage_b_snapshots[0]["received_at"] < stage_b_snapshots[1]["received_at"]


async def test_admin_and_viewer_get_masked_application_detail(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    await create_employee(session_factory, email="v@example.com", role="viewer")
    await login(admin_client, "v@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    body = response.json()

    assert body["client_name"] == "Клиент скрыт"
    assert body["profile"] is None
    assert body["ocr_result"] is None
    assert body["external_snapshots"] == []
    for check in body["checks"]:
        assert check["parsed"] is None
        assert check["response_raw"] is None


async def test_retry_check_succeeds_for_risk_manager_and_writes_audit(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    application_id, final_status = await _good_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, raise_error=True,
    )

    async with session_factory() as session:
        fns_check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source == "fns")
            )
        ).scalars().first()
    assert fns_check is not None
    assert fns_check.status == "source_unavailable"

    employee_id = await create_employee(session_factory, email="rm2@example.com", role="risk_manager")
    await login(admin_client, "rm2@example.com")

    response = await admin_client.post(f"/api/applications/{application_id}/checks/{fns_check.id}/retry")
    assert response.status_code == 200
    new_check = response.json()["check"]
    assert new_check["id"] != str(fns_check.id)
    assert new_check["source"] == "fns"
    assert new_check["status"] != "source_unavailable"  # рабочий фейк-источник по умолчанию (без raise_error)

    async with session_factory() as session:
        audit_row = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "retry_check", AuditLog.employee_id == employee_id)
            )
        ).scalar_one()
        assert audit_row.object_id == fns_check.id


async def test_retry_check_rejected_for_wrong_role_wrong_source_and_wrong_status(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    application_id, _ = await _good_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, raise_error=True,
    )

    async with session_factory() as session:
        fns_check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source == "fns")
            )
        ).scalars().first()
        mvd_check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source == "mvd")
            )
        ).scalars().first()

    await create_employee(session_factory, email="viewer2@example.com", role="viewer")
    await login(admin_client, "viewer2@example.com")
    response = await admin_client.post(f"/api/applications/{application_id}/checks/{fns_check.id}/retry")
    assert response.status_code == 403

    await create_employee(session_factory, email="rm3@example.com", role="risk_manager")
    await login(admin_client, "rm3@example.com")

    # mvd — локальный источник, не в списке допустимых для ручного повтора.
    response = await admin_client.post(f"/api/applications/{application_id}/checks/{mvd_check.id}/retry")
    assert response.status_code == 409

    # Первый повтор fns удаётся (переводит источник в рабочее состояние) —
    # повторный повтор ТОГО ЖЕ (уже не source_unavailable) check_id отклоняется.
    first_retry = await admin_client.post(f"/api/applications/{application_id}/checks/{fns_check.id}/retry")
    assert first_retry.status_code == 200

    second_retry = await admin_client.post(f"/api/applications/{application_id}/checks/{fns_check.id}/retry")
    assert second_retry.status_code == 409


async def test_discrepancies_only_filter_shows_only_mismatched_overrides(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """«Расхождения с оператором» — главный инструмент риск-менеджера (ТЗ):
    override, который НЕ совпал с решением системы, а не просто «есть
    override» (ваше уточнение при обсуждении плана T13)."""
    mismatch_id, mismatch_status = await _good_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset
    )
    match_id, match_status = await _good_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset
    )

    async with session_factory() as session:
        mismatch_decision = (
            await session.execute(select(Decision).where(Decision.application_id == mismatch_id))
        ).scalars().first()
        match_decision = (
            await session.execute(select(Decision).where(Decision.application_id == match_id))
        ).scalars().first()
        actual_outcome = (match_decision.outcome if match_decision else None) or "manual_review"
        mismatched_outcome = "approve" if actual_outcome != "approve" else "reject"

        session.add(
            ManualOverride(
                application_id=mismatch_id,
                decision_id=mismatch_decision.id if mismatch_decision else None,
                from_outcome=actual_outcome, to_outcome=mismatched_outcome,
                reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
            )
        )
        session.add(
            ManualOverride(
                application_id=match_id,
                decision_id=match_decision.id if match_decision else None,
                from_outcome=actual_outcome, to_outcome=actual_outcome,
                reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
            )
        )
        await session.commit()

    await create_employee(session_factory, email="rm4@example.com", role="risk_manager")
    await login(admin_client, "rm4@example.com")

    response = await admin_client.get("/api/applications", params={"discrepancies_only": True})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(mismatch_id) in ids
    assert str(match_id) not in ids
