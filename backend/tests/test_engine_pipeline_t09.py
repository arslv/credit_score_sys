"""T09/T16: ранний выход при hard_stop, идемпотентная финализация, заморозка
при санкциях. ФНС в этом файле — только "не вызвана при раннем выходе"
(fire-and-forget с T16, запускается после финализации queue_fns_checks,
проверяем patch'ем, не считая реальные check()-вызовы — тех больше нет в
синхронном пути). ФССП здесь не участвует вообще (T16 — только по действию
оператора, см. tests/test_api_fssp.py). Общие хелперы (_ingest,
_age_overrides, active_ruleset) — из test_engine_pipeline.py, чтобы не
дублировать."""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.config import get_settings
from app.engine.pipeline import finalize_stage_a, run_stage_a
from app.engine.rules import _MISSING, get_field, parse_matrix_key
from app.engine.schema import load_ruleset_from_yaml
from app.engine.state_machine import transition
from app.models import Application, Check, Client, Decision, FeatureSnapshot, SanctionsList
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from tests.test_engine_pipeline import _age_overrides, _ingest, _last_decision, active_ruleset  # noqa: F401
from tests.conftest import unique_valid_passport_series


# --- п.1: ранний выход при локальном hard_stop --------------------------------


async def test_local_hard_stop_skips_external_sources_entirely(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))  # AGE_BELOW_MIN

    with patch("app.engine.pipeline.queue_fns_checks") as mock_queue:
        final_status = await run_stage_a(
            session_factory, application_id,
            ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
        )

    assert final_status == "rejected_a"
    mock_queue.assert_not_called()  # T16: hard_stop -> ни одной внешней проверки, даже фоновой

    async with session_factory() as session:
        checks = (
            await session.execute(select(Check).where(Check.application_id == application_id))
        ).scalars().all()
        assert {c.source for c in checks} == {"mvd", "rosfinmon", "blacklist"}


# --- ФНС — фоновой задачей после финализации (T16) ------------------------------


async def test_fns_checks_queued_after_stage_a_finalizes_without_waiting(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Стадия А не ждёт ФНС (ваше явное указание) — финализируется сразу по
    локальным признакам, задача на ФНС просто ставится в очередь Celery."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))

    with patch("app.engine.pipeline.queue_fns_checks") as mock_queue:
        final_status = await run_stage_a(
            session_factory, application_id,
            ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
        )

    assert final_status == "to_operator"  # решение принято сразу, без ФНС
    mock_queue.assert_called_once()
    _, kwargs = mock_queue.call_args
    assert kwargs["application_id"] == application_id
    assert kwargs["subject_type"] == "applicant"

    async with session_factory() as session:
        # Фоновая задача ничего не записала СИНХРОННО — закономерно, она не
        # была реально выполнена (queue_fns_checks замокан), это и есть суть
        # "не ждём": на момент финализации проверок ФНС ещё нет.
        checks = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.source.like("fns_%"))
            )
        ).scalars().all()
        assert checks == []


# --- п.3: идемпотентная финализация ---------------------------------------------


