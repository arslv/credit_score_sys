"""T10/T11: стадия Б, включая БКИ и финансовые правила. Хелперы
_ingest/_create_partner_application — из test_engine_pipeline.py, чтобы не
дублировать создание заявки в fake_partner."""

import uuid
from datetime import date, datetime, timezone
from unittest.mock import patch
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
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _all_decisions, _all_sent_texts, _ingest, _last_decision, active_ruleset  # noqa: F401

_DEFAULT_STAGE_B_PAYLOAD = {
    # T18: /applications больше не несёт параметры сделки (это теперь поле
    # /review) — дефолты здесь те же, что раньше были в CreateApplicationRequest
    # на стороне fake_partner (goods_price=60000, down_payment=6000, term=12,
    # tariff="standard"), чтобы тесты, не указывающие их явно, не ловили
    # TERM_OUT_OF_RANGE/лимиты на плейсхолдерах 0/"unknown" из /applications.
    "deal": {
        "goods_category_code": "household_appliances",
        "goods_name": "Стиральная машина",
        "goods_price": 60000.0,
        "down_payment": 6000.0,
        "term_months": 12,
        "tariff": "standard",
    },
    "employment": {"type": "employed"},
    "income": {"declared_monthly": "100000"},
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


async def _mark_fssp_checked(session_factory, application_id, *, subject_type: str = "applicant", subject_id=None) -> None:
    """T16: approved невозможен без проверки ФССП (fssp_required_for_approval) —
    тесты на одобрение вставляют эту строку напрямую, как если бы оператор уже
    прошёл капчу до рассмотрения (сама капча — app/api/fssp.py, отдельные тесты)."""
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            Check(
                application_id=application_id, subject_type=subject_type, subject_id=subject_id,
                source="fssp", status="ok", parsed={"productions": []},
                started_at=now, finished_at=now, duration_ms=0,
            )
        )
        await session.commit()


async def _prepare_application_at_stage_b(
    partner_admin, partner_client, storage, session_factory, *, stage_b_payload=None, **create_overrides
):
    """T18: два вебхука, как сделал бы заказчик в новой схеме — /applications
    (через _ingest), затем /review с готовым снимком анкеты стадии Б (та же
    форма deal/employment/income/obligations/guarantor, что раньше писали
    напрямую в ExternalSnapshot). Заявка после review остаётся to_operator —
    тем же путём её подбирают run_stage_b/finalize_stage_b ниже, как и раньше."""
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app as fastapi_app

    consent_bki = create_overrides.pop("consent_bki", True)
    # T18: цена/взнос/срок/тариф/название товара — теперь поля /review (deal),
    # не /applications: раньше шли в create_overrides заявки, здесь перенаправляем
    # их в deal, иначе Pydantic молча отбросит неизвестные поля в NewApplicationIn.
    deal_overrides = {
        key: create_overrides.pop(key)
        for key in ("goods_price", "down_payment", "term_months", "tariff", "goods_name")
        if key in create_overrides
    }
    fastapi_app.state.storage = storage
    external_id, application_id = await _ingest(
        partner_admin, partner_client, storage, session_factory, **create_overrides,
    )

    payload = stage_b_payload or _DEFAULT_STAGE_B_PAYLOAD
    # Дефолты сделки — даже если конкретный тест переопределил "deal" частично
    # (например, только goods_category_code), остальное не улетает на
    # плейсхолдеры /applications (0/"unknown") — см. докстрайк _DEFAULT_STAGE_B_PAYLOAD.
    deal = {**_DEFAULT_STAGE_B_PAYLOAD["deal"], **(payload.get("deal") or {}), **deal_overrides}
    review_body = {
        "external_id": external_id,
        "event_id": str(uuid.uuid4()),
        "status": "on_review",
        "deal": {
            "goods_category_code": deal.get("goods_category_code"),
            "goods_name": deal.get("goods_name"),
            "goods_price": deal.get("goods_price"),
            "down_payment": deal.get("down_payment"),
            "term_months": deal.get("term_months"),
            "tariff": deal.get("tariff"),
            "monthly_payment": deal.get("monthly_payment"),
        },
        "employment": payload.get("employment") or {},
        "income": payload.get("income") or {},
        "obligations": payload.get("obligations") or {},
        "bki_consent": {
            "given": consent_bki,
            "at": datetime.now(timezone.utc).isoformat() if consent_bki else None,
        },
        "guarantor": payload.get("guarantor"),
    }
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        response = await client.post(
            "/api/v1/partner/review", json=review_body,
            headers={"Authorization": f"Bearer {get_settings().partner_inbound_token}"},
        )
    assert response.status_code == 202, response.text  # правило 14 CLAUDE.md

    # Этот хелпер намеренно пропускает стадию А (тесты файла — про стадию Б/БКИ
    # саму по себе) — вебхук /review не трогает application.status (его меняет
    # сама стадия Б), а run_stage_b подбирает заявки по статусу to_operator/
    # gray_zone/stage_b. Выставляем то же, что раньше реально дала бы стадия А.
    async with session_factory() as session:
        application = await session.get(Application, application_id)
        application.status = "to_operator"
        session.add(
            StatusHistory(
                application_id=application_id, from_status="received", to_status="to_operator", actor_type="system",
            )
        )
        await session.commit()

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

    decision_sent = await _last_decision(partner_admin, external_id)
    assert "БКИ не подключено" in decision_sent["reason"]


