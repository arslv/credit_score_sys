"""T10: стадия Б без БКИ. Хелперы _ingest/_create_partner_application —
из test_engine_pipeline.py, чтобы не дублировать создание заявки в fake_partner."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.engine.stage_b_pipeline import finalize_stage_b, run_stage_b
from app.models import (
    Application,
    Check,
    Decision,
    ExternalSnapshot,
    FeatureSnapshot,
    Guarantor,
    ManualOverride,
    StatusHistory,
)
from app.sources.fedresurs import FakeFedresursSource
from app.sources.fns import FakeFnsSource
from app.sources.fssp import FakeFsspSource
from app.tasks.fssp_poll import _poll_one
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _create_partner_application, active_ruleset  # noqa: F401

_DEFAULT_STAGE_B_PAYLOAD = {
    "deal": {"goods_category_code": "household_appliances"},
    "employment": {"type": "employed"},
    "income": {"declared_monthly": "100000"},
    "address": {"registration": "г. Тестоград"},
}


async def _prepare_application_at_stage_b(
    partner_admin, partner_client, storage, session_factory, *, stage_b_payload=None, **create_overrides
):
    from app.integrations.adapter import _content_hash, sync_applications

    external_id = await _create_partner_application(partner_admin, **create_overrides)
    # fake_partner: new -> on_review напрямую запрещён (ALLOWED_TRANSITIONS в
    # store.py) — сначала on_call (как сделала бы наша же стадия А), потом
    # "Рассмотреть". raise_for_status ловит тихий провал перехода сразу, а не
    # через непонятную ошибку по ходу теста (см. отчёт T10).
    (await partner_admin.post(f"/applications/{external_id}/status", json={"status": "on_call"})).raise_for_status()
    (await partner_admin.post(f"/_test/applications/{external_id}/review")).raise_for_status()
    await sync_applications(session_factory, partner_client, storage)

    payload = {"stage_b": stage_b_payload or _DEFAULT_STAGE_B_PAYLOAD}
    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        application.status = "to_operator"
        session.add(
            StatusHistory(
                application_id=application.id, from_status="received", to_status="to_operator", actor_type="system"
            )
        )
        session.add(
            ExternalSnapshot(
                application_id=application.id, stage="B",
                payload_raw=payload, content_hash=_content_hash(payload),
            )
        )
        await session.commit()
        application_id = application.id

    return external_id, application_id


async def test_all_checks_pass_never_approved(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T10 п.1: даже когда все проверки стадии Б пройдены, approved недостижим
    — заявка остаётся в stage_b, готовая к БКИ (T11), Decision не пишется."""
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )

    assert final_status == "stage_b"
    assert final_status != "approved"

    async with session_factory() as session:
        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert decisions == []  # никакого решения — ждём БКИ

        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["goods"]["sharia_allowed"] is True


async def test_missing_section_returns_to_operator_with_list(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    incomplete_payload = {"deal": {"goods_category_code": "household_appliances"}}  # без employment/income/address
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=incomplete_payload,
    )

    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)

    assert final_status == "to_operator"

    async with session_factory() as session:
        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert decisions == []

    detail = await partner_client.get_application(external_id)
    comment = detail.raw["comments"][-1]["text"]
    assert "employment" in comment and "income" in comment and "address" in comment


async def test_sharia_forbidden_category_rejects_without_bki(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "alcohol"}}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )

    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)

    assert final_status == "rejected_b"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "reject"
        assert decision.reason_code == "GOODS_NOT_ALLOWED"
        assert decision.stage == "B"


async def test_unknown_goods_category_code_does_not_auto_reject(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Код без совпадения в справочнике — не отказ (T10 п.4)."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "no_such_code_in_catalog"}}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )

    assert final_status == "stage_b"  # прошло дальше, не rejected_b

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["goods"]["category_found"] is False


async def test_limits_exceeded_on_requested_tariff_offers_alternative(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
        goods_price=550000.0, down_payment=0.0, tariff="standard",
    )

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )

    assert final_status == "stage_b"  # предложение, не отказ

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["tariff_offer"]["tariff"] == "advantageous"

    detail = await partner_client.get_application(external_id)
    comment = detail.raw["comments"][-1]["text"]
    assert "advantageous" in comment


