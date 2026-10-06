"""T16: ФССП с капчей — app/api/fssp.py. Капчу решает только человек (ваше
жёсткое правило) — тестами проверяем протокол вокруг неё (роли, одна активная
сессия на субъекта, обязательная причина при повторе, предупреждение после
уже принятого решения), не само распознавание (его и не существует)."""

from unittest.mock import patch

from sqlalchemy import select

from app.engine.pipeline import run_stage_a
from app.models import Application, AuditLog, Check, Decision, FsspCaptchaSession, FsspPostDecisionAlert, Guarantor
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.sources.fssp import FakeFsspCaptchaSource
from tests._admin_helpers import create_employee, login
from tests.test_engine_pipeline import _age_overrides, _ingest, active_ruleset  # noqa: F401

_VALID_CODE = "424242"


async def _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset):
    """Заявка с решением (AGE_BELOW_MIN, стадия А) — самый короткий путь до
    Decision, который нужен для проверки fssp_post_decision_alerts (ваше
    явное указание, п.3 правок: статус заявки после решения не меняем)."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))
    await run_stage_a(
        session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )
    return application_id


def _patched_source(fake: FakeFsspCaptchaSource):
    return patch("app.api.fssp.build_fssp_captcha_source", return_value=fake)


async def test_fssp_search_forbidden_for_admin_and_viewer(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)

    for role in ("admin", "viewer", "compliance"):
        await create_employee(session_factory, email=f"{role}-fssp@example.com", role=role)
        await login(admin_client, f"{role}-fssp@example.com")
        response = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        assert response.status_code == 403, role


async def test_fssp_regions_endpoint_lists_regions_and_suggests_by_address(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op-regions@example.com", role="operator")
    await login(admin_client, "op-regions@example.com")

    response = await admin_client.get(
        "/api/fssp/regions", params={"application_id": str(application_id), "subject_type": "applicant"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["regions"]) > 10
    assert all({"code", "label"} <= r.keys() for r in body["regions"])
    # Регион не подставляется молча (ваше явное указание) — поле suggested_region_code
    # может быть None, но его наличие/отсутствие не блокирует получение списка.
    assert "suggested_region_code" in body


async def test_fssp_search_and_submit_code_happy_path(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op1@example.com", role="operator")
    await login(admin_client, "op1@example.com")

    fake = FakeFsspCaptchaSource(valid_code=_VALID_CODE)
    with _patched_source(fake):
        search_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        assert search_resp.status_code == 200
        search_body = search_resp.json()
        assert search_body["captcha_png_base64"]
        session_id = search_body["session_id"]

        # Найденный баг (живой прогон, T16): OcrResult.fields — обёртка
        # {"fields": {...}, ...}, не сами поля; без распаковки в search()
        # уходил пустой Subject (ФИО/дата рождения — None), и реальный сайт
        # ФССП искал по пустой строке. FakeFsspCaptchaSource не валидирует
        # вход, поэтому без этой проверки баг не поймать юнит-тестом.
        assert fake.search_calls[-1].last_name
        assert fake.search_calls[-1].birth_date is not None

        submit_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{session_id}/submit-code",
            json={"code": _VALID_CODE},
        )
        assert submit_resp.status_code == 200
        submit_body = submit_resp.json()
        assert submit_body["outcome"] == "ok"
        assert submit_body["check"]["status"] == "ok"

    async with session_factory() as session:
        check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source == "fssp")
            )
        ).scalars().first()
        assert check is not None
        assert check.status == "ok"

        session_row = await session.get(FsspCaptchaSession, session_id)
        assert session_row.status == "resolved"


async def test_fssp_wrong_code_opens_new_session_and_old_session_rejected(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op2@example.com", role="operator")
    await login(admin_client, "op2@example.com")

    fake = FakeFsspCaptchaSource(valid_code=_VALID_CODE)
    with _patched_source(fake):
        search_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        old_session_id = search_resp.json()["session_id"]

        wrong_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{old_session_id}/submit-code",
            json={"code": "000000"},
        )
        assert wrong_resp.status_code == 200
        wrong_body = wrong_resp.json()
        assert wrong_body["outcome"] == "wrong_code"
        assert wrong_body["message"] == "Код неверный, введите новый"
        new_session_id = wrong_body["new_session_id"]
        assert new_session_id != old_session_id

        # Старая сессия сгорела — тот же code_id/код больше не принимается.
        retry_old = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{old_session_id}/submit-code",
            json={"code": _VALID_CODE},
        )
        assert retry_old.status_code == 409

        ok_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{new_session_id}/submit-code",
            json={"code": _VALID_CODE},
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["outcome"] == "ok"


async def test_fssp_two_operators_concurrent_search_invalidates_older_session(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    """T16, п.5 ваших правок: одна активная сессия на (заявку, субъекта) —
    второй поиск (например, второй оператор открыл ту же заявку) гасит
    предыдущую, её code_id больше не принимается submit-code."""
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op-a@example.com", role="operator")
    await create_employee(session_factory, email="op-b@example.com", role="operator")

    fake = FakeFsspCaptchaSource(valid_code=_VALID_CODE)
    with _patched_source(fake):
        await login(admin_client, "op-a@example.com")
        first_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        first_session_id = first_resp.json()["session_id"]

        await login(admin_client, "op-b@example.com")
        second_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "23"},
        )
        second_session_id = second_resp.json()["session_id"]
        assert second_session_id != first_session_id

        async with session_factory() as session:
            first_row = await session.get(FsspCaptchaSession, first_session_id)
            assert first_row.status == "expired"
            second_row = await session.get(FsspCaptchaSession, second_session_id)
            assert second_row.status == "pending_captcha"

        # Оператор A пытается ввести код по своей (уже протухшей) сессии — отказ.
        await login(admin_client, "op-a@example.com")
        stale_submit = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{first_session_id}/submit-code",
            json={"code": _VALID_CODE},
        )
        assert stale_submit.status_code == 409

        # Оператор B доводит СВОЮ сессию до конца успешно.
        await login(admin_client, "op-b@example.com")
        ok_submit = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{second_session_id}/submit-code",
            json={"code": _VALID_CODE},
        )
        assert ok_submit.status_code == 200
        assert ok_submit.json()["outcome"] == "ok"


async def test_fssp_repeat_check_requires_reason(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op3@example.com", role="operator")
    await login(admin_client, "op3@example.com")

    fake = FakeFsspCaptchaSource(valid_code=_VALID_CODE)
    with _patched_source(fake):
        first_search = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        session_id = first_search.json()["session_id"]
        await admin_client.post(
            f"/api/applications/{application_id}/fssp/{session_id}/submit-code", json={"code": _VALID_CODE},
        )

        no_reason = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        assert no_reason.status_code == 409

        with_reason = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77", "reason": "оператор запросил повтор"},
        )
        assert with_reason.status_code == 200


async def test_fssp_hit_after_decision_creates_post_decision_alert_without_changing_status(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    """T16, п.3 ваших правок: решение уже принято (стадия А, AGE_BELOW_MIN),
    ФССП находит долг, который сработал бы на hard_stop fssp_credit_debt
    (config/rulesets/dev.yaml: features.fssp_credit_debt_amount > 50000) —
    статус заявки НЕ меняется (правило 12 CLAUDE.md), пишется
    fssp_post_decision_alerts для предупреждения в карточке/счётчика состояния
    системы (app/api/system.py)."""
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)

    async with session_factory() as session:
        status_before = (await session.get(Application, application_id)).status
        decisions_before = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert len(decisions_before) == 1

    await create_employee(session_factory, email="op4@example.com", role="operator")
    await login(admin_client, "op4@example.com")

    fake = FakeFsspCaptchaSource(
        valid_code=_VALID_CODE,
        productions=[{"subject": "взыскание по кредитному договору", "amount": "60000"}],
    )
    with _patched_source(fake):
        search_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
        session_id = search_resp.json()["session_id"]
        submit_resp = await admin_client.post(
            f"/api/applications/{application_id}/fssp/{session_id}/submit-code",
            json={"code": _VALID_CODE},
        )
        assert submit_resp.status_code == 200
        assert submit_resp.json()["check"]["status"] == "hit"

    async with session_factory() as session:
        status_after = (await session.get(Application, application_id)).status
        assert status_after == status_before  # правило 12 — решение не пересматриваем

        alerts = (
            await session.execute(
                select(FsspPostDecisionAlert).where(FsspPostDecisionAlert.application_id == application_id)
            )
        ).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].subject_type == "applicant"


async def test_fssp_search_validation_error_returns_422_and_leaves_audit_without_check(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    """T16, живой прогон (ваше явное указание, п.2/4 правок): субъект
    неполный (нет даты рождения у поручителя) -> 422 с конкретной причиной,
    сайт вообще не запрашивается — checks(source=fssp) не появляется (сайт не
    спрашивали, нечего сохранять, правило 3 CLAUDE.md), но факт попытки
    остаётся в audit_log."""
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)

    async with session_factory() as session:
        guarantor = Guarantor(application_id=application_id, profile={"last_name": "Петров"}, status="used")
        session.add(guarantor)
        await session.commit()
        guarantor_id = guarantor.id

    await create_employee(session_factory, email="op5@example.com", role="operator")
    await login(admin_client, "op5@example.com")

    fake = FakeFsspCaptchaSource(valid_code=_VALID_CODE)
    with _patched_source(fake):
        response = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "guarantor", "subject_id": str(guarantor_id), "region_code": "77"},
        )
    assert response.status_code == 422
    assert "Нет данных для проверки" in response.json()["detail"]

    async with session_factory() as session:
        checks = (
            await session.execute(select(Check).where(Check.application_id == application_id, Check.source == "fssp"))
        ).scalars().all()
        assert checks == []

        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "fssp_search_started"))
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].details["result"] == "validation_error"


async def test_fssp_search_site_unavailable_leaves_audit_and_check_trace(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    """T16, живой прогон (ваше явное указание, п.4 правок): неудачная попытка
    (сайт/сеть подвели) оставляет след — checks(source=fssp,
    status=source_unavailable) И audit_log, не тишина, как было до находки."""
    application_id = await _rejected_applicant(partner_admin, partner_client, storage, session_factory, active_ruleset)
    await create_employee(session_factory, email="op6@example.com", role="operator")
    await login(admin_client, "op6@example.com")

    fake = FakeFsspCaptchaSource(raise_on_search=True)
    with _patched_source(fake):
        response = await admin_client.post(
            f"/api/applications/{application_id}/fssp/search",
            json={"subject_type": "applicant", "region_code": "77"},
        )
    assert response.status_code == 503

    async with session_factory() as session:
        check = (
            await session.execute(select(Check).where(Check.application_id == application_id, Check.source == "fssp"))
        ).scalars().first()
        assert check is not None
        assert check.status == "source_unavailable"
        assert check.error_text

        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "fssp_search_started"))
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].details["result"] == "source_unavailable"