async def test_stage_b_run_ten_times_without_new_snapshot_makes_exactly_one_decision(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Найденный баг (T13c, ваше уточнение): gray_zone — статус, в который
    заявка ВОЗВРАЩАЕТСЯ после решения (в отличие от "received" у стадии А,
    откуда только уходят), а run_stage_b подбирается по статусу — без клейма
    снимка celery beat решал бы тот же снимок заново на каждом тике."""
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )

    kwargs = dict(
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
        sent = await _all_decisions(partner_admin, external_id)
        return decisions, history, len(sent)

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
    # content_hash — больше не идемпотентность (T18: это event_id у вебхука),
    # только подсказка фронту для схлопывания одинаковых снимков подряд
    # (frontend/src/components/SnapshotHistory.tsx) — здесь просто хеш payload.
    import hashlib
    import json

    async with session_factory() as session:
        payload = {"stage_b": _DEFAULT_STAGE_B_PAYLOAD}
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        session.add(ExternalSnapshot(
            application_id=application_id, stage="B", payload_raw=payload, content_hash=content_hash,
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


# T18: test_write_back_status_and_comment_does_not_create_new_stage_b_snapshot
# убран целиком — его предпосылка (polling эхом видит нашу же запись как «новый
# снимок анкеты», T13c) структурно не воспроизводима в push-схеме: опроса их API
# больше нет, ExternalSnapshot(stage="B") пишется ровно по входящему /review
# вебхуку с новым event_id, никогда по нашему собственному исходящему ответу.


async def test_missing_section_returns_to_operator_with_list(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    incomplete_payload = {"deal": {"goods_category_code": "household_appliances"}}  # без employment/income
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

    sent = await _all_decisions(partner_admin, external_id)
    assert len(sent) == 1

    comment = sent[-1]["reason"]
    # T18: "address" убран из required_sections (адрес заявителя уже есть из
    # OCR) — только employment/income остаются обязательными в этом сценарии.
    assert "employment" in comment and "income" in comment


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
    )

    assert final_status == "gray_zone"  # предложение сохранилось, но БКИ не подключено

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(FeatureSnapshot).where(FeatureSnapshot.application_id == application_id, FeatureSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot.features["tariff_offer"]["tariff"] == "advantageous"

    comments = await _all_sent_texts(partner_admin, external_id)
    assert "advantageous" in comments


async def test_limits_exceeded_for_every_tariff_rejects(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    # T21: ПДН включает платёж по рассрочке — на 10 млн это ~833 тыс. ₽/мес.,
    # и заявленный ПДН (фильтр ДО лимитов) отказал бы раньше. Тест — про
    # лимиты, поэтому доход такой, что ПДН проходит.
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
        stage_b_payload={**_DEFAULT_STAGE_B_PAYLOAD, "income": {"declared_monthly": "100000000"}},
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


async def test_guarantor_full_check_suite_queues_fns_and_links_client(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T10 п.2 / T16: поручитель проходит локальные проверки (МВД/РФМ/ЧС),
    привязывается к clients; ФНС по нему ставится в очередь фоновой задачей
    (не блокирует стадию Б — ваше явное указание T16), ФССП по нему — только
    по действию оператора (app/api/fssp.py), не здесь."""
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

    with patch("app.engine.stage_b_pipeline.queue_fns_checks") as mock_queue:
        final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client)
    # БКИ не настроено в тестовом окружении по умолчанию -> gray_zone, не стадия
    # ждёт ФНС (ФНС вообще не блокирует, см. докстринг выше).
    assert final_status == "gray_zone"

    async with session_factory() as session:
        guarantor = (
            await session.execute(select(Guarantor).where(Guarantor.application_id == application_id))
        ).scalar_one()
        assert guarantor.client_id is not None
        assert guarantor.status == "used"

        checks = (
            await session.execute(
                select(Check).where(Check.application_id == application_id, Check.subject_type == "guarantor")
            )
        ).scalars().all()
        assert {c.source for c in checks} == {"mvd", "rosfinmon", "blacklist"}
        # ФНС — фоновая задача, не checks синхронно (T16): проверяем, что она
        # была поставлена в очередь именно для поручителя.
        mock_queue.assert_called_once()
        _, kwargs = mock_queue.call_args
        assert kwargs["subject_type"] == "guarantor"
        assert kwargs["subject_id"] == guarantor.id

        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.reason_code == "BKI_NOT_CONFIGURED"


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
    # наш write-back успеет пройти: T18, их эндпоинт (fake_partner) отвечает
    # 409 с current_status/reason — настраиваем заглушку на этот ответ.
    (await partner_admin.post(
        "/_test/configure",
        json={
            "mode": "conflict", "conflict_current_status": "approved",
            "conflict_reason": "Клиент постоянный, одобрено на месте",
        },
    )).raise_for_status()

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
    await _mark_fssp_checked(session_factory, application_id)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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

    decision_sent = await _last_decision(partner_admin, external_id)
    assert "Одобрено" in decision_sent["reason"]


