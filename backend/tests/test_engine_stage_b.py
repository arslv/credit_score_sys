"""T10/T11: стадия Б, включая БКИ и финансовые правила. Хелперы
_ingest/_create_partner_application — из test_engine_pipeline.py, чтобы не
дублировать создание заявки в fake_partner."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.engine.bki_cache import claim_in_flight, release_in_flight
from app.engine.clients import find_or_create_client
from app.engine.rules import _MISSING, get_field, parse_matrix_key
from app.engine.schema import load_ruleset_from_yaml
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
from app.models import BkiReport as BkiReportRow
from app.sources.bki import (
    BkiContract,
    BkiReport,
    FakeBkiProvider,
    NotConfiguredBkiProvider,
    good_history_report,
    overdue_history_report,
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


async def _link_applicant_to_client(session_factory, application_id, *, series: str, number: str) -> uuid.UUID:
    """Привязывает заявку к клиенту напрямую (без прогона OCR стадии А — эти
    тесты фокусируются на стадии Б/БКИ) — тем же find_or_create_client, что
    использует стадия А (T09), чтобы получить настоящий client_id для
    кэша/защиты от двойной оплаты (оба — по client_id, не по заявке)."""
    async with session_factory() as session:
        application = await session.get(Application, application_id)
        client, _changed = await find_or_create_client(
            session, last_name="Заёмщиков", first_name="Заём", middle_name=None,
            birth_date=date(1990, 6, 15), passport_series=series, passport_number=number,
            phone="+79990000099",
        )
        application.client_id = client.id
        await session.commit()
        return client.id


def _bki_reports_for_applicant(report: BkiReport) -> dict[str, BkiReport]:
    return {"Заёмщиков|Заём|1990-06-15": report}


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


async def test_all_checks_pass_bki_not_configured_is_gray_zone(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T11 п.1: БКИ не подключено (провайдер по умолчанию — NotConfiguredBkiProvider)
    -> gray_zone с пометкой оператору, а не approved и не зависание в stage_b."""
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )

    assert final_status == "gray_zone"
    assert final_status != "approved"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "manual_review"
        assert decision.reason_code == "BKI_NOT_CONFIGURED"

        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["goods"]["sharia_allowed"] is True

    detail = await partner_client.get_application(external_id)
    assert "БКИ не подключено" in detail.raw["comments"][-1]["text"]


