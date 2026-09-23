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
    assert "AGE_BELOW_MIN" in detail.raw["comments"][-1]["text"]


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
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["checks"] == {
            "mvd": {"status": "ok"},
            "rosfinmon": {"status": "ok"},
            "blacklist": {"status": "ok"},
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

    series = f"{uuid.uuid4().int % 10000:04d}"
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
