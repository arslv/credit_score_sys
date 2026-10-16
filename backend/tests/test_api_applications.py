"""T13a: карточка заявки — человекочитаемые правила, обезличивание для
admin/viewer, повтор проверки недоступного источника (только risk_manager,
только ФНС — fns_inn/fns_npd/fns_egrip, T16; ФССП туда не входит вообще, у
него свой экран с капчей)."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from app.engine.pipeline import run_stage_a
from app.engine.stage_b_pipeline import STAGE_B_FEATURE_SET_VERSION
from app.engine.versions import get_active_ruleset_version
from app.engine.schema import parse_ruleset
from app.engine.versions import save_ruleset_version
from app.integrations.adapter import content_hash as _content_hash
from app.models import (
    Application,
    AuditLog,
    Check,
    Decision,
    ExternalSnapshot,
    FeatureSnapshot,
    GoodsCategory,
    ManualOverride,
)
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.sources.fns import FakeFnsInnSource
from tests._admin_helpers import create_employee, login
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _age_overrides, _ingest, active_ruleset  # noqa: F401


async def _good_applicant(
    partner_admin, partner_client, storage, session_factory, active_ruleset, *, fns_unavailable: bool = False,
):
    """T16: ФНС — фоновая задача, run_stage_a её не ждёт и не принимает
    fns_source. fns_unavailable=True имитирует "фоновая задача уже
    отработала и записала source_unavailable" — вставляем Check напрямую,
    как это сделал бы app/tasks/fns_checks.py."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    final_status = await run_stage_a(
        session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )
    if fns_unavailable:
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            session.add(
                Check(
                    application_id=application_id, subject_type="applicant", source="fns_inn",
                    status="source_unavailable", started_at=now, finished_at=now, duration_ms=0,
                    error_text="fake fns_inn unavailable",
                )
            )
            await session.commit()
    return application_id, final_status


async def test_risk_manager_sees_human_readable_rules_and_raw_checks(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))  # AGE_BELOW_MIN — hard_stop
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    await create_employee(session_factory, email="rm@example.com", role="admin")
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

    await create_employee(session_factory, email="rm5@example.com", role="admin")
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


async def test_every_role_sees_application_pii(  # T23: персданные не скрываются ни у одной роли
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    for role, email in (("viewer", "v@example.com"), ("admin", "adm@example.com")):
        await create_employee(session_factory, email=email, role=role)
        await login(admin_client, email)

        response = await admin_client.get(f"/api/applications/{application_id}")
        assert response.status_code == 200
        body = response.json()

        assert body["client_name"] != "Клиент скрыт", role
        assert body["profile"] is not None, role
        assert body["ocr_result"] is not None, role
        assert body["external_snapshots"], role


async def test_operator_sees_application_pii_in_card_but_stays_out_of_rest(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T16, ваша правка после отчёта: operator нужен ФИО/дата рождения/адрес
    регистрации заявителя (и поручителя — если он есть) в КАРТОЧКЕ заявки,
    чтобы выбрать регион ФССП и отличить должника от однофамильца — но не
    где-либо ещё (сотрудники/аудит/правила/импорт по-прежнему закрыты)."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    await create_employee(session_factory, email="op@example.com", role="operator")
    await login(admin_client, "op@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["client_name"] != "Клиент скрыт"
    assert body["profile"] is not None
    assert body["ocr_result"] is not None
    assert body["ocr_result"]["fields"]

    assert (await admin_client.get("/api/employees")).status_code == 403
    assert (await admin_client.get("/api/audit-log")).status_code == 403
    assert (await admin_client.get("/api/rulesets/active")).status_code == 403
    assert (await admin_client.get("/api/imports/contract-candidates")).status_code == 403


async def test_retry_check_succeeds_for_risk_manager_and_writes_audit(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    application_id, final_status = await _good_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, fns_unavailable=True,
    )

    async with session_factory() as session:
        fns_check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source == "fns_inn")
            )
        ).scalars().first()
    assert fns_check is not None
    assert fns_check.status == "source_unavailable"

    employee_id = await create_employee(session_factory, email="rm2@example.com", role="admin")
    await login(admin_client, "rm2@example.com")

    # Найденный баг (живой прогон, T16): OcrResult.fields — обёртка
    # {"fields": {...}, ...}, не сами поля; без распаковки retry уходил с
    # пустым Subject (ФИО/дата рождения — None) к настоящему источнику.
    # FakeFnsInnSource на пустой Subject тоже отвечает "no_data" (как и на
    # непустой без совпадения в responses) — статус один и тот же в обоих
    # случаях, поэтому проверяем сам Subject, а не только итоговый статус.
    fake_fns_inn = FakeFnsInnSource()
    with patch("app.api.applications.build_fns_inn_source", return_value=fake_fns_inn):
        response = await admin_client.post(f"/api/applications/{application_id}/checks/{fns_check.id}/retry")
    assert response.status_code == 200
    new_check = response.json()["check"]
    assert new_check["id"] != str(fns_check.id)
    assert new_check["source"] == "fns_inn"
    assert new_check["status"] != "source_unavailable"  # рабочий фейк-источник по умолчанию (без fns_unavailable)
    assert fake_fns_inn.check_calls[-1].last_name
    assert fake_fns_inn.check_calls[-1].birth_date is not None

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
        partner_admin, partner_client, storage, session_factory, active_ruleset, fns_unavailable=True,
    )

    async with session_factory() as session:
        fns_check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source == "fns_inn")
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

    await create_employee(session_factory, email="rm3@example.com", role="admin")
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

    await create_employee(session_factory, email="rm4@example.com", role="admin")
    await login(admin_client, "rm4@example.com")

    response = await admin_client.get("/api/applications", params={"discrepancies_only": True})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(mismatch_id) in ids
    assert str(match_id) not in ids

    # T23: очередь admin — только заявка с расхождением. Раньше подзапросы
    # не были привязаны к строке, и одно расхождение ставило в очередь все.
    response = await admin_client.get("/api/applications", params={"view": "action", "page_size": 50})
    assert response.status_code == 200
    queue = {item["id"]: item for item in response.json()["items"]}
    assert str(mismatch_id) in queue
    assert str(match_id) not in queue
    assert queue[str(mismatch_id)]["waiting_since"] is not None