async def test_stage_b_run_ten_times_without_new_snapshot_makes_exactly_one_decision(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Найденный баг (T13c, ваше уточнение): gray_zone — статус, в который
    заявка ВОЗВРАЩАЕТСЯ после решения (в отличие от "received" у стадии А,
    откуда только уходят), а run_stage_b подбирается по статусу — без клейма
    снимка celery beat решал бы тот же снимок заново на каждом тике."""
    from app.integrations.adapter import _content_hash

    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )

    kwargs = dict(
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )
    first_status = await run_stage_b(session_factory, application_id, partner_client=partner_client, **kwargs)
    assert first_status == "gray_zone"  # см. test_all_checks_pass_bki_not_configured_is_gray_zone

    async def _counts() -> tuple[int, int, int]:
        async with session_factory() as session:
            decisions = len(
                (await session.execute(select(Decision).where(Decision.application_id == application_id)))
                .scalars().all()
            )
            history = len(
                (await session.execute(select(StatusHistory).where(StatusHistory.application_id == application_id)))
                .scalars().all()
            )
        detail = await partner_client.get_application(external_id)
        return decisions, history, len(detail.raw["comments"])

    baseline = await _counts()
    assert baseline[0] == 1  # ровно одно решение после первого запуска

    # Тот же снимок, тот же статус gray_zone — ещё 9 запусков подряд, как
    # если бы celery beat подбирал эту заявку на каждом тике без нового
    # «Рассмотреть» (ровно баг, который чиним).
    for _ in range(9):
        status = await run_stage_b(session_factory, application_id, partner_client=partner_client, **kwargs)
        assert status == "gray_zone"

    assert await _counts() == baseline  # ни одного дубля — решение, статус, комментарий не выросли

    # Новое «Рассмотреть» (новый снимок анкеты) — ОБЯЗАНО дать новое решение.
    async with session_factory() as session:
        payload = {"stage_b": _DEFAULT_STAGE_B_PAYLOAD}
        session.add(ExternalSnapshot(
            application_id=application_id, stage="B", payload_raw=payload, content_hash=_content_hash(payload),
        ))
        await session.commit()

    status_after_new_snapshot = await run_stage_b(
        session_factory, application_id, partner_client=partner_client, **kwargs
    )
    assert status_after_new_snapshot == "gray_zone"

    # Ровно одно НОВОЕ решение на новый снимок — не ноль (не «застряло»
    # навсегда) и не два (не задвоилось). Комментарий партнёру в этой ветке
    # не проверяем отдельно: fake_partner отклоняет повторную запись того же
    # статуса как конфликт (правило 12 CLAUDE.md) — это про синтетическую
    # подмену снимка в тесте напрямую в БД, без реального нового «Рассмотреть»
    # на стороне заказчика, не про баг, который здесь чинится.
    decisions_after, _, _ = await _counts()
    assert decisions_after == baseline[0] + 1


async def test_write_back_status_and_comment_does_not_create_new_stage_b_snapshot(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Найденный баг, продолжение (T13c, ваше уточнение №2): клейм по
    snapshot_id (тест выше) гарантирует одно решение на снимок — но плодиться
    может сам СНИМОК. content_hash раньше считался по ВСЕМУ ответу fake_partner
    (detail.raw), включая status/comments/status_history — поля, которые
    меняем МЫ САМИ, записывая решение обратно (write_decision_status/
    write_comment, app/integrations/adapter.py::_write_back). Наша же запись
    делала хеш "новым", следующий sync_applications видел "новый снимок
    анкеты" и стадия Б запускалась заново на тех же (не изменённых
    оператором) данных.

    В отличие от остальных тестов этого файла, НЕ использует
    _prepare_application_at_stage_b — тот хелпер вставляет ExternalSnapshot
    напрямую с синтетическим payload_raw={"stage_b": ...}, без status/
    comments/history вовсе, и потому не мог поймать этот баг ни разу (ваш
    вопрос "почему тест 10 запусков это не поймал" — вот почему: там не было
    ни настоящего sync_applications между запусками, ни нашей же записи
    комментария в заглушку). Здесь — реальный цикл: заполнили анкету ->
    «Рассмотреть» -> sync_applications (настоящий ответ fake_partner) ->
    решение (пишет статус+комментарий в fake_partner) -> ещё один
    sync_applications, как сделал бы celery beat тиком позже."""
    from app.integrations.adapter import sync_applications

    external_id = await _create_partner_application(partner_admin)
    (await partner_admin.post(f"/applications/{external_id}/status", json={"status": "on_call"})).raise_for_status()
    (
        await partner_admin.post(
            f"/_test/applications/{external_id}/stage-b-fields", json=_DEFAULT_STAGE_B_PAYLOAD
        )
    ).raise_for_status()
    (await partner_admin.post(f"/_test/applications/{external_id}/review")).raise_for_status()

    await sync_applications(session_factory, partner_client, storage)

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        application_id = application.id
        # sync_applications сама только складывает снимок — переход в
        # "стадию Б, ждёт обработки" в реальной системе делает run_stage_a
        # (не гоняем его здесь — тест про хеш снимка, не про стадию А), так
        # что имитируем ровно этот один переход, как _prepare_application_at_stage_b.
        application.status = "to_operator"
        session.add(
            StatusHistory(
                application_id=application_id, from_status="received", to_status="to_operator", actor_type="system",
            )
        )
        await session.commit()

    async def _stage_b_snapshot_count() -> int:
        async with session_factory() as session:
            return len(
                (
                    await session.execute(
                        select(ExternalSnapshot).where(
                            ExternalSnapshot.application_id == application_id, ExternalSnapshot.stage == "B"
                        )
                    )
                ).scalars().all()
            )

    async def _decision_count() -> int:
        async with session_factory() as session:
            return len(
                (
                    await session.execute(
                        select(Decision).where(Decision.application_id == application_id, Decision.stage == "B")
                    )
                ).scalars().all()
            )

    assert await _stage_b_snapshot_count() == 1

    status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
    )
    assert status == "gray_zone"  # БКИ не подключено в тестовом окружении — пишет статус+комментарий в fake_partner
    assert await _decision_count() == 1

    # Тик beat спустя: ещё один цикл синхронизации, анкета не менялась.
    await sync_applications(session_factory, partner_client, storage)

    assert await _stage_b_snapshot_count() == 1  # не выросло — наша же запись статуса/комментария хеш не меняет
    assert await _decision_count() == 1  # стадия Б не запустилась заново


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