async def test_approve_downgraded_to_gray_zone_without_fssp_check(
    partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T16, ваше явное указание: без проверки ФССП approved невозможен —
    решение, которое иначе было бы approve, становится gray_zone с причиной
    FSSP_NOT_CHECKED (тот же паттерн, что BKI_NOT_CONFIGURED). Та же заявка,
    что в test_bki_approve_with_good_history_and_sufficient_score выше, но
    БЕЗ _mark_fssp_checked — единственная разница."""
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
        bki_provider=provider,
    )

    assert final_status == "gray_zone"
    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        assert decision.outcome == "manual_review"
        assert decision.reason_code == "FSSP_NOT_CHECKED"
        # Решение всё равно одобрило бы по финансовым правилам — approved_amount
        # НЕ выставлен (manual_review — не approve), в отличие от теста выше.
        assert decision.approved_amount is None

    decision_sent = await _last_decision(partner_admin, external_id)
    assert decision_sent["status"] == "manual_decision"


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
    await _mark_fssp_checked(session_factory, application_id)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overdue_history_report()))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overdue_history_report()))  # score 380

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id)
    high_score_report = BkiReport(contracts=[], inquiries=[], score=800)  # выше 760 (порог для low-ликвидности)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(high_score_report))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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

    decision_sent = await _last_decision(partner_admin, external_id)
    assert "12000" in decision_sent["reason"]


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
    await _mark_fssp_checked(session_factory, application_id)
    high_score_report = BkiReport(contracts=[], inquiries=[], score=800)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(high_score_report))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id)
    high_score_report = BkiReport(contracts=[], inquiries=[], score=800)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(high_score_report))

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id_1)
    status_1 = await run_stage_b(
        session_factory, application_id_1, partner_client=partner_client,
        bki_provider=provider,
    )
    assert status_1 == "approved"
    assert len(provider.requested_subjects) == 1

    external_id_2, application_id_2 = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await _link_applicant_to_client(session_factory, application_id_2, series=series, number=number)
    await _mark_fssp_checked(session_factory, application_id_2)
    status_2 = await run_stage_b(
        session_factory, application_id_2, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id_1)
    status_1 = await run_stage_b(
        session_factory, application_id_1, partner_client=partner_client,
        bki_provider=provider,
    )
    assert status_1 == "approved"  # кэш теперь есть для этого клиента

    external_id_2, application_id_2 = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, consent_bki=False,
    )
    await _link_applicant_to_client(session_factory, application_id_2, series=series, number=number)
    await _mark_fssp_checked(session_factory, application_id_2)
    status_2 = await run_stage_b(
        session_factory, application_id_2, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id)

    final_status = await run_stage_b(
        session_factory, application_id, partner_client=partner_client,
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
    await _mark_fssp_checked(session_factory, application_id)
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))

    # "Другая" заявка того же клиента, чей платный запрос сейчас в полёте —
    # application_id у bki_requests_in_flight ссылается на реальную строку
    # applications (FK), поэтому нужна вторая настоящая заявка, не случайный
    # uuid. Вставляем её напрямую (не через _prepare_application_at_stage_b):
    # T18: раньше хелпер зовёт polling-синхронизацию, которая перечитывает ВСЕ
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
    await _mark_fssp_checked(session_factory, application_id)
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


async def _card_decision(admin_client, session_factory, application_id, email: str) -> dict:
    from tests._admin_helpers import create_employee, login

    await create_employee(session_factory, email=email, role="admin")
    await login(admin_client, email)
    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200, response.text
    return response.json()["decisions"][-1]


async def test_review_income_and_obligations_reach_pti_and_card(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T20, баг «ПДН 0%»: доход и платежи из /review (T18) доходят до расчёта.
    T21: в оба ПДН входит платёж по нашей рассрочке. Заказчик не прислал
    deal.monthly_payment -> по тарифу из правил: (60 000 − 6 000) × (1 + 0) / 12
    = 4 500 ₽. Заявленный: (15 000 + 4 500) / 100 000 = 19,5%; фактический:
    (34 000 по трём кредитам КИ + 4 500) / 100 000 = 38,5%."""
    from app.sources.bki import overloaded_report

    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "obligations": {"monthly_payment_total": "15000"}}
    _external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overloaded_report()))
    await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
        snapshot = await session.get(FeatureSnapshot, decision.feature_snapshot_id)
        assert snapshot.features["declared_pti"] == 0.195
        assert snapshot.feature_set_ver == "stage_b.v2"

    card = await _card_decision(admin_client, session_factory, application_id, "rm_pti_ok@example.com")
    assert card["pti_status"] == "computed"
    assert card["pti"] == 0.385
    assert card["bki_active_contracts_count"] == 3
    assert card["installment_payment"] == "4500.00"
    assert card["installment_payment_source"] == "tariff"


