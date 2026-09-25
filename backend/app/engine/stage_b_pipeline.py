"""Оркестратор стадии Б без БКИ (T10, ТЗ «Конвейер» -> «Стадия Б. После звонка»).
Запускается по сигналу «Рассмотреть» — снимок анкеты уже лежит в
external_snapshots(stage="B") благодаря T04 (sync_applications). Дешёвые
проверки до БКИ: полнота -> шариат -> лимиты/тариф -> согласие на КИ ->
поручитель (полный набор проверок стадии А, включая внешние — тем же
submit/poll-механизмом, что и заявитель в T09). БКИ — T11: если все проверки
пройдены, заявка остаётся в статусе "stage_b" (готова к БКИ), Decision не
пишется — approved до T11 в принципе недостижим ни при каком исходе здесь."""

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.engine.clients import find_or_create_client
from app.engine.external_checks import run_fns_and_fedresurs, submit_fssp
from app.engine.features import compute_stage_a_features
from app.engine.freeze import find_active_sanctions_freeze
from app.engine.goods import compute_goods_features
from app.engine.limits import evaluate_deal_limits
from app.engine.rules import TriggeredRule, evaluate_hard_stops, evaluate_stage_b_pre_bki
from app.engine.state_machine import transition
from app.engine.versions import get_active_ruleset_version, load_config
from app.integrations.adapter import write_comment, write_decision_status
from app.integrations.base import PartnerClient
from app.integrations.status_mapping import get_reason_note, get_status_mapping, render_comment
from app.models import (
    Application,
    Check,
    Decision,
    ExternalSnapshot,
    FeatureSnapshot,
    Guarantor,
)
from app.sources.base import Subject
from app.sources.blacklist import BlacklistSource
from app.sources.factory import build_fedresurs_source, build_fns_source, build_fssp_source
from app.sources.fssp import FsspSource
from app.sources.mvd import MvdSource
from app.sources.rosfinmon import RosfinmonSource
from app.sources.runner import run_and_record_check

logger = structlog.get_logger()

STAGE_B_FEATURE_SET_VERSION = "stage_b.v1"


class NoStageBSnapshotError(Exception):
    """Нет ни одного снимка анкеты стадии Б — «Рассмотреть» ещё не нажималось
    либо адаптер его не забрал."""