async def test_stage_b_run_ten_times_on_incomplete_snapshot_writes_comment_once(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Тот же баг, другая ветка: "неполный снимок" не пишет Decision вовсе
    (см. test_missing_section_returns_to_operator_with_list выше), но статус
    остаётся to_operator — тоже переподбираемый статус. Клейм в run_stage_b
    стоит ДО проверки полноты, поэтому защищает и эту ветку."""
    incomplete_payload = {"deal": {"goods_category_code": "household_appliances"}}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=incomplete_payload,
    )

    for _ in range(10):
        status = await run_stage_b(session_factory, application_id, partner_client=partner_client)
        assert status == "to_operator"

    detail = await partner_client.get_application(external_id)
    assert len(detail.raw["comments"]) == 1

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

    assert final_status == "gray_zone"  # прошло дальше (не rejected_b), БКИ не подключено

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

    assert final_status == "gray_zone"  # предложение сохранилось, но БКИ не подключено

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["tariff_offer"]["tariff"] == "advantageous"

    detail = await partner_client.get_application(external_id)
    comments = " ".join(c["text"] for c in detail.raw["comments"])
    assert "advantageous" in comments


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

    assert final_status == "gray_zone"  # решение без поручителя (не отказ по заявке), БКИ не подключено

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
    assert final_status == "stage_b"  # поручитель ждёт опроса ФССП — БКИ ещё не запрашивалось

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


# --- T11: БКИ, финансовые правила, кэш, защита от двойной оплаты ---------------


async def test_declared_pti_too_high_rejects_without_reaching_bki(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Заявленный (не проверенный) ПДН уже выше limits.pti_max -> отказ до
    какого-либо обращения в бюро — платного вызова быть не должно."""
    payload = {
        **_DEFAULT_STAGE_B_PAYLOAD,
        "income": {"declared_monthly": "100000"},
        "obligations": {"monthly_payment_total": "70000"},  # ПДН 0.7 > pti_max (0.60)
    }
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )

    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)

    assert final_status == "rejected_b"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "DECLARED_PTI_TOO_HIGH"