async def test_finalize_stage_a_concurrent_calls_produce_exactly_one_decision(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """run_stage_a теперь всегда финализирует сразу (T16) — чтобы проверить
    идемпотентность самой finalize_stage_a (SELECT ... FOR UPDATE), заводим
    заявку в статус "stage_a" напрямую в обход run_stage_a, имитируя редкую
    гонку двух конкурентных вызовов финализации одной и той же заявки."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        transition(session, application, "ocr", actor_type="system")
        transition(session, application, "stage_a", actor_type="system")
        await session.commit()

    # OCR должен существовать для finalize_stage_a (читает ocr_results) —
    # прогоняем его напрямую тем же путём, что run_stage_a, без финализации.
    from app.engine.pipeline import _load_photo
    from app.ocr.service import run_ocr

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        passport_main = await _load_photo(session, storage, application_id, "passport_main")
        passport_reg = await _load_photo(session, storage, application_id, "passport_reg")
        await run_ocr(
            session, ocr_provider, application=application, passport_main=passport_main, passport_reg=passport_reg,
        )
        await session.commit()

    await asyncio.gather(
        finalize_stage_a(session_factory, application_id, partner_client=partner_client),
        finalize_stage_a(session_factory, application_id, partner_client=partner_client),
    )

    async with session_factory() as session:
        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert len(decisions) == 1


# --- заморозка при санкциях -----------------------------------------------------


async def test_sanctions_hit_persists_freeze_logs_error_and_notes_operator(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    # Уникальный паспорт — санкционный хит персистит freeze (T09) на весь пул тестов
    # до конца сессии pytest, если бы использовался общий дефолтный паспорт.
    passport_series = unique_valid_passport_series()
    passport_number = f"{uuid.uuid4().int % 1000000:06d}"

    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "last_name": "Магомедов", "first_name": "Магомед", "middle_name": "Магомедович",
                "passport_series": passport_series, "passport_number": passport_number,
            }
        )
    )

    async with session_factory() as session:
        session.add(SanctionsList(
            list_type="te", full_name="Магомедов Магомед Магомедович",
            name_normalized="магомедов магомед магомедович",
            birth_date=None, loaded_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    with patch("app.engine.pipeline.logger") as mock_logger:
        final_status = await run_stage_a(
            session_factory, application_id,
            ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
        )

    assert final_status == "rejected_a"

    error_events = [call.args[0] for call in mock_logger.error.call_args_list]
    assert "sanctions_freeze_triggered" in error_events

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "SANCTIONS_HIT"
        assert decision.freeze is True

    decision_sent = await _last_decision(partner_admin, external_id)
    assert "комплаенс" in decision_sent["reason"].lower()


async def test_repeat_application_of_frozen_passport_is_rejected_without_any_checks(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    series, number = "1205", "555555"  # безопасный год бланка ("05" -> 2005)

    # Первая заявка — санкционный хит, замораживает паспорт.
    external_id_1, application_id_1 = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_1 = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "passport_series": series, "passport_number": number,
                "last_name": "Магомедов", "first_name": "Магомед", "middle_name": "Магомедович",
            }
        )
    )
    async with session_factory() as session:
        session.add(SanctionsList(
            list_type="te", full_name="Магомедов Магомед Магомедович",
            name_normalized="магомедов магомед магомедович",
            birth_date=None, loaded_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    await run_stage_a(
        session_factory, application_id_1,
        ocr_provider=ocr_1, partner_client=partner_client, storage=storage,
    )

    # Вторая заявка — тот же паспорт, другое имя (заявитель мог его сменить/скрыть) —
    # заморозка всё равно бьёт по паспорту, без единой новой проверки.
    external_id_2, application_id_2 = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_2 = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "passport_series": series, "passport_number": number,
                "last_name": "Совсем", "first_name": "Другой", "middle_name": None,
            }
        )
    )
    with patch("app.engine.pipeline.logger") as mock_logger, patch("app.engine.pipeline.queue_fns_checks") as mock_queue:
        final_status = await run_stage_a(
            session_factory, application_id_2,
            ocr_provider=ocr_2, partner_client=partner_client, storage=storage,
        )

    assert final_status == "rejected_a"
    mock_queue.assert_not_called()

    error_events = [call.args[0] for call in mock_logger.error.call_args_list]
    assert "sanctions_freeze_reapplied" in error_events

    async with session_factory() as session:
        checks_2 = (
            await session.execute(select(Check).where(Check.application_id == application_id_2))
        ).scalars().all()
        assert checks_2 == []  # ни одной проверки — ни локальной, ни внешней

        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
        assert decision_2.reason_code == "SANCTIONS_HIT"
        assert decision_2.freeze is True
        assert decision_2.feature_snapshot_id is None

    decision_sent_2 = await _last_decision(partner_admin, external_id_2)
    assert decision_sent_2["status"] == "rejected"
    assert "комплаенс" in decision_sent_2["reason"].lower()


# --- проверка путей полей в dev.yaml против реального снапшота -----------------

# Стадия Б ещё не реализована (T10 — лимиты/шариат/полнота; T11 — БКИ и ПДН):
# эти поля стадия А не заполняет, потому что заполнять их пока нечем. Убирать
# id по мере реализации соответствующей задачи — если тест ниже начнёт падать
# на оставленном здесь id, значит поле уже появилось и исключение устарело.
_NOT_YET_IMPLEMENTED_RULE_IDS = {
    "sharia_goods",  # stage_b_pre_bki -> goods.sharia_allowed, T10
    "declared_pti_too_high",  # stage_b_pre_bki -> declared_pti_exceeds_max, T11 (стадия Б, не А)
    "pti_high",  # soft_rules -> features.pti, T11 (после запроса в БКИ)
}

# Аналогично для полей matrix (условия when в dev.yaml) — сумма сделки и
# ликвидность товара считаются не в стадии А, а при оценке матрицы в стадии Б
# (T10); просрочки по прошлым договорам появятся только с импортом платежей
# (T15). Убирать по мере реализации.
_NOT_YET_IMPLEMENTED_MATRIX_FIELDS = {"amount", "goods_liquidity", "prior_defaults"}


async def test_dev_ruleset_rule_field_paths_all_resolve_against_real_snapshot(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Ловит класс бага T09 (fssp_credit_debt/fssp_minor ссылались на путь,
    который признаки никогда не заполняли, — правило тихо никогда не
    срабатывало) автоматически: гоняет реальную стадию А с максимально
    заполненными признаками (включая непустой ответ ФССП — T16, заранее
    записанный в checks, как если бы оператор уже прошёл капчу до этого) и
    проверяет для каждого правила из dev.yaml, что его field реально
    разрешается в получившемся снапшоте, а не просто отсутствует."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            Check(
                application_id=application_id, subject_type="applicant", source="fssp", status="hit",
                parsed={"productions": [{"subject": "Взыскание по кредитному договору", "amount": "10000"}]},
                started_at=now, finished_at=now, duration_ms=0,
            )
        )
        await session.commit()

    with patch("app.engine.pipeline.queue_fns_checks"):
        await run_stage_a(
            session_factory, application_id,
            ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
        )

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
    features = snapshot.features

    config = load_ruleset_from_yaml(Path(get_settings().ruleset_path))
    all_rules = [*config.hard_stops, *config.stage_b_pre_bki, *config.soft_rules]
    all_rule_ids = {rule.id for rule in all_rules}

    # Защита от опечатки/забытого id в самом списке исключений.
    assert _NOT_YET_IMPLEMENTED_RULE_IDS <= all_rule_ids

    unresolved = [
        rule.id
        for rule in all_rules
        if rule.id not in _NOT_YET_IMPLEMENTED_RULE_IDS and get_field(features, rule.field) is _MISSING
    ]
    assert unresolved == [], f"правила ссылаются на несуществующие в снапшоте поля: {unresolved}"

    # matrix: условия when — отдельная структура (нет id, несколько полей на
    # запись), поэтому проверяется отдельно тем же способом.
    matrix_unresolved = [
        key
        for entry in config.matrix
        for key in entry.when
        if parse_matrix_key(key)[0] not in _NOT_YET_IMPLEMENTED_MATRIX_FIELDS
        and get_field(features, parse_matrix_key(key)[0]) is _MISSING
    ]
    assert matrix_unresolved == [], f"matrix.when ссылается на несуществующие в снапшоте поля: {matrix_unresolved}"


# --- подключение clients (T09) --------------------------------------------------


async def test_two_applications_same_passport_link_to_same_client(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    series, number = unique_valid_passport_series(), f"{uuid.uuid4().int % 1000000:06d}"

    _, application_id_1 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_1,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(30), "passport_series": series, "passport_number": number,
        })),
        partner_client=partner_client, storage=storage,
    )

    _, application_id_2 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_2,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(30), "passport_series": series, "passport_number": number,
        })),
        partner_client=partner_client, storage=storage,
    )

    async with session_factory() as session:
        app_1 = await session.get(Application, application_id_1)
        app_2 = await session.get(Application, application_id_2)

        assert app_1.client_id is not None
        assert app_1.client_id == app_2.client_id

        clients = (await session.execute(select(Client))).scalars().all()
        assert len(clients) == 1  # ровно один клиент, не два


async def test_ocr_failed_does_not_create_or_link_client(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(confidence=0.99, confidence_overrides={"passport_series": 0.1}))

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )
    assert final_status == "to_operator"

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        assert application.client_id is None

        clients = (await session.execute(select(Client))).scalars().all()
        assert clients == []


async def test_same_name_and_birth_date_different_passport_links_to_same_client_and_flags_change(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    birth_date = _age_overrides(30)["birth_date"]

    _, application_id_1 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_1,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(30),
            "passport_series": unique_valid_passport_series(),
            "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
        })),
        partner_client=partner_client, storage=storage,
    )

    _, application_id_2 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_2,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(30),
            "passport_series": unique_valid_passport_series(),  # новый паспорт — переоформление
            "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
        })),
        partner_client=partner_client, storage=storage,
    )

    async with session_factory() as session:
        app_1 = await session.get(Application, application_id_1)
        app_2 = await session.get(Application, application_id_2)
        assert app_1.client_id == app_2.client_id  # тот же человек, найден по ФИО+ДР

        clients = (await session.execute(select(Client))).scalars().all()
        assert len(clients) == 1

        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
        snapshot_2 = await session.get(FeatureSnapshot, decision_2.feature_snapshot_id)
        assert snapshot_2.features["client_passport_changed"] is True


async def test_freeze_persists_for_same_client_across_passport_change(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Ключевая новая возможность T09: заморозка теперь по клиенту, не только
    по паспорту — тот же человек с новым (переоформленным) паспортом всё равно
    остаётся заблокирован, потому что находится по ФИО+ДР и привязывается к
    тому же client_id, что и в момент заморозки."""
    last_name, first_name, middle_name = "Магомедов", "Магомед", "Магомедович"

    async with session_factory() as session:
        session.add(SanctionsList(
            list_type="te", full_name=f"{last_name} {first_name} {middle_name}",
            name_normalized="магомедов магомед магомедович",
            birth_date=None, loaded_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    _, application_id_1 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_1,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(30),
            "last_name": last_name, "first_name": first_name, "middle_name": middle_name,
            "passport_series": unique_valid_passport_series(),
            "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
        })),
        partner_client=partner_client, storage=storage,
    )

    # Новая заявка — то же ФИО+ДР, но СОВСЕМ ДРУГОЙ паспорт (переоформление).
    _, application_id_2 = await _ingest(partner_admin, partner_client, storage, session_factory)
    with patch("app.engine.pipeline.queue_fns_checks") as mock_queue:
        final_status = await run_stage_a(
            session_factory, application_id_2,
            ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
                **_age_overrides(30),
                "last_name": last_name, "first_name": first_name, "middle_name": middle_name,
                "passport_series": unique_valid_passport_series(),
                "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
            })),
            partner_client=partner_client, storage=storage,
        )

    assert final_status == "rejected_a"
    mock_queue.assert_not_called()  # заморозка сработала раньше внешних проверок

    async with session_factory() as session:
        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
        assert decision_2.reason_code == "SANCTIONS_HIT"
        assert decision_2.freeze is True


async def test_is_repeat_and_prior_counts_computed_from_client_history(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    series, number = unique_valid_passport_series(), f"{uuid.uuid4().int % 1000000:06d}"

    # Первая заявка клиента: отказ по возрасту — считается как "прошлый отказ".
    _, application_id_1 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_1,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(19), "passport_series": series, "passport_number": number,
        })),
        partner_client=partner_client, storage=storage,
    )

    # Вторая заявка того же клиента (тот же паспорт) — чистая.
    _, application_id_2 = await _ingest(partner_admin, partner_client, storage, session_factory)
    await run_stage_a(
        session_factory, application_id_2,
        ocr_provider=FakeOcrProvider(build_ocr_result(overrides={
            **_age_overrides(30), "passport_series": series, "passport_number": number,
        })),
        partner_client=partner_client, storage=storage,
    )

    async with session_factory() as session:
        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
        snapshot_2 = await session.get(FeatureSnapshot, decision_2.feature_snapshot_id)
        assert snapshot_2.features["is_repeat"] is True
        assert snapshot_2.features["prior_applications_count"] == 1
        assert snapshot_2.features["prior_rejections_count"] == 1
