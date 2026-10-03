import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest_asyncio
import yaml
from sqlalchemy import select

from app.config import get_settings
from app.engine.pipeline import DocumentNotFoundError, run_stage_a
from app.engine.schema import parse_ruleset
from app.engine.versions import save_ruleset_version
from app.integrations.adapter import sync_applications
from app.models import Application, Decision, FeatureSnapshot, InvalidPassport, SanctionsList
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.registries.sync import is_registry_stale
from app.sources.name_normalize import full_name, normalize_name
from tests.conftest import unique_valid_passport_series


def _unique_name() -> str:
    return f"Тест-{uuid.uuid4().hex[:8]}"


async def _create_partner_application(partner_admin, **overrides) -> str:
    payload = {"last_name": _unique_name(), "first_name": "Клиент", **overrides}
    response = await partner_admin.post("/_test/applications", json=payload)
    response.raise_for_status()
    return response.json()["id"]


async def _ingest(partner_admin, partner_client, storage, session_factory, **overrides) -> tuple[str, "uuid.UUID"]:
    external_id = await _create_partner_application(partner_admin, **overrides)
    await sync_applications(session_factory, partner_client, storage)
    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        return external_id, application.id


def _age_overrides(age: int) -> dict:
    """birth_date + правдоподобный issue_date (выдан в 14..25 лет, не в будущем) —
    чтобы passport_expired/passport_issue_date_invalid (T08) не срабатывали в
    тестах, которые проверяют что-то другое."""
    today = date.today()
    issue_age = max(14, min(age - 1, 25))
    return {
        "birth_date": today.replace(year=today.year - age).isoformat(),
        "issue_date": today.replace(year=today.year - (age - issue_age)).isoformat(),
    }


@pytest_asyncio.fixture
async def active_ruleset(session_factory):
    """Активная версия конфига из config/rulesets/dev.yaml, но с effective_from
    гарантированно в прошлом — независимо от того, что записано в самом файле."""
    path = Path(get_settings().ruleset_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["effective_from"] = (date.today() - timedelta(days=1)).isoformat()
    config = parse_ruleset(raw)

    async with session_factory() as session:
        row = await save_ruleset_version(session, config)
        await session.commit()
    return row


async def test_stage_a_rejects_underage_applicant(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))
    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "rejected_a"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "reject"
        assert decision.reason_code == "AGE_BELOW_MIN"
        assert decision.stage == "A"

        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["age"] == 19

    detail = await partner_client.get_application(external_id)
    assert detail.status == "rejected"
    comment = detail.raw["comments"][-1]["text"]
    # T11: комментарий — человеческим языком с фактическим значением и порогом,
    # без служебного кода (оператор не программист).
    assert "AGE_BELOW_MIN" not in comment


async def test_stage_a_does_not_reprocess_after_decision(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Тот же вопрос, что и для стадии Б (T13c, ваше уточнение) — покрыть
    тестом, даже если защита уже есть.

    Подбор задачи — по status=="received", а в этот статус ничего не
    возвращается (ALLOWED_TRANSITIONS: app/engine/state_machine.py —
    "received" ни разу не встречается целью перехода): после решения
    диспетчерский запрос `WHERE status = 'received'` эту заявку больше не
    находит — в отличие от стадии Б, где gray_zone/to_operator переиспользуются.
    Не гоняем сам app.tasks.stage_a._run() (он же celery-задача) — у него
    свои настройки/хранилище на весь процесс, не тестовые фикстуры, и запрос
    global — подхватил бы "received"-заявки других тестов; вместо этого
    проверяем оба слоя защиты напрямую: диспетчерский SELECT и state machine."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(19)))
    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )
    assert final_status == "rejected_a"

    async def _decision_count() -> int:
        async with session_factory() as session:
            return len(
                (await session.execute(select(Decision).where(Decision.application_id == application_id)))
                .scalars().all()
            )

    baseline = await _decision_count()
    assert baseline == 1

    # 1) Диспетчерский запрос (app/tasks/stage_a.py): "received" больше не
    # вернёт эту заявку, сколько раз ни повтори — не найдено, не переоткрыто.
    async with session_factory() as session:
        for _ in range(10):
            picked = (
                await session.execute(
                    select(Application.id).where(Application.status == "received", Application.id == application_id)
                )
            ).scalar_one_or_none()
            assert picked is None

    # 2) Второй уровень защиты: даже прямой повторный вызов run_stage_a на
    # уже решённой заявке не проходит тихо — state machine запрещает переход
    # rejected_a -> ocr (см. app/engine/state_machine.py), решение не дублируется.
    from app.engine.state_machine import InvalidTransitionError

    for _ in range(9):
        try:
            await run_stage_a(
                session_factory, application_id,
                ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
            )
        except InvalidTransitionError:
            pass
        else:
            raise AssertionError("повторный run_stage_a на решённой заявке должен был упасть")

    assert await _decision_count() == baseline


