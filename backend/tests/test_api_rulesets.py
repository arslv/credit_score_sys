"""T14: редактор правил и симуляция.

Не гоняет реальный run_stage_a/run_stage_b — сеет Application/FeatureSnapshot
напрямую, симуляция сама работает только со снимками
(app.engine.ruleset_simulation), без похода в pipeline."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import yaml
from sqlalchemy import func, select

from app.engine.versions import get_active_ruleset_version, load_config
from app.models import Application, Decision, FeatureSnapshot, RulesetSimulation, RulesetVersion
from tests._admin_helpers import create_employee, login
from tests.test_engine_pipeline import active_ruleset  # noqa: F401


def _dump_yaml(config) -> str:
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


async def _application(session, *, external_id: str, client_id=None, status: str = "rejected_a") -> Application:
    now = datetime.now(timezone.utc)
    application = Application(
        external_id=external_id, status=status,
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={}, consent_pd_at=now, submitted_at=now, client_id=client_id,
    )
    session.add(application)
    await session.flush()
    return application


async def _snapshot(session, application_id, stage: str, features: dict) -> FeatureSnapshot:
    fs = FeatureSnapshot(
        application_id=application_id, stage=stage, features=features, feature_set_ver="test-v1",
    )
    session.add(fs)
    await session.flush()
    return fs


_STAGE_A_PASS_FEATURES = {
    "age": 30,
    "checks": {"mvd": {"status": "ok"}, "rosfinmon": {"status": "ok"}, "blacklist": {"status": "ok"}},
    "features": {"fssp_credit_debt_amount": 0},
}
_STAGE_A_REJECT_FEATURES = {**_STAGE_A_PASS_FEATURES, "age": 19}
_STAGE_B_APPROVE_FEATURES = {
    "goods": {"sharia_allowed": True},
    "score": 780,
    "pti": 0.30,
}


async def _counts(session_factory) -> tuple[int, int]:
    async with session_factory() as session:
        decisions = (await session.execute(select(func.count()).select_from(Decision))).scalar_one()
        snapshots = (await session.execute(select(func.count()).select_from(FeatureSnapshot))).scalar_one()
    return decisions, snapshots


async def test_simulate_does_not_write_decisions_or_feature_snapshots(
    admin_client, session_factory, active_ruleset
):
    await create_employee(session_factory, email="rm1@example.com", role="risk_manager")
    await login(admin_client, "rm1@example.com")

    async with session_factory() as session:
        app1 = await _application(session, external_id=f"SIM-1-{uuid.uuid4().hex[:6]}")
        await _snapshot(session, app1.id, "A", _STAGE_A_PASS_FEATURES)
        await _snapshot(session, app1.id, "B", _STAGE_B_APPROVE_FEATURES)
        await session.commit()
        config = load_config(active_ruleset)

    before = await _counts(session_factory)
    response = await admin_client.post("/api/rulesets/simulate", json={"yaml": _dump_yaml(config)})
    assert response.status_code == 200, response.text
    after = await _counts(session_factory)
    assert before == after


async def test_not_computable_reports_missing_feature_separately(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm2@example.com", role="risk_manager")
    await login(admin_client, "rm2@example.com")

    async with session_factory() as session:
        app1 = await _application(session, external_id=f"SIM-2-{uuid.uuid4().hex[:6]}")
        await _snapshot(session, app1.id, "A", _STAGE_A_PASS_FEATURES)  # нет checks.new_source вовсе
        await session.commit()
        config = load_config(active_ruleset)

    raw = config.model_dump(mode="json")
    raw["hard_stops"].append(
        {"id": "new_source_hit", "field": "checks.new_source.status", "op": "eq", "value": "hit", "reason": "NEW_SOURCE_HIT"}
    )
    candidate_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)

    response = await admin_client.post("/api/rulesets/simulate", json={"yaml": candidate_yaml})
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]

    not_computable = {row["rule_id"]: row for row in summary["not_computable"]}
    assert "new_source_hit" in not_computable
    assert not_computable["new_source_hit"]["count"] == 1
    assert str(app1.id) in not_computable["new_source_hit"]["application_ids"]
    # Не вычислимое правило не должно тихо посчитаться как "не сработавшее" —
    # раз оно не резолвится, кандидат по этой заявке не должен отличаться от
    # активной версии ТОЛЬКО из-за этого нового правила (обе стороны согласны:
    # заявка проходит стадию А).
    assert summary["changed_count"] == 0


async def test_unknown_after_stage_a_pass_bucket(admin_client, session_factory, active_ruleset):
    """Уточнение 2 (ваше явное указание): отклонена на А активной версией, по
    кандидату прошла бы — снимка стадии Б нет — отдельная категория, не «swap»."""
    await create_employee(session_factory, email="rm3@example.com", role="risk_manager")
    await login(admin_client, "rm3@example.com")

    async with session_factory() as session:
        app1 = await _application(session, external_id=f"SIM-3-{uuid.uuid4().hex[:6]}")
        await _snapshot(session, app1.id, "A", _STAGE_A_REJECT_FEATURES)  # age=19 < age_min(21) в dev.yaml
        await session.commit()
        config = load_config(active_ruleset)

    raw = config.model_dump(mode="json")
    for rule in raw["hard_stops"]:
        if rule["id"] == "age_min":
            rule["value"] = 18  # кандидат снимает возрастной стоп для этой заявки
    candidate_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)

    response = await admin_client.post("/api/rulesets/simulate", json={"yaml": candidate_yaml})
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]

    assert summary["unknown_after_stage_a_pass_total"] == 1
    ids = [row["application_id"] for row in summary["unknown_after_stage_a_pass"]]
    assert str(app1.id) in ids
    assert summary["changed_count"] == 0  # не в основной сводке (уточнение 2)


async def test_defaultness_by_known_outcome_uses_mature_contracts(admin_client, session_factory, active_ruleset):
    """T15: дефолтность в симуляции — реальный расчёт по зрелым договорам
    (target_bad_60_6), не заглушка."""
    from app.models import Contract, ImportBatch, Payment, PaymentSchedule

    await create_employee(session_factory, email="rm-def@example.com", role="risk_manager")
    await login(admin_client, "rm-def@example.com")
    employee_id = await create_employee(session_factory, email="rm-def2@example.com", role="risk_manager")

    async with session_factory() as session:
        app1 = await _application(session, external_id=f"SIM-DEF-{uuid.uuid4().hex[:6]}")
        await _snapshot(session, app1.id, "A", _STAGE_A_PASS_FEATURES)
        await _snapshot(session, app1.id, "B", _STAGE_B_APPROVE_FEATURES)  # score=780 -> approve по dev.yaml

        contract = Contract(
            application_id=app1.id, external_number=f"DEF-{uuid.uuid4().hex[:8]}",
            principal=Decimal("6000"), markup=Decimal("0"), total_amount=Decimal("6000"),
            down_payment=Decimal("0"), term_months=6, issued_at=date(2025, 1, 1), status="active",
        )
        session.add(contract)
        await session.flush()
        for n in range(1, 7):
            month = 1 + n
            year = 2025 + (month - 1) // 12
            month = (month - 1) % 12 + 1
            due = date(year, month, 1)
            session.add(PaymentSchedule(contract_id=contract.id, period_no=n, due_date=due, due_amount=Decimal("1000")))
            session.add(Payment(contract_id=contract.id, paid_at=due, amount=Decimal("1000"), source="import", kind="regular"))
        session.add(
            ImportBatch(
                kind="payments", filename="x.csv", file_sha256=uuid.uuid4().hex.ljust(64, "0"), storage_key="x",
                uploaded_by=employee_id, status="completed", data_as_of=date(2026, 1, 1),
            )
        )
        await session.commit()
        config = load_config(active_ruleset)

    from app.engine.delinquency_recompute import recompute_all_contracts

    async with session_factory() as session:
        await recompute_all_contracts(session, upto_date=date(2026, 1, 1))
        await session.commit()

    response = await admin_client.post("/api/rulesets/simulate", json={"yaml": _dump_yaml(config)})
    assert response.status_code == 200, response.text
    defaultness = response.json()["summary"]["defaultness_by_known_outcome"]

    assert defaultness["available"] is True
    assert defaultness["by_candidate_outcome"]["approved"] == {"known": 1, "bad": 0, "bad_rate": 0.0}


async def _simulate(client, config) -> dict:
    response = await client.post("/api/rulesets/simulate", json={"yaml": _dump_yaml(config)})
    response.raise_for_status()
    return response.json()


async def test_save_without_matching_simulation_rejected(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm4@example.com", role="risk_manager")
    await login(admin_client, "rm4@example.com")
    config = load_config(active_ruleset)

    response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": _dump_yaml(config), "simulation_id": str(uuid.uuid4()), "comment": "тест: публикация для проверки"}
    )
    assert response.status_code == 400


async def test_save_with_changed_yaml_after_simulation_rejected(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm5@example.com", role="risk_manager")
    await login(admin_client, "rm5@example.com")
    config = load_config(active_ruleset)

    result = await _simulate(admin_client, config)

    # Меняем САМО ПРАВИЛО, не комментарий/дату — хеш (T14, ваше замечание п.1)
    # считается только по правилам, comment/effective_from из него исключены.
    raw = config.model_dump(mode="json")
    for rule in raw["hard_stops"]:
        if rule["id"] == "age_min":
            rule["value"] = rule["value"] + 1
    changed_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)

    response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": changed_yaml, "simulation_id": result["simulation_id"], "comment": "тест: публикация для проверки"}
    )
    assert response.status_code == 400


async def test_save_hash_ignores_comment_and_effective_from(admin_client, session_factory, active_ruleset):
    """T14, ваше замечание п.1: хеш — только по правилам. Поменять comment/
    effective_from после симуляции и всё равно сохранить ТЕМ ЖЕ simulation_id
    должно быть можно — иначе любая правка даты вступления или текста
    комментария вынуждала бы пересимулировать то, что не влияет на решения."""
    await create_employee(session_factory, email="rm5b@example.com", role="risk_manager")
    await login(admin_client, "rm5b@example.com")
    config = load_config(active_ruleset)

    result = await _simulate(admin_client, config)

    raw = config.model_dump(mode="json")
    raw["comment"] = "другой комментарий, те же правила"
    raw["effective_from"] = date.today().isoformat()
    same_rules_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)

    response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": same_rules_yaml, "simulation_id": result["simulation_id"], "comment": "тест: публикация для проверки"}
    )
    assert response.status_code == 200, response.text


async def test_save_with_stale_active_version_rejected(admin_client, session_factory, active_ruleset):
    """Уточнение 3 (ваше явное указание): активная версия поменялась между
    симуляцией и сохранением (кто-то другой сохранил) — 409, не тихая запись."""
    await create_employee(session_factory, email="rm6@example.com", role="risk_manager")
    await login(admin_client, "rm6@example.com")
    config = load_config(active_ruleset)

    result = await _simulate(admin_client, config)

    # Кто-то другой успел сохранить версию, пока эта симуляция "думала".
    async with session_factory() as session:
        from app.engine.versions import save_ruleset_version

        other_config = config.model_copy(update={"effective_from": date.today()})
        await save_ruleset_version(session, other_config, author_id=None)
        await session.commit()

    response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": _dump_yaml(config), "simulation_id": result["simulation_id"], "comment": "тест: публикация для проверки"}
    )
    assert response.status_code == 409
    assert "повторите симуляцию" in response.json()["detail"]


async def test_save_with_expired_simulation_rejected(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm7@example.com", role="risk_manager")
    await login(admin_client, "rm7@example.com")
    config = load_config(active_ruleset)

    result = await _simulate(admin_client, config)

    async with session_factory() as session:
        simulation = await session.get(RulesetSimulation, uuid.UUID(result["simulation_id"]))
        simulation.requested_at = datetime.now(timezone.utc) - timedelta(hours=48)
        await session.commit()

    response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": _dump_yaml(config), "simulation_id": result["simulation_id"], "comment": "тест: публикация для проверки"}
    )
    assert response.status_code == 409
    assert "устарела" in response.json()["detail"]


async def test_save_creates_new_active_version_and_writes_audit(admin_client, session_factory, active_ruleset):
    employee_id = await create_employee(session_factory, email="rm8@example.com", role="risk_manager")
    await login(admin_client, "rm8@example.com")
    config = load_config(active_ruleset)

    raw = config.model_dump(mode="json")
    raw["comment"] = "новая версия для сохранения"
    raw["effective_from"] = date.today().isoformat()
    new_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)

    sim_response = await admin_client.post("/api/rulesets/simulate", json={"yaml": new_yaml})
    assert sim_response.status_code == 200, sim_response.text
    simulation_id = sim_response.json()["simulation_id"]

    save_response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": new_yaml, "simulation_id": simulation_id, "comment": "тест: публикация для проверки"}
    )
    assert save_response.status_code == 200, save_response.text
    new_version = save_response.json()["version"]

    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        assert active.version == new_version
        assert active.author_id == employee_id

        from app.models import AuditLog

        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "change_ruleset", AuditLog.object_id == active.id)
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].details["op"] == "save"


async def test_rollback_requires_nonempty_reason(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm9@example.com", role="risk_manager")
    await login(admin_client, "rm9@example.com")

    response = await admin_client.post(
        "/api/rulesets/rollback", json={"version": active_ruleset.version, "reason": "   "}
    )
    assert response.status_code == 400


async def _save_version_with_different_rules(session_factory, active_ruleset) -> int:
    """Версия, правила которой ОТЛИЧАЮТСЯ от текущего файла на диске — не
    только датой/комментарием. После сужения хеша (T14, ваше замечание п.1)
    откат к версии, совпадающей с файлом по правилам, больше не считается
    конфликтом (и это правильно) — тестам на конфликт нужна версия, где
    отличие настоящее."""
    from app.engine.schema import parse_ruleset
    from app.engine.versions import compute_config_hash, save_ruleset_version

    config = load_config(active_ruleset)
    raw = config.model_dump(mode="json")
    for rule in raw["hard_stops"]:
        if rule["id"] == "age_min":
            rule["value"] = rule["value"] + 4
    modified_config = parse_ruleset(raw)

    async with session_factory() as session:
        row = await save_ruleset_version(
            session, modified_config, author_id=None, config_hash=compute_config_hash(modified_config),
        )
        await session.commit()
        return row.version


async def test_rollback_creates_copy_and_causes_file_conflict_on_next_sync(
    admin_client, session_factory, active_ruleset
):
    employee_id = await create_employee(session_factory, email="rm10@example.com", role="risk_manager")
    await login(admin_client, "rm10@example.com")
    target_version = await _save_version_with_different_rules(session_factory, active_ruleset)

    response = await admin_client.post(
        "/api/rulesets/rollback",
        json={"version": target_version, "reason": "проверка регрессии по П-42"},
    )
    assert response.status_code == 200, response.text
    new_version_number = response.json()["version"]

    async with session_factory() as session:
        new_row = (
            await session.execute(select(RulesetVersion).where(RulesetVersion.version == new_version_number))
        ).scalar_one()
        target_row = (
            await session.execute(select(RulesetVersion).where(RulesetVersion.version == target_version))
        ).scalar_one()

        assert new_row.author_id == employee_id
        assert f"Откат к версии {target_version}" in new_row.comment
        assert "проверка регрессии по П-42" in new_row.comment

        new_config = load_config(new_row)
        target_config = load_config(target_row)
        # Правила — точная копия версии-цели; effective_from — не переносится
        # буквально (ваше решение: откат должен подействовать сейчас).
        assert new_config.hard_stops == target_config.hard_stops
        assert new_config.soft_rules == target_config.soft_rules
        assert new_config.limits == target_config.limits

        active = await get_active_ruleset_version(session)
        assert active.id == new_row.id  # откат реально стал активной версией

    # Следствие, которое вы подтвердили: у откатной версии есть автор, а её
    # правила реально отличаются от файла — значит следующая синхронизация с
    # файлом увидит конфликт (существующая логика sync_ruleset_from_file),
    # а не тихо перезапишет её.
    from app.config import get_settings
    from app.engine.versions import RulesetSyncResult, sync_ruleset_from_file

    result = await sync_ruleset_from_file(session_factory, get_settings().ruleset_path)
    assert result == RulesetSyncResult.CONFLICT


async def test_versions_endpoint_shows_file_conflict_after_rollback(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm11@example.com", role="risk_manager")
    await login(admin_client, "rm11@example.com")
    target_version = await _save_version_with_different_rules(session_factory, active_ruleset)

    await admin_client.post(
        "/api/rulesets/rollback", json={"version": target_version, "reason": "тест конфликта с файлом"}
    )

    response = await admin_client.get("/api/rulesets/versions")
    assert response.status_code == 200
    body = response.json()
    assert body["file_conflict"]["conflict"] is True


async def test_versions_endpoint_shows_no_conflict_when_active_matches_file(
    admin_client, session_factory, active_ruleset
):
    """T14, ваше замечание п.1/3: если активная версия совпадает с файлом по
    правилам — конфликта нет вовсе (плашки быть не должно)."""
    await create_employee(session_factory, email="rm11b@example.com", role="risk_manager")
    await login(admin_client, "rm11b@example.com")

    response = await admin_client.get("/api/rulesets/versions")
    assert response.status_code == 200
    assert response.json()["file_conflict"]["conflict"] is False


async def test_accept_file_sets_author_none_and_is_idempotent(admin_client, session_factory, active_ruleset):
    """Найденный баг (T14, ваше замечание п.1): раньше "Принять версию из
    файла" ставило author_id сотрудника, который нажал кнопку — каждое
    нажатие создавало версию, которая сама на следующей проверке считалась
    конфликтующей с файлом, и плодились версии (10 версий после нескольких
    нажатий). Проверяем весь ваш сценарий: принять из файла -> matches_file
    true -> "перезапуск api" (sync_ruleset_from_file) -> UNCHANGED, без новых
    версий -> повторное нажатие кнопки не создаёт версию."""
    from app.models import AuditLog

    await create_employee(session_factory, email="rm12@example.com", role="risk_manager")
    await login(admin_client, "rm12@example.com")

    # Активная версия сейчас отличается от файла правилами — есть что принимать.
    target_version = await _save_version_with_different_rules(session_factory, active_ruleset)
    assert target_version != active_ruleset.version

    response = await admin_client.post("/api/rulesets/accept-file")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    new_version_number = body["version"]

    async with session_factory() as session:
        new_row = (
            await session.execute(select(RulesetVersion).where(RulesetVersion.version == new_version_number))
        ).scalar_one()
        assert new_row.author_id is None  # версия из файла — не "от человека"

        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "change_ruleset", AuditLog.object_id == new_row.id)
            )
        ).scalars().all()
        assert len(audit_rows) == 1  # нажатие — в аудит, даже с author_id=None на самой версии
        assert audit_rows[0].details["op"] == "accept_file"

    versions_response = await admin_client.get("/api/rulesets/versions")
    assert versions_response.json()["file_conflict"]["conflict"] is False  # matches_file: true

    # "Перезапуск api" — та же проверка, что делает lifespan при старте.
    from app.config import get_settings
    from app.engine.versions import RulesetSyncResult, sync_ruleset_from_file

    sync_result = await sync_ruleset_from_file(session_factory, get_settings().ruleset_path)
    assert sync_result == RulesetSyncResult.UNCHANGED

    async with session_factory() as session:
        count_after_restart = (
            await session.execute(select(func.count()).select_from(RulesetVersion))
        ).scalar_one()

    # Повторное нажатие — no-op, ничего не создаёт.
    second_response = await admin_client.post("/api/rulesets/accept-file")
    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["created"] is False

    async with session_factory() as session:
        count_after_second_click = (
            await session.execute(select(func.count()).select_from(RulesetVersion))
        ).scalar_one()

    assert count_after_second_click == count_after_restart


def test_row_to_dict_redacts_client_name_without_full_pii():
    """Экран теперь только для risk_manager (у которого ПДн всегда есть), но
    сама функция редактирования остаётся — проверяем её напрямую, не через
    маршрут, раз через маршрут эту ветку больше никто не может получить."""
    from app.api.rulesets import _row_to_dict
    from app.engine.ruleset_simulation import ApplicationSimulationRow

    row = ApplicationSimulationRow(
        application_id=uuid.uuid4(), external_id="APP-1", client_name="Тестова Клиентова",
        active_outcome="rejected_b", active_reason_code=None,
        candidate_outcome="approved", candidate_reason_code=None,
        actual_decision_outcome=None, actual_decision_reason_code=None,
    )
    assert _row_to_dict(row, full_pii=False)["client_name"] is None
    assert _row_to_dict(row, full_pii=True)["client_name"] == "Тестова Клиентова"


async def test_admin_forbidden_from_viewing_rulesets(admin_client, session_factory, active_ruleset):
    """T14, ваше замечание п.2: «Правила и пороги» — только risk_manager
    (ТЗ, карта экранов), admin допускался сюда по ошибке — исправлено."""
    await create_employee(session_factory, email="admin14@example.com", role="admin")
    await login(admin_client, "admin14@example.com")

    assert (await admin_client.get("/api/rulesets/active")).status_code == 403
    assert (await admin_client.get("/api/rulesets/versions")).status_code == 403


async def test_admin_forbidden_from_simulating(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="admin15@example.com", role="admin")
    await login(admin_client, "admin15@example.com")
    config = load_config(active_ruleset)

    response = await admin_client.post("/api/rulesets/simulate", json={"yaml": _dump_yaml(config)})
    assert response.status_code == 403


async def test_admin_forbidden_from_saving(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="admin16@example.com", role="admin")
    await login(admin_client, "admin16@example.com")
    config = load_config(active_ruleset)

    response = await admin_client.post(
        "/api/rulesets/save", json={"yaml": _dump_yaml(config), "simulation_id": str(uuid.uuid4()), "comment": "тест: публикация для проверки"}
    )
    assert response.status_code == 403


async def test_admin_forbidden_from_rollback(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="admin17@example.com", role="admin")
    await login(admin_client, "admin17@example.com")

    response = await admin_client.post(
        "/api/rulesets/rollback", json={"version": active_ruleset.version, "reason": "test"}
    )
    assert response.status_code == 403


async def test_admin_forbidden_from_accepting_file(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="admin18@example.com", role="admin")
    await login(admin_client, "admin18@example.com")

    response = await admin_client.post("/api/rulesets/accept-file")
    assert response.status_code == 403


async def test_save_requires_nonblank_comment(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm9@example.com", role="risk_manager")
    await login(admin_client, "rm9@example.com")
    config = load_config(active_ruleset)
    yaml_text = _dump_yaml(config)

    sim = await admin_client.post("/api/rulesets/simulate", json={"yaml": yaml_text})
    simulation_id = sim.json()["simulation_id"]

    for bad in ("", "   "):
        response = await admin_client.post(
            "/api/rulesets/save", json={"yaml": yaml_text, "simulation_id": simulation_id, "comment": bad}
        )
        assert response.status_code == 422, bad
    missing = await admin_client.post("/api/rulesets/save", json={"yaml": yaml_text, "simulation_id": simulation_id})
    assert missing.status_code == 422


async def test_save_stores_publish_comment_as_version_comment(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm10@example.com", role="risk_manager")
    await login(admin_client, "rm10@example.com")
    config = load_config(active_ruleset)
    raw = config.model_dump(mode="json")
    raw["comment"] = "в файле другой комментарий"
    raw["effective_from"] = date.today().isoformat()
    new_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)

    sim = await admin_client.post("/api/rulesets/simulate", json={"yaml": new_yaml})
    save = await admin_client.post(
        "/api/rulesets/save",
        json={"yaml": new_yaml, "simulation_id": sim.json()["simulation_id"], "comment": "  Поднял порог отказа  "},
    )
    assert save.status_code == 200, save.text

    versions = (await admin_client.get("/api/rulesets/versions")).json()["versions"]
    assert versions[0]["comment"] == "Поднял порог отказа"


async def test_versions_list_describes_typed_changes_between_versions(admin_client, session_factory, active_ruleset):
    await create_employee(session_factory, email="rm11@example.com", role="risk_manager")
    await login(admin_client, "rm11@example.com")
    base = load_config(active_ruleset).model_dump(mode="json")
    base["effective_from"] = date.today().isoformat()
    base["score_thresholds"]["auto_reject_below"] = 460
    new_yaml = yaml.safe_dump(base, sort_keys=False, allow_unicode=True)

    sim = await admin_client.post("/api/rulesets/simulate", json={"yaml": new_yaml})
    save = await admin_client.post(
        "/api/rulesets/save",
        json={"yaml": new_yaml, "simulation_id": sim.json()["simulation_id"], "comment": "подняли порог отказа"},
    )
    assert save.status_code == 200, save.text

    versions = (await admin_client.get("/api/rulesets/versions")).json()["versions"]
    latest = versions[0]
    assert "Порог отказа (балл БКИ): 450 → 460" in latest["changes"]
    assert versions[-1]["changes"] == []  # самая ранняя версия — без предыдущей, сравнивать не с чем


async def test_simulation_applies_installment_pti_to_pre_t21_snapshots(admin_client, session_factory, active_ruleset):
    """T21: ПДН теперь включает платёж по нашей рассрочке — изменение формулы,
    а не порога. Снимки стадии Б до T21 посчитаны без него, поэтому симуляция
    кандидата С наценками тарифов досчитывает платёж (по сделке и доходу из
    снимка анкеты) — иначе «проверить на истории» показал бы 0 изменений.
    Активная версия без наценок (как до T21) считает по снимку как есть.

    ПДН 52% + 3 750 ₽ ((50 000 − 5 000) / 12, наценка 0) / 100 000 = 55,75% —
    выше pti_high (55%): одобрение -> ручная проверка."""
    import hashlib
    import json

    from app.engine.schema import parse_ruleset
    from app.engine.versions import save_ruleset_version
    from app.models import ExternalSnapshot

    await create_employee(session_factory, email="rm_t21@example.com", role="risk_manager")
    await login(admin_client, "rm_t21@example.com")

    candidate = load_config(active_ruleset)
    raw_without_markups = candidate.model_dump(mode="json")
    for tariff in raw_without_markups["tariffs"].values():
        tariff["markup_pct"] = None
    raw_without_markups["effective_from"] = date.today().isoformat()

    async with session_factory() as session:
        await save_ruleset_version(session, parse_ruleset(raw_without_markups))
        application = await _application(session, external_id=f"SIM-T21-{uuid.uuid4().hex[:6]}", status="approved")
        await _snapshot(session, application.id, "A", _STAGE_A_PASS_FEATURES)
        await _snapshot(
            session, application.id, "B",
            {"goods": {"sharia_allowed": True}, "score": 780, "amount": 45000.0, "is_repeat": False,
             "declared_pti": 0.2, "declared_pti_exceeds_max": False, "features": {"pti": 0.52}},
        )
        payload_raw = {"stage_b": {"deal": {"tariff": "standard"}, "income": {"declared_monthly": "100000"}}}
        session.add(
            ExternalSnapshot(
                application_id=application.id, stage="B", payload_raw=payload_raw,
                content_hash=hashlib.sha256(json.dumps(payload_raw, sort_keys=True).encode()).hexdigest(),
            )
        )
        await session.commit()

    summary = await _simulate(admin_client, candidate)
    summary = summary["summary"]
    swapped_out = {row["application_id"]: row for row in summary["swap_out"]}
    assert str(application.id) in swapped_out
    assert swapped_out[str(application.id)]["active_outcome"] == "approved"
    assert swapped_out[str(application.id)]["candidate_outcome"] == "manual_review"
    assert swapped_out[str(application.id)]["candidate_reason_code"] == "PTI_HIGH"