async def test_bki_approve_with_good_history_and_sufficient_score(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "approved"
    async with session_factory() as session:
        application = await session.get(Application, application_id)
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "approve"
        assert decision.score == Decimal("780.00")
        assert decision.score_source == "bki_score"
        assert decision.approved_amount == application.requested_amount
        assert decision.offered_tariff == "standard"
        assert decision.required_down_pay == application.down_payment

    detail = await partner_client.get_application(external_id)
    assert "Одобрено" in detail.raw["comments"][-1]["text"]


async def test_bki_reject_on_low_score_regardless_of_down_payment(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Скор ниже порога автоотказа -> reject независимо от первого взноса."""
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overdue_history_report()))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "rejected_b"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "reject"
        assert decision.reason_code == "SCORE_BELOW_THRESHOLD"
        assert decision.required_down_pay is None


async def test_bki_reject_even_when_matrix_requires_down_payment(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Матрица подняла бы порог/потребовала взнос, но скор ниже порога
    автоотказа И ПОСЛЕ этого повышения — отказ, предложение не возникает."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "clothing_footwear"}}  # liquidity=low
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overdue_history_report()))  # score 380

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "rejected_b"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "reject"
        assert decision.required_down_pay is None
        assert decision.offered_tariff is None


async def test_risk_tariff_down_payment_required_when_actual_payment_insufficient(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Точное условие альтернативного тарифа по риску: решение было бы
    approve, если бы выполнялось require_down_payment из матрицы, а фактический
    взнос меньше -> предложение (gray_zone), не отказ."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "clothing_footwear"}}  # liquidity=low
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
        goods_price=60000.0, down_payment=6000.0,  # 10% — меньше требуемых 20%
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    high_score_report = BkiReport(contracts=[], inquiries=[], score=800)  # выше 760 (порог для low-ликвидности)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(high_score_report))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "gray_zone"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "manual_review"
        assert decision.reason_code == "RISK_TARIFF_DOWN_PAYMENT_REQUIRED"
        assert decision.required_down_pay == Decimal("12000.00")  # 20% от 60000
        assert decision.offered_tariff == "standard"

    detail = await partner_client.get_application(external_id)
    assert "12000" in detail.raw["comments"][-1]["text"]


async def test_risk_tariff_down_payment_sufficient_approves_directly(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "clothing_footwear"}}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
        goods_price=60000.0, down_payment=15000.0,  # 25% — выше требуемых 20%
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    high_score_report = BkiReport(contracts=[], inquiries=[], score=800)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(high_score_report))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "approved"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "approve"


async def test_risk_tariff_down_payment_exactly_at_boundary_approves(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Взнос ровно равен требуемому (20% от 60000 = 12000) -> одобрение, не
    предложение: условие интерсепции строгое "меньше", не "меньше или равно"."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {"goods_category_code": "clothing_footwear"}}
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
        goods_price=60000.0, down_payment=12000.0,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    high_score_report = BkiReport(contracts=[], inquiries=[], score=800)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(high_score_report))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "approved"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "approve"
        assert decision.required_down_pay == Decimal("12000.00")


async def test_repeat_review_reuses_cached_report_without_new_bureau_call(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T11 критерий приёмки: повторная стадия Б берёт отчёт из кэша — второй
    платный вызов в бюро не совершается."""
    series = unique_valid_passport_series()
    number = f"{uuid.uuid4().int % 1000000:06d}"
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    external_id_1, application_id_1 = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(session_factory, application_id_1, series=series, number=number)
    status_1 = await run_stage_b(
        session_factory, application_id_1, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )
    assert status_1 == "approved"
    assert len(provider.requested_subjects) == 1

    external_id_2, application_id_2 = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(session_factory, application_id_2, series=series, number=number)
    status_2 = await run_stage_b(
        session_factory, application_id_2, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert status_2 == "approved"
    assert len(provider.requested_subjects) == 1  # не выросло — кэш, не новый вызов

    async with session_factory() as session:
        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
        assert decision_2.score == Decimal("780.00")


async def test_missing_consent_does_not_use_cached_report(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Отчёт из кэша используется только если у ТЕКУЩЕЙ заявки есть
    действующее согласие — иначе серая зона, кэш не читается вовсе."""
    series = unique_valid_passport_series()
    number = f"{uuid.uuid4().int % 1000000:06d}"
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    external_id_1, application_id_1 = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(session_factory, application_id_1, series=series, number=number)
    status_1 = await run_stage_b(
        session_factory, application_id_1, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )
    assert status_1 == "approved"  # кэш теперь есть для этого клиента

    external_id_2, application_id_2 = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, consent_bki=False,
    )
    await _link_applicant_to_client(session_factory, application_id_2, series=series, number=number)
    status_2 = await run_stage_b(
        session_factory, application_id_2, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert status_2 == "gray_zone"
    async with session_factory() as session:
        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
        assert decision_2.reason_code == "BKI_CONSENT_MISSING_OR_EXPIRED"
        assert decision_2.outcome == "manual_review"
    assert len(provider.requested_subjects) == 1  # кэш не читался — вызов бюро не повторился и не потребовался


async def test_unknown_client_is_gray_zone_without_bureau_call(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Провайдер настроен, но заявка почему-то не привязана к client_id (не
    должно случаться в реальном потоке, но данные бывают неполными) — не
    роняем и не запрашиваем платно без идентификатора для кэша/защиты."""
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    assert final_status == "gray_zone"
    assert provider.requested_subjects == []
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "BKI_CLIENT_UNKNOWN"


async def test_not_configured_provider_never_attempts_bureau_call(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=NotConfiguredBkiProvider(),
    )

    assert final_status == "gray_zone"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "BKI_NOT_CONFIGURED"

        rows = (await session.execute(select(BkiReportRow))).scalars().all()
        assert rows == []  # "не настроено" -> вызова не было -> в журнал оплаты не пишется


async def test_in_flight_busy_parks_application_and_retry_succeeds(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Повторное «Рассмотреть» из состояния ожидания БКИ должно работать:
    занятый клейм не отказывает и не роняет заявку — она остаётся в stage_b и
    следующий подбор (finalize_stage_b, как в app/tasks/stage_b.py) доводит
    БКИ до конца."""
    series = unique_valid_passport_series()
    number = f"{uuid.uuid4().int % 1000000:06d}"
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    client_id = await _link_applicant_to_client(session_factory, application_id, series=series, number=number)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    # "Другая" заявка того же клиента, чей платный запрос сейчас в полёте —
    # application_id у bki_requests_in_flight ссылается на реальную строку
    # applications (FK), поэтому нужна вторая настоящая заявка, не случайный
    # uuid. Вставляем её напрямую (не через _prepare_application_at_stage_b):
    # тот хелпер зовёт sync_applications, который повторно перечитывает ВСЕ
    # заявки в fake_partner в статусе "на рассмотрении", включая первую, и
    # затёр бы её тестовый снимок стадии Б снимком из fake_partner.
    async with session_factory() as session:
        other_application = Application(
            external_id=f"other-{uuid.uuid4().hex[:10]}", status="stage_b",
            goods_name="Другой товар", goods_price=Decimal("50000"), down_payment=Decimal("0"),
            term_months=12, tariff="standard", requested_amount=Decimal("50000"),
            profile={}, consent_pd_at=datetime.now(timezone.utc),
        )
        session.add(other_application)
        await session.flush()
        other_application_id = other_application.id
        await session.commit()

    busy_claim_id = await claim_in_flight(
        session_factory, client_id=client_id, application_id=other_application_id, timeout_minutes=10
    )
    assert busy_claim_id is not None

    status_while_busy = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )
    assert status_while_busy == "stage_b"
    assert provider.requested_subjects == []  # платного вызова не было вовсе

    async with session_factory() as session:
        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalars().all()
        assert decisions == []

    await release_in_flight(session_factory, busy_claim_id, failed=False)

    status_after_retry = await finalize_stage_b(
        session_factory, application_id, partner_client=partner_client, bki_provider=provider,
    )
    assert status_after_retry == "approved"
    assert len(provider.requested_subjects) == 1


async def test_dev_ruleset_stage_b_rule_field_paths_all_resolve(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Аналог test_dev_ruleset_rule_field_paths_all_resolve_against_real_snapshot
    (T09, test_engine_pipeline_t09.py), но для полей, заполняемых только на
    стадии Б (T10/T11): sharia_goods, declared_pti_too_high, pti_high, а также
    matrix.amount/goods_liquidity/is_repeat — до T11 их не считал никто, и
    matrix была фактически мертва (см. отчёт T11). Признаки ФССП заявителя
    (features.fssp_*) переносятся из стадии А не пересчётом, а переносом
    готового снапшота — сюда вставляется напрямую, как и остальная стадия А в
    этих тестах (см. _prepare_application_at_stage_b). prior_defaults
    остаётся исключением (T15, импорт истории платежей ещё не сделан)."""
    guarantor_payload = {
        "last_name": "Гарантов", "first_name": "Гарант", "middle_name": "Гарантович",
        "birth_date": "1990-01-01", "issue_date": "2015-01-01",
        "passport_series": unique_valid_passport_series(),
        "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
        "phone": "+79990000003",
    }
    payload = {
        **_DEFAULT_STAGE_B_PAYLOAD,
        "deal": {"goods_category_code": "clothing_footwear"},  # liquidity=low -> matrix.goods_liquidity
        "guarantor": guarantor_payload,
    }
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
        goods_price=250000.0, down_payment=60000.0,  # amount > 200000 -> matrix.amount_gt
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    async with session_factory() as session:
        session.add(
            FeatureSnapshot(
                application_id=application_id, stage="A",
                features={
                    "is_repeat": True,  # matrix.is_repeat
                    "features": {"fssp_total_amount": 10000.0, "fssp_credit_debt_amount": 10000.0, "fssp_count": 1},
                },
                feature_set_ver="stage_a.v1",
            )
        )
        await session.commit()

    report = BkiReport(
        contracts=[
            BkiContract(
                opened_at=date(2024, 1, 1), closed_at=None, status="active",
                monthly_payment="5000", balance="10000", overdue_days_max=0, subject="Кредит",
            ),
        ],
        inquiries=[], score=800,
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(report))

    await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
        fns_source=FakeFnsSource(), fedresurs_source=FakeFedresursSource(), fssp_source=FakeFsspSource(),
        bki_provider=provider,
    )

    async with session_factory() as session:
        decision = (
            await session.execute(
                select(Decision).where(Decision.application_id == application_id)
                .order_by(Decision.created_at.desc())
            )
        ).scalars().first()
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
    features = snapshot.features

    config = load_ruleset_from_yaml(Path(get_settings().ruleset_path))
    stage_b_rules = [*config.stage_b_pre_bki, *config.soft_rules]
    unresolved = [rule.id for rule in stage_b_rules if get_field(features, rule.field) is _MISSING]
    assert unresolved == [], f"правила стадии Б ссылаются на несуществующие в снапшоте поля: {unresolved}"

    matrix_unresolved = [
        key
        for entry in config.matrix
        for key in entry.when
        if parse_matrix_key(key)[0] != "prior_defaults"  # T15 — ещё не реализовано
        and get_field(features, parse_matrix_key(key)[0]) is _MISSING
    ]
    assert matrix_unresolved == [], f"matrix.when ссылается на несуществующие в снапшоте поля: {matrix_unresolved}"