async def test_stage_a_sends_adult_applicant_to_operator(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))
    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "manual_review"
        assert decision.reason_code is None

        # Контракт по всему конвейеру: чистые проверки (ok) -> to_operator, не rejected_a.
        # ФНС/Федресурс/ФССП (T09) — фейковые источники по умолчанию без данных:
        # ФНС не находит ИНН без предварительно заданного ответа (no_data), ФССП
        # немедленно резолвится в "нет производств" (см. _submit_fssp).
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["checks"] == {
            "mvd": {"status": "ok"},
            "rosfinmon": {"status": "ok"},
            "blacklist": {"status": "ok"},
            "fns": {"status": "no_data"},
            "fedresurs": {"status": "ok"},
            "fssp": {"status": "ok"},
        }

    detail = await partner_client.get_application(external_id)
    assert detail.status == "on_call"


async def test_stage_a_ocr_failed_goes_to_operator_with_note(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    low_confidence = build_ocr_result(confidence=0.99, confidence_overrides={"passport_series": 0.1})
    ocr_provider = FakeOcrProvider(low_confidence)

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        assert application.status == "to_operator"

        # ocr_failed не проходит через движок правил — decisions/feature_snapshots не пишутся
        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert decisions == []

    detail = await partner_client.get_application(external_id)
    assert detail.status == "on_call"
    assert "распознать" in detail.raw["comments"][-1]["text"]


async def test_recover_stuck_ocr_applications_moves_old_ones_to_operator(
    partner_admin, partner_client, storage, session_factory
):
    """Ваше явное указание: защита на случай, что даже повторы в run_ocr не
    спасли (процесс убит посреди вызова и т.п.) — заявка, дольше
    OCR_STUCK_MINUTES в статусе "ocr", уводится оператору отдельным
    механизмом, не связанным с одним конкретным вызовом run_stage_a."""
    from app.engine.pipeline import recover_stuck_ocr_applications
    from app.engine.state_machine import transition
    from app.models import StatusHistory

    stuck_external_id, stuck_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    fresh_external_id, fresh_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    async with session_factory() as session:
        for application_id in (stuck_id, fresh_id):
            application = await session.get(Application, application_id)
            transition(session, application, "ocr", actor_type="system")
        await session.commit()

    async with session_factory() as session:
        stuck_row = (
            await session.execute(
                select(StatusHistory).where(StatusHistory.application_id == stuck_id, StatusHistory.to_status == "ocr")
            )
        ).scalar_one()
        stuck_row.created_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        await session.commit()

    recovered = await recover_stuck_ocr_applications(session_factory, partner_client=partner_client, stuck_minutes=30)
    assert recovered == 1

    async with session_factory() as session:
        stuck_application = await session.get(Application, stuck_id)
        fresh_application = await session.get(Application, fresh_id)
        assert stuck_application.status == "to_operator"
        assert fresh_application.status == "ocr"  # недавняя — не трогаем, обработка в разгаре

    stuck_detail = await partner_client.get_application(stuck_external_id)
    assert stuck_detail.status == "on_call"


async def test_stage_a_ocr_provider_failure_goes_to_operator_not_stuck(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Ваше явное указание после находки с 22 зависшими заявками: сбой самого
    провайдера (не "уверенность ниже порога", а сеть/ключ недоступны) не
    должен держать заявку в статусе "ocr" бесконечно — после всех повторов из
    конфига (OCR_MAX_ATTEMPTS) она уходит оператору, как и below_threshold."""
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    ocr_provider = FakeOcrProvider(raise_error=True)

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"
    assert len(ocr_provider.calls) == get_settings().ocr_max_attempts  # все повторы использованы, не один

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        assert application.status == "to_operator"

        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert decisions == []

    detail = await partner_client.get_application(external_id)
    assert detail.status == "on_call"
    assert "распознать" in detail.raw["comments"][-1]["text"]


async def test_stage_a_missing_document_raises(session_factory, storage, active_ruleset):
    from decimal import Decimal

    async with session_factory() as session:
        application = Application(
            external_id=f"ext-no-docs-{uuid.uuid4().hex[:8]}",
            status="received",
            goods_name="Холодильник",
            goods_price=Decimal("50000.00"),
            down_payment=Decimal("5000.00"),
            term_months=12,
            tariff="standard",
            requested_amount=Decimal("45000.00"),
            profile={},
            consent_pd_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.commit()
        application_id = application.id

    ocr_provider = FakeOcrProvider(build_ocr_result())
    try:
        await run_stage_a(
            session_factory, application_id,
            ocr_provider=ocr_provider, partner_client=None, storage=storage,
        )
        assert False, "ожидалась DocumentNotFoundError"
    except DocumentNotFoundError:
        pass


# --- контракт статусов через весь конвейер (T08) ---------------------------------


async def test_stage_a_mvd_hit_rejects(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    series = unique_valid_passport_series()
    number = f"{uuid.uuid4().int % 1000000:06d}"
    async with session_factory() as session:
        session.add(
            InvalidPassport(
                passport_series=series, passport_number=number, loaded_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "passport_series": series,
                "passport_number": number,
            }
        )
    )
    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "rejected_a"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "PASSPORT_INVALID"

        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["checks"]["mvd"]["status"] == "hit"

    detail = await partner_client.get_application(external_id)
    assert detail.status == "rejected"


async def test_stage_a_rosfinmon_hit_rejects(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    last_name, first_name, middle_name = "Магомедов", "Магомед", "Магомедович"
    age_overrides = _age_overrides(30)
    birth_date_str = age_overrides["birth_date"]

    async with session_factory() as session:
        session.add(
            SanctionsList(
                list_type="te",
                full_name=f"{last_name} {first_name} {middle_name}",
                name_normalized=normalize_name(full_name(last_name, first_name, middle_name)),
                birth_date=date.fromisoformat(birth_date_str),
                loaded_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **age_overrides,
                "last_name": last_name,
                "first_name": first_name,
                "middle_name": middle_name,
                # Уникальный паспорт (T09): санкционный хит теперь персистит freeze
                # (app.engine.pipeline) — с общим дефолтным паспортом это заморозило
                # бы его для всех остальных тестов до конца сессии pytest.
                "passport_series": unique_valid_passport_series(),
                "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
            }
        )
    )
    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "rejected_a"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "SANCTIONS_HIT"

        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["checks"]["rosfinmon"]["status"] == "hit"

    detail = await partner_client.get_application(external_id)
    assert detail.status == "rejected"


async def test_stale_registry_does_not_cause_auto_reject(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Устаревание реестра само по себе не должно приводить к отказу: заявка с
    чистыми проверками по-прежнему уходит оператору, а не в rejected_a."""
    throwaway_registry = f"never_synced_{uuid.uuid4().hex[:8]}"
    assert await is_registry_stale(session_factory, registry=throwaway_registry, max_staleness_days=2) is True

    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(build_ocr_result(overrides=_age_overrides(30)))

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"


async def test_stage_a_rosfinmon_threshold_actually_used_from_config(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Порог 0.7 в dev.yaml — не случайное число: два похожих, но не идентичных
    ФИО подобраны так, что при этом конкретном пороге одно даёт hit, другое нет
    (проверено напрямую через similarity() в БД: 0.857 и 0.385). Если кто-то
    захардкодит порог в pipeline.py (например, 0.99) или перестанет читать его
    из конфига, "похожий" случай перестанет давать rejected_a — тест покраснеет."""
    stored_birth_date = _age_overrides(30)["birth_date"]
    async with session_factory() as session:
        session.add(
            SanctionsList(
                list_type="te",
                full_name="Гаджиев Гаджи Гаджиевич",
                name_normalized=normalize_name(full_name("Гаджиев", "Гаджи", "Гаджиевич")),
                birth_date=date.fromisoformat(stored_birth_date),
                loaded_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    # similarity('гаджиев гаджи гаджиевич', 'гаджиев гаджи гажиевич') = 0.857 >= 0.7
    _, similar_application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    similar_ocr = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "last_name": "Гаджиев",
                "first_name": "Гаджи",
                "middle_name": "Гажиевич",
                # Уникальный паспорт (T09) — см. комментарий в test_stage_a_rosfinmon_hit_rejects.
                "passport_series": unique_valid_passport_series(),
                "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
            }
        )
    )
    similar_status = await run_stage_a(
        session_factory, similar_application_id,
        ocr_provider=similar_ocr, partner_client=partner_client, storage=storage,
    )

    # similarity('гаджиев гаджи гаджиевич', 'гаджиев заур русланович') = 0.385 < 0.7
    _, different_application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    different_ocr = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "last_name": "Гаджиев",
                "first_name": "Заур",
                "middle_name": "Русланович",
            }
        )
    )
    different_status = await run_stage_a(
        session_factory, different_application_id,
        ocr_provider=different_ocr, partner_client=partner_client, storage=storage,
    )

    assert similar_status == "rejected_a"
    assert different_status == "to_operator"

    async with session_factory() as session:
        similar_decision = (
            await session.execute(
                select(Decision).where(Decision.application_id == similar_application_id)
            )
        ).scalar_one()
        assert similar_decision.reason_code == "SANCTIONS_HIT"


async def test_stage_a_blank_year_mismatch_is_flag_only_not_auto_reject(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                "birth_date": "1990-01-01",
                "issue_date": "2020-01-01",
                "passport_series": "1210",  # год бланка 2010; N=3 -> выдача позже 2013 — расхождение
            }
        )
    )

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"  # флаг, не автоотказ

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code is None
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["passport_blank_year"] == 2010
        assert snapshot.features["passport_blank_year_mismatch"] is True
        assert snapshot.features["passport_blank_year_future"] is False


async def test_stage_a_blank_year_future_is_hard_stop(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "passport_series": "1250",  # yy=50 > текущих двух цифр года -> бланк из будущего
            }
        )
    )

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "rejected_a"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "PASSPORT_BLANK_YEAR_FUTURE"
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["passport_blank_year"] is None
        assert snapshot.features["passport_blank_year_future"] is True


async def test_stage_a_department_code_region_mismatch_is_flag_only(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                # серия по умолчанию "7700" (регион 77), код — реальный код другого
                # региона (010-001, Адыгея, см. config/reference/fms_unit.csv) -> расхождение
                "department_code": "010-001",
            }
        )
    )

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"  # флаг, не автоотказ

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code is None
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["passport_department_code_flag"] is True
        assert "passport_department_code_not_found" not in snapshot.features


async def test_stage_a_department_code_not_in_reference_is_marked_not_flagged(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "department_code": "999-999",  # заведомо нет в справочнике (нет региона 99)
            }
        )
    )

    final_status = await run_stage_a(
        session_factory, application_id,
        ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code is None
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["passport_department_code_not_found"] is True
        assert "passport_department_code_flag" not in snapshot.features


async def test_stage_a_passport_reused_is_flag_only_not_auto_reject(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    shared_series = unique_valid_passport_series()
    shared_number = f"{uuid.uuid4().int % 1000000:06d}"

    _, first_application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    first_ocr = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "passport_series": shared_series,
                "passport_number": shared_number,
                "last_name": "Иванов",
                "first_name": "Иван",
                "middle_name": "Иванович",
            }
        )
    )
    await run_stage_a(
        session_factory, first_application_id,
        ocr_provider=first_ocr, partner_client=partner_client, storage=storage,
    )

    external_id_2, second_application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    second_ocr = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(30),
                "passport_series": shared_series,
                "passport_number": shared_number,
                "last_name": "Петров",
                "first_name": "Пётр",
                "middle_name": "Петрович",
            }
        )
    )
    final_status = await run_stage_a(
        session_factory, second_application_id,
        ocr_provider=second_ocr, partner_client=partner_client, storage=storage,
    )

    assert final_status == "to_operator"  # флаг, не автоотказ

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == second_application_id))
        ).scalar_one()
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["passport_reused"] is True