async def test_decision_thresholds_come_from_own_ruleset_version_not_current(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    """T17, шаг 3 (ваше явное указание): пороги в карточке — из версии
    правил, по которой ПРИНЯТО решение, а не из текущей активной. Меняем
    порог ПОСЛЕ решения — старое решение должно по-прежнему показывать
    старое число."""
    application_id, _ = await _good_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset,
    )
    # active_ruleset — dev.yaml как есть: auto_approve_above=700, pti_max=0.60.
    original_approve_above = active_ruleset.config["score_thresholds"]["auto_approve_above"]
    assert original_approve_above == 700

    # Публикуем НОВУЮ версию с другим порогом и более поздним effective_from —
    # она становится активной, старое решение при этом не трогаем (append-only).
    raw = dict(active_ruleset.config)
    raw["score_thresholds"] = {**raw["score_thresholds"], "auto_approve_above": 650}
    raw["effective_from"] = date.today().isoformat()
    new_config = parse_ruleset(raw)
    async with session_factory() as session:
        await save_ruleset_version(session, new_config)
        await session.commit()

    await create_employee(session_factory, email="rm6@example.com", role="admin")
    await login(admin_client, "rm6@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    decision = response.json()["decisions"][0]
    assert decision["score_auto_approve_above"] == 700  # версия решения, не текущая (650)
    assert decision["pti_max"] == 0.60


async def test_card_explains_reject_without_triggered_rules_and_exposes_stage_b_pti(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Карточка не должна писать «стоп-факторов нет» там, где решение —
    отказ. Повторная заморозка по перечню пишет triggered_rules=[] (см.
    _reject_for_active_freeze) — причина должна прийти текстом из reason_code.
    Одобрение стадии Б отдаёт фактический ПДН и порог из ЕГО версии правил,
    чтобы карточка сравнивала «ПДН против порога», а не пересказывала его."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    async with session_factory() as session:
        ruleset_row = await get_active_ruleset_version(session)
        session.add(
            Decision(
                application_id=application_id, stage="A", outcome="reject", reason_code="SANCTIONS_HIT",
                freeze=True, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=None,
            )
        )
        snapshot = FeatureSnapshot(
            application_id=application_id, stage="B",
            features={"score": 780, "features": {"pti": 0.31}}, feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
        )
        session.add(snapshot)
        await session.flush()
        session.add(
            Decision(
                application_id=application_id, stage="B", outcome="approve", reason_code=None,
                freeze=False, triggered_rules=[], ruleset_version_id=ruleset_row.id,
                feature_snapshot_id=snapshot.id, score=780, score_source="bki_score",
            )
        )
        await session.commit()

    await create_employee(session_factory, email="rm_pti@example.com", role="admin")
    await login(admin_client, "rm_pti@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    decisions = response.json()["decisions"]

    rejected = next(d for d in decisions if d["reason_code"] == "SANCTIONS_HIT")
    assert rejected["triggered_rules_text"] == []
    assert rejected["reason_text"] and "перечн" in rejected["reason_text"]
    assert "SANCTIONS_HIT" not in rejected["reason_text"]

    approved = next(d for d in decisions if d["outcome"] == "approve")
    assert approved["reason_text"] is None
    assert approved["pti"] == 0.31
    assert approved["pti_max"] is not None
    assert approved["score_auto_approve_above"] is not None


async def test_card_exposes_decision_stage_for_overrides_and_goods_category_name(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Ручной итог хранит этап решения, которое переопределили: «передано
    оператору» на стадии А и «серая зона» на стадии Б — разные подписи.
    Категория товара — название из справочника, не код из анкеты."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    await run_stage_a(session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage)

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().first()
        category = (await session.execute(select(GoodsCategory).limit(1))).scalars().first()
        assert category is not None, "справочник категорий засеян миграцией 0007"
        application = await session.get(Application, application_id)
        application.goods_category_id = category.id
        session.add(
            ManualOverride(
                application_id=application_id, decision_id=decision.id,
                from_outcome="manual_review", to_outcome="approve", reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
            )
        )
        await session.commit()
        category_name = category.name

    await create_employee(session_factory, email="rm_stage@example.com", role="admin")
    await login(admin_client, "rm_stage@example.com")

    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["goods_category"] == category_name
    assert body["manual_overrides"][0]["decision_stage"] == "A"



async def test_list_queues_are_per_role_and_sorted_by_waiting_time(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """«Требуют действия» — своя очередь у каждой роли (T17, часть 4):
    оператор — позвонить/проверить ФССП; комплаенс — совпадения по перечню;
    риск-менеджер — серая зона и «оператор изменил статус». Наблюдатель
    очереди не имеет. Заявки в очереди — с самой долгой ожидания сверху."""
    _, waiting_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, waiting_id, ocr_provider=FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30))),
        partner_client=partner_client, storage=storage,
    )
    _, frozen_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, frozen_id, ocr_provider=FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19))),
        partner_client=partner_client, storage=storage,
    )
    async with session_factory() as session:
        ruleset_row = await get_active_ruleset_version(session)
        session.add(
            Decision(
                application_id=frozen_id, stage="A", outcome="reject", reason_code="SANCTIONS_HIT",
                freeze=True, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=None,
            )
        )
        await session.commit()

    for email, role in (
        ("op_queue@example.com", "operator"),
        ("adm_queue@example.com", "admin"),
        ("viewer_queue@example.com", "viewer"),
    ):
        await create_employee(session_factory, email=email, role=role)

    def ids(body):
        return {item["id"] for item in body["items"]}

    await login(admin_client, "op_queue@example.com")
    operator = (await admin_client.get("/api/applications", params={"view": "action", "page_size": 50})).json()
    assert str(waiting_id) in ids(operator)
    # T23: разбор совпадений с перечнем (бывший комплаенс) — и в очереди оператора.
    assert str(frozen_id) in ids(operator)
    assert next(i for i in operator["items"] if i["id"] == str(frozen_id))["action_text"] == (
        "Проверить совпадение с перечнем"
    )
    waiting_item = next(item for item in operator["items"] if item["id"] == str(waiting_id))
    assert waiting_item["action_kind"] == "call"
    assert waiting_item["action_text"] == "Позвонить"
    waits = [item["waiting_since"] for item in operator["items"]]
    assert waits == sorted(waits)  # дольше всех ждущие — сверху
    counts = (await admin_client.get("/api/applications/quick-counts")).json()
    assert counts["action"] == operator["total"]

    # T23: admin — бывшие risk_manager и compliance: серая зона, расхождения
    # и совпадения с перечнем; «передано оператору» на стадии А — не его.
    await login(admin_client, "adm_queue@example.com")
    admin_queue = (await admin_client.get("/api/applications", params={"view": "action", "page_size": 50})).json()
    assert str(waiting_id) not in ids(admin_queue)
    assert str(frozen_id) in ids(admin_queue)
    frozen_item = next(item for item in admin_queue["items"] if item["id"] == str(frozen_id))
    assert frozen_item["action_text"] == "Проверить совпадение с перечнем"

    await login(admin_client, "viewer_queue@example.com")
    viewer = (await admin_client.get("/api/applications", params={"view": "action", "page_size": 50})).json()
    assert viewer["total"] == 0
    assert (await admin_client.get("/api/applications/quick-counts")).json()["action"] is None

    await login(admin_client, "adm_queue@example.com")
    gray = (await admin_client.get("/api/applications", params={"view": "gray", "page_size": 50})).json()
    assert str(waiting_id) not in ids(gray)
    today = (await admin_client.get("/api/applications", params={"view": "today", "page_size": 50})).json()
    assert {str(waiting_id), str(frozen_id)} <= ids(today)
    assert (await admin_client.get("/api/applications", params={"view": "bogus"})).status_code == 422


def test_reason_context_drops_empty_values_and_trailing_zeros():
    """T23: текст причины в карточке — с баллом и порогами решения, без
    «None» и без «629.00» (раньше выходило «…порогом автоодобрения .»)."""
    from decimal import Decimal

    from app.api.applications import _reason_context
    from app.integrations.status_mapping import build_operator_comment

    context = _reason_context(
        score=Decimal("629.00"), auto_approve_above=720.0, auto_reject_below=450.0, required_down_pay=None,
    )
    assert context == {"score": 629, "auto_approve_above": 720, "auto_reject_below": 450}
    text = build_operator_comment(reason_code="SCORE_GRAY_ZONE", context=context)
    assert "629" in text and "450" in text and "720" in text
    assert " ." not in text