async def test_limits_exceeded_for_every_tariff_rejects(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
        goods_price=10000000.0, down_payment=0.0, tariff="standard",
    )

    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)

    assert final_status == "rejected_b"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "LIMITS_NOT_MET"


async def test_missing_bki_consent_is_gray_zone_not_reject(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, consent_bki=False,
    )

    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)

    assert final_status == "gray_zone"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "manual_review"
        assert decision.reason_code == "BKI_CONSENT_MISSING_OR_EXPIRED"


async def test_guarantor_local_hard_stop_proceeds_without_guarantor(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    guarantor_payload = {
        "last_name": "Гарантов", "first_name": "Гарант", "middle_name": "Гарантович",
        "birth_date": "2010-01-01",  # < 21 года -> age_min hard_stop
        "passport_series": unique_valid_passport_series(),
        "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
        "phone": "+79990000001",
    }
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "guarantor": guarantor_payload}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )

    assert final_status == "stage_b"  # решение без поручителя, не отказ по заявке

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["guarantor_used"] is False
        assert snapshot.features["guarantor_reason_code"] == "AGE_BELOW_MIN"

        guarantor = (
            await session.execute(select(Guarantor).where(Guarantor.application_id == application_id))
        ).scalar_one()
        assert guarantor.status == "rejected"


async def test_guarantor_full_check_suite_including_external_and_client_link(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T10 п.2: поручитель проходит полный набор, включая ФНС/Федресурс/ФССП
    тем же submit/poll механизмом, что и заявитель; привязывается к clients."""
    guarantor_series = unique_valid_passport_series()
    guarantor_number = f"{uuid.uuid4().int % 1000000:06d}"
    guarantor_payload = {
        "last_name": "Гарантов", "first_name": "Гарант", "middle_name": "Гарантович",
        "birth_date": "1990-01-01", "issue_date": "2015-01-01",
        "passport_series": guarantor_series, "passport_number": guarantor_number,
        "phone": "+79990000002",
    }
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "guarantor": guarantor_payload}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )

    fssp = FakeFsspSource(never_ready=True)  # заставляет реально ждать опроса
    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=fssp,
    )
    assert final_status == "stage_b"

    async with session_factory() as session:
        guarantor = (
            await session.execute(select(Guarantor).where(Guarantor.application_id == application_id))
        ).scalar_one()
        assert guarantor.client_id is not None

        checks = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.subject_type == "guarantor")
            )
        ).scalars().all()
        assert {c.source for c in checks} == {"mvd", "rosfinmon", "blacklist", "fns", "fedresurs"}
        # ФССП ещё не среди checks — в очереди на опрос, не завершился сразу.

        from app.models import FsspRequest
        fssp_request = (
            await session.execute(select(FsspRequest).where(FsspRequest.application_id == application_id))
        ).scalar_one()
        assert fssp_request.subject_type == "guarantor"
        assert fssp_request.subject_id == guarantor.id
        assert fssp_request.stage == "B"

    outcome = await _poll_one(session_factory, fssp, partner_client, fssp_request.id)
    # never_ready=True -> всегда None -> "still_pending" пока не истечёт дедлайн
    assert outcome == "still_pending"

    async with session_factory() as session:
        fssp_request = await session.get(FsspRequest, fssp_request.id)
        fssp_request.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    outcome = await _poll_one(session_factory, fssp, partner_client, fssp_request.id)
    assert outcome == "finalized"

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["guarantor_used"] is True


async def test_manual_override_recorded_when_operator_disagrees(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T10 п.5: оператор меняет статус вручную в системе заказчика с причиной —
    расхождение с нашим решением пишется в manual_overrides."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "alcohol"}}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )

    # Оператор вручную одобряет вопреки нашему (будущему) отказу — раньше, чем
    # наш write-back успеет пройти: имитируем гонку через manual-status с причиной.
    await partner_admin.post(
        f"/_test/applications/{external_id}/manual-status",
        json={"status": "approved", "reason": "Клиент постоянный, одобрено на месте"},
    )

    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)
    assert final_status == "rejected_b"  # наш внутренний статус меняется независимо от партнёра

    async with session_factory() as session:
        override = (
            await session.execute(select(ManualOverride).where(ManualOverride.application_id == application_id))
        ).scalar_one()
        assert override.employee_id is None
        assert override.to_outcome == "approved"
        assert override.reason_code == "PARTNER_OPERATOR_OVERRIDE_APPROVE"
        assert "постоянный" in override.comment