def _guarantor_ocr_shape(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Данные поручителя приходят от оператора уже введёнными (без OCR) — но
    compute_stage_a_features ожидает форму {"поле": {"value": ...}}, как у
    распознавания. Оборачиваем, чтобы переиспользовать ту же проверенную
    логику формата/срока/года бланка/кода подразделения (T10 п.2)."""
    return {key: {"value": value} for key, value in payload.items() if value is not None}


async def _latest_stage_b_snapshot(session, application_id: UUID) -> ExternalSnapshot | None:
    return (
        await session.execute(
            select(ExternalSnapshot)
            .where(ExternalSnapshot.application_id == application_id, ExternalSnapshot.stage == "B")
            .order_by(ExternalSnapshot.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_checks_for_subject(session, application_id: UUID, subject_type: str, subject_id) -> dict:
    stmt = (
        select(Check)
        .where(
            Check.application_id == application_id,
            Check.subject_type == subject_type,
            Check.subject_id.is_(None) if subject_id is None else Check.subject_id == subject_id,
        )
        .order_by(Check.source, Check.started_at.desc())
        .distinct(Check.source)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {c.source: {"status": c.status, "parsed": c.parsed} for c in rows}


def _serialize_triggered_rules(rules: list[TriggeredRule]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id, "field": r.field, "op": r.op.value, "expected": r.expected,
            "actual": r.actual, "reason": r.reason, "action": r.action, "freeze": r.freeze,
        }
        for r in rules
    ]


async def _write_back(
    session_factory: async_sessionmaker, partner_client: PartnerClient, application: Application,
    our_status: str, *, reason_code: str | None = None, summary: str | None = None,
) -> None:
    mapping = get_status_mapping(our_status)
    comment = render_comment(mapping, reason_code=reason_code or "-", summary=summary or "")
    note = get_reason_note(reason_code)
    if note:
        comment = f"{comment} {note}"
    await write_decision_status(
        session_factory, partner_client, application_id=application.id, external_id=application.external_id,
        expected_current_status="on_review", new_status=mapping.partner_status, comment=comment,
    )


async def _resolve_guarantor(
    session, application: Application, guarantor_payload: dict[str, Any]
) -> Guarantor | None:
    """Создаёт/находит строку guarantors и привязывает к clients (T10 п.2) —
    точно так же, как find_or_create_client для заявителя, но по данным,
    введённым оператором (без OCR)."""
    last_name = guarantor_payload.get("last_name")
    first_name = guarantor_payload.get("first_name")
    middle_name = guarantor_payload.get("middle_name")
    birth_date_raw = guarantor_payload.get("birth_date")
    series = guarantor_payload.get("passport_series")
    number = guarantor_payload.get("passport_number")
    phone = guarantor_payload.get("phone")

    if not (last_name and first_name and birth_date_raw and series and number):
        return None

    client_id = None
    if phone:
        client, _changed = await find_or_create_client(
            session, last_name=last_name, first_name=first_name, middle_name=middle_name,
            birth_date=date.fromisoformat(birth_date_raw), passport_series=series,
            passport_number=number, phone=phone,
        )
        client_id = client.id

    guarantor = Guarantor(
        application_id=application.id, client_id=client_id, profile=guarantor_payload, status="checking",
    )
    session.add(guarantor)
    await session.flush()
    return guarantor


def _compute_guarantor_passport_features(config, guarantor_payload: dict) -> tuple[dict, Subject]:
    """Паспортные признаки поручителя по введённым оператором данным (без
    БД) — переиспользуется и там, где локальные проверки ещё НЕ запускались
    (run_stage_b), и там, где они уже записаны и нужно просто пересобрать
    features из уже готовых Check-строк (finalize_stage_b), не гоняя
    МВД/Росфинмониторинг/чёрный список повторно (правило 11 CLAUDE.md —
    идемпотентность, не повторяем проверки)."""
    ocr_shape = _guarantor_ocr_shape(guarantor_payload)
    features = compute_stage_a_features(
        ocr_shape,
        department_code_check_enabled=config.sources.passport.department_code_check_enabled,
        known_department_codes=frozenset(),  # см. открытый пункт в отчёте — справочник кодов не подключён к поручителю
        blank_year_max_issue_offset_years=config.sources.passport.blank_year_max_issue_offset_years,
    )

    subject = Subject(
        last_name=guarantor_payload.get("last_name"),
        first_name=guarantor_payload.get("first_name"),
        middle_name=guarantor_payload.get("middle_name"),
        birth_date=date.fromisoformat(guarantor_payload["birth_date"]) if guarantor_payload.get("birth_date") else None,
        passport_series=guarantor_payload.get("passport_series"),
        passport_number=guarantor_payload.get("passport_number"),
        phone=guarantor_payload.get("phone"),
    )
    return features, subject


async def _run_guarantor_local_checks(session, config, guarantor: Guarantor, guarantor_payload: dict) -> tuple[dict, Subject]:
    """Тот же набор, что и для заявителя на стадии А: паспортные признаки по
    введённым данным + МВД/Росфинмониторинг/чёрный список (T09/T10). Пишет
    Check-строки — вызывается один раз, из run_stage_b; finalize_stage_b
    читает уже записанное через _latest_checks_for_subject, не повторяя."""
    features, subject = _compute_guarantor_passport_features(config, guarantor_payload)

    sources = (
        MvdSource(session),
        RosfinmonSource(session, name_match_min_similarity=config.sources.rosfinmon.name_match_min_similarity),
        BlacklistSource(session),
    )
    for source in sources:
        await run_and_record_check(
            session, source, application_id=guarantor.application_id,
            subject_type="guarantor", subject_id=guarantor.id, subject=subject,
        )

    checks = await _latest_checks_for_subject(session, guarantor.application_id, "guarantor", guarantor.id)
    features["checks"] = {name: {"status": c["status"]} for name, c in checks.items()}

    return features, subject


async def run_stage_b(
    session_factory: async_sessionmaker, application_id: UUID, *, partner_client: PartnerClient,
    fns_source=None, fedresurs_source=None, fssp_source: FsspSource | None = None,
) -> str:
    settings = get_settings()
    fns_source = fns_source or build_fns_source(settings)
    fedresurs_source = fedresurs_source or build_fedresurs_source(settings)
    fssp_source = fssp_source or build_fssp_source(settings)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        if application is None:
            raise ValueError(f"заявка {application_id} не найдена")

        if application.status == "to_operator":
            transition(session, application, "stage_b", actor_type="system")
            await session.commit()

        ruleset_row = await get_active_ruleset_version(session)
        config = load_config(ruleset_row)

        snapshot = await _latest_stage_b_snapshot(session, application_id)
        if snapshot is None:
            raise NoStageBSnapshotError(f"нет снимка анкеты стадии Б для {application_id}")
        stage_b_payload = (snapshot.payload_raw or {}).get("stage_b") or {}

        missing_sections = [
            section for section in config.stage_b_completeness.required_sections
            if not stage_b_payload.get(section)
        ]
        if missing_sections:
            transition(session, application, "to_operator", actor_type="system")
            await session.commit()
            await _write_back(
                session_factory, partner_client, application, "stage_b_incomplete",
                summary=", ".join(missing_sections),
            )
            logger.info("stage_b_incomplete", application_id=str(application_id), missing=missing_sections)
            return application.status

        deal = stage_b_payload.get("deal") or {}

        # Шариатский фильтр — до лимитов (ТЗ: порядок по стоимости проверки).
        goods_features = await compute_goods_features(session, deal.get("goods_category_code"))
        sharia_outcome = evaluate_stage_b_pre_bki(config, goods_features)
        if sharia_outcome.matched:
            return await _reject_stage_b(
                session, session_factory, application, ruleset_row, sharia_outcome.reason_code,
                _serialize_triggered_rules(sharia_outcome.triggered_rules), goods_features, partner_client,
            )

        # Лимиты сделки + альтернативный тариф (T10, "только по лимитам").
        client_history = await _client_history_snapshot(session, application)
        is_repeat = bool(client_history.get("is_repeat", False))
        limits_outcome = evaluate_deal_limits(
            requested_tariff=application.tariff, goods_price=application.goods_price,
            down_payment=application.down_payment, term_months=application.term_months,
            is_repeat=is_repeat, limits=config.limits, tariffs=config.tariffs,
        )
        if not limits_outcome.passed:
            return await _reject_stage_b(
                session, session_factory, application, ruleset_row, limits_outcome.reason_code,
                [], goods_features, partner_client,
            )

        # Согласие на КИ — наличие и срок действия (ТЗ, «Согласия»): серая зона,
        # не отказ, запрос в БКИ просто невозможен.
        if not _consent_bki_valid(application, config.bki.consent_max_age_days):
            return await _gray_zone_stage_b(
                session, session_factory, application, ruleset_row, "BKI_CONSENT_MISSING_OR_EXPIRED",
                goods_features, partner_client,
            )

        # Поручитель — опционален; при наличии полный набор проверок, включая
        # внешние (T10 п.2). Локальный hard_stop по нему -> решение без него,
        # не отказ по всей заявке.
        guarantor_payload = stage_b_payload.get("guarantor")
        if guarantor_payload:
            guarantor = await _resolve_guarantor(session, application, guarantor_payload)
            if guarantor is not None:
                guarantor_freeze = (
                    await find_active_sanctions_freeze(session, client_id=guarantor.client_id)
                    if guarantor.client_id else None
                )
                guarantor_features, guarantor_subject = await _run_guarantor_local_checks(
                    session, config, guarantor, guarantor_payload
                )
                guarantor_stop = evaluate_hard_stops(config, guarantor_features)
                guarantor_ok = guarantor_freeze is None and not guarantor_stop.matched
                await session.commit()

                if guarantor_ok:
                    await run_fns_and_fedresurs(
                        session, settings=settings, application_id=application.id,
                        subject_type="guarantor", subject_id=guarantor.id, subject=guarantor_subject,
                        fns_source=fns_source, fedresurs_source=fedresurs_source,
                    )
                    fssp_settled = await submit_fssp(
                        session, settings, application_id=application.id, subject_type="guarantor",
                        subject_id=guarantor.id, stage="B", subject=guarantor_subject, fssp_source=fssp_source,
                    )
                    await session.commit()
                    if not fssp_settled:
                        logger.info("stage_b_awaiting_guarantor_fssp", application_id=str(application_id))
                        return application.status

        await session.commit()

    return await finalize_stage_b(session_factory, application_id, partner_client=partner_client)


async def finalize_stage_b(
    session_factory: async_sessionmaker, application_id: UUID, *, partner_client: PartnerClient
) -> str:
    """Хвост стадии Б: пересобирает поручителя (если был) с уже готовыми
    внешними проверками, пишет решение/остаётся в ожидании БКИ. Идемпотентна
    так же, как finalize_stage_a — SELECT ... FOR UPDATE на applications."""
    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.id == application_id).with_for_update())
        ).scalar_one()

        if application.status != "stage_b":
            return application.status

        ruleset_row = await get_active_ruleset_version(session)
        config = load_config(ruleset_row)

        snapshot = await _latest_stage_b_snapshot(session, application_id)
        stage_b_payload = (snapshot.payload_raw or {}).get("stage_b") or {} if snapshot else {}
        deal = stage_b_payload.get("deal") or {}
        goods_features = await compute_goods_features(session, deal.get("goods_category_code"))

        # Альтернативный тариф (T10): если лимиты прошли не по запрошенному, а по
        # другому тарифу — это не отказ, а предложение. run_stage_b уже пропустил
        # эту заявку дальше (limits_outcome.passed), здесь пересчитываем для
        # финального features-снапшота и комментария оператору — тем же способом,
        # каким finalize_stage_a пересчитывает признаки заново, самодостаточно.
        client_history = await _client_history_snapshot(session, application)
        limits_outcome = evaluate_deal_limits(
            requested_tariff=application.tariff, goods_price=application.goods_price,
            down_payment=application.down_payment, term_months=application.term_months,
            is_repeat=bool(client_history.get("is_repeat", False)), limits=config.limits, tariffs=config.tariffs,
        )
        tariff_offer_comment = None
        if limits_outcome.is_alternative:
            goods_features["tariff_offer"] = {
                "tariff": limits_outcome.tariff,
                "required_down_pay": float(limits_outcome.required_down_pay),
            }
            tariff_offer_comment = (
                f"По запрошенному тарифу заявка не проходит по лимитам. Предложение: "
                f"тариф «{limits_outcome.tariff}», первый взнос от "
                f"{limits_outcome.required_down_pay}. Обсудить с клиентом."
            )

        guarantor_payload = stage_b_payload.get("guarantor")
        guarantor_used = False
        guarantor_reason_code = None
        if guarantor_payload:
            guarantor = (
                await session.execute(select(Guarantor).where(Guarantor.application_id == application_id))
            ).scalars().first()
            if guarantor is not None:
                guarantor_freeze = (
                    await find_active_sanctions_freeze(session, client_id=guarantor.client_id)
                    if guarantor.client_id else None
                )
                guarantor_features, _subj = _compute_guarantor_passport_features(config, guarantor_payload)
                checks = await _latest_checks_for_subject(session, application_id, "guarantor", guarantor.id)
                guarantor_features["checks"] = {n: {"status": c["status"]} for n, c in checks.items()}
                guarantor_stop = evaluate_hard_stops(config, guarantor_features)
                if guarantor_freeze:
                    guarantor_used = False
                    guarantor_reason_code = guarantor_freeze
                elif guarantor_stop.matched:
                    guarantor_used = False
                    guarantor_reason_code = guarantor_stop.reason_code
                else:
                    guarantor_used = True
                guarantor.status = "used" if guarantor_used else "rejected"

        features = {**goods_features, "guarantor_used": guarantor_used}
        if guarantor_reason_code:
            features["guarantor_reason_code"] = guarantor_reason_code

        feature_snapshot = FeatureSnapshot(
            application_id=application_id, stage="B", features=features,
            feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
        )
        session.add(feature_snapshot)
        await session.flush()

        logger.info(
            "stage_b_checks_passed_awaiting_bki",
            application_id=str(application_id), guarantor_used=guarantor_used,
        )
        # Никакого Decision и перехода статуса: approved требует БКИ (T11).
        # Заявка остаётся в "stage_b" — готова к запросу БКИ.
        await session.commit()
        external_id = application.external_id

    if tariff_offer_comment:
        # Вне блока сессии: write_comment сам пишет в integration_events через
        # НОВУЮ сессию — если делать это, пока строка applications ещё
        # заблокирована SELECT ... FOR UPDATE выше, INSERT в integration_events
        # (FK на applications) ждёт снятия блокировки, которая снимется только
        # после закрытия ЭТОЙ сессии — самоблокировка (поймано вживую при
        # прогоне тестов, см. отчёт T10).
        await write_comment(
            session_factory, partner_client, application_id=application_id, external_id=external_id,
            text=tariff_offer_comment,
        )

    return application.status


async def _reject_stage_b(
    session, session_factory, application, ruleset_row, reason_code, triggered_rules, goods_features, partner_client,
) -> str:
    feature_snapshot = FeatureSnapshot(
        application_id=application.id, stage="B", features=goods_features, feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
    )
    session.add(feature_snapshot)
    await session.flush()

    decision = Decision(
        application_id=application.id, stage="B", outcome="reject", reason_code=reason_code,
        triggered_rules=triggered_rules, ruleset_version_id=ruleset_row.id, feature_snapshot_id=feature_snapshot.id,
    )
    session.add(decision)
    await session.flush()

    transition(session, application, "rejected_b", actor_type="system")
    await session.commit()

    await _write_back(session_factory, partner_client, application, "rejected_b", reason_code=reason_code)
    return application.status


async def _gray_zone_stage_b(
    session, session_factory, application, ruleset_row, reason_code, goods_features, partner_client,
) -> str:
    feature_snapshot = FeatureSnapshot(
        application_id=application.id, stage="B", features=goods_features, feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
    )
    session.add(feature_snapshot)
    await session.flush()

    decision = Decision(
        application_id=application.id, stage="B", outcome="manual_review", reason_code=reason_code,
        triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=feature_snapshot.id,
    )
    session.add(decision)
    await session.flush()

    transition(session, application, "gray_zone", actor_type="system")
    await session.commit()

    await _write_back(session_factory, partner_client, application, "gray_zone", reason_code=reason_code)
    return application.status


def _consent_bki_valid(application: Application, max_age_days: int) -> bool:
    if application.consent_bki_at is None:
        return False
    age = datetime.now(timezone.utc) - application.consent_bki_at
    return age.days <= max_age_days


async def _client_history_snapshot(session, application: Application) -> dict:
    """is_repeat уже посчитан и сохранён в последнем снапшоте стадии А —
    пересчитывать не нужно, читаем оттуда (см. app.engine.clients, T09)."""
    if application.client_id is None:
        return {}
    snapshot = (
        await session.execute(
            select(FeatureSnapshot)
            .where(FeatureSnapshot.application_id == application.id, FeatureSnapshot.stage == "A")
            .order_by(FeatureSnapshot.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return snapshot.features if snapshot else {}