async def test_partner_monthly_payment_from_review_is_used_in_pti(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T21: основной источник платежа — deal.monthly_payment из /review (их
    график с наценкой), а не расчёт по тарифу: (34 000 + 6 500) / 100 000."""
    from app.sources.bki import overloaded_report

    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {**_DEFAULT_STAGE_B_PAYLOAD["deal"], "monthly_payment": 6500}}
    _external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overloaded_report()))
    await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    async with session_factory() as session:
        snapshot_row = (
            await session.execute(
                select(ExternalSnapshot).where(
                    ExternalSnapshot.application_id == application_id, ExternalSnapshot.stage == "B"
                )
            )
        ).scalar_one()
        # Правило 3: сырое значение от заказчика сохранено в снимке анкеты.
        assert snapshot_row.payload_raw["stage_b"]["deal"]["monthly_payment"] == "6500"

    card = await _card_decision(admin_client, session_factory, application_id, "rm_pti_partner@example.com")
    assert card["pti"] == 0.405
    assert card["installment_payment"] == "6500.00"
    assert card["installment_payment_source"] == "partner"


async def test_pti_not_computable_without_partner_payment_and_tariff_markup(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T21: «не вычислимо» — только если нет ни поля от заказчика, ни наценки
    тарифа (тариф неизвестен правилам). Заниженного ПДН без рассрочки нет."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "deal": {**_DEFAULT_STAGE_B_PAYLOAD["deal"], "tariff": "no_such_tariff"}}
    _external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )
    await run_stage_b(session_factory, application_id, partner_client=partner_client)

    card = await _card_decision(admin_client, session_factory, application_id, "rm_pti_noinst@example.com")
    assert card["pti"] is None
    assert card["pti_status"] == "no_installment"
    assert card["installment_payment"] is None


async def test_card_pti_not_computable_without_income(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T20: нет дохода — ПДН «не вычислимо (нет данных о доходе)», а не 0%.
    Нулевой доход проходит полноту анкеты (секция income непустая), БКИ
    запрашивается, но делить не на что: pti=None, и карточка это различает."""
    payload = {**_DEFAULT_STAGE_B_PAYLOAD, "income": {"declared_monthly": "0"}}
    _external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(good_history_report()))
    await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    card = await _card_decision(admin_client, session_factory, application_id, "rm_pti_none@example.com")
    assert card["stage"] == "B"
    assert card["pti"] is None
    assert card["pti_status"] == "no_income"


async def test_card_pti_not_computable_without_credit_history(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """БКИ не подключено — решение без КИ: ПДН не вычислим по другой причине
    (доход есть), карточка не должна сваливать это в «нет данных о доходе»."""
    _external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
    )
    await run_stage_b(
        session_factory, application_id, partner_client=partner_client, bki_provider=NotConfiguredBkiProvider(),
    )

    card = await _card_decision(admin_client, session_factory, application_id, "rm_pti_nobki@example.com")
    assert card["reason_code"] == "BKI_NOT_CONFIGURED"
    assert card["pti"] is None
    assert card["pti_status"] == "no_credit_history"


async def test_pti_83_percent_by_credit_history_rejects_with_reason_text(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """T22, скриншот T21: ПДН 83% по КИ давал «Серую зону» (pti_max = 60%
    действовал только на заявленный ПДН). Теперь — отказ PTI_TOO_HIGH:
    (34 000 по трём кредитам КИ + 7 500 платёж по рассрочке) / 50 000 = 83%.
    Заявленный ПДН (0 + 7 500) / 50 000 = 15% — фильтр до БКИ проходит."""
    from app.sources.bki import overloaded_report

    payload = {
        **_DEFAULT_STAGE_B_PAYLOAD,
        "deal": {**_DEFAULT_STAGE_B_PAYLOAD["deal"], "monthly_payment": 7500},
        "income": {"declared_monthly": "50000"},
    }
    external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory, stage_b_payload=payload,
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    provider = FakeBkiProvider(reports=_bki_reports_for_applicant(overloaded_report()))
    final_status = await run_stage_b(session_factory, application_id, partner_client=partner_client, bki_provider=provider)

    assert final_status == "rejected_b"
    card = await _card_decision(admin_client, session_factory, application_id, "rm_pti83@example.com")
    assert card["outcome"] == "reject"
    assert card["reason_code"] == "PTI_TOO_HIGH"
    assert card["pti"] == 0.83
    assert card["triggered_rules_text"] == ["Долговая нагрузка по кредитной истории 83% выше порога отказа 60%."]

    decision_sent = await _last_decision(partner_admin, external_id)
    assert decision_sent["status"] == "rejected"
