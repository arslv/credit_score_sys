"""Оркестратор стадии А: заявка уже получена адаптером (T04) -> распознавание ->
признаки -> движок -> решение -> статус в систему заказчика (T07).

T09: внешние источники шага 3 (ФНС, Федресурс, ФССП) добавляют вторую точку
входа, finalize_stage_a — общий "хвост" (признаки -> движок -> решение),
вызываемый и из run_stage_a напрямую (быстрый путь), и из периодической задачи
опроса ФССП (app/tasks/fssp_poll.py, отложенный путь). Идемпотентность и защита
от двойной финализации — через SELECT ... FOR UPDATE на строке applications."""

from datetime import date
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.engine.clients import compute_client_history_features, find_or_create_client
from app.engine.department_codes import load_department_codes
from app.engine.duplicates import is_passport_reused_with_different_identity
from app.engine.external_checks import run_fns_and_fedresurs, submit_fssp
from app.engine.features import STAGE_A_FEATURE_SET_VERSION, compute_stage_a_features
from app.engine.freeze import find_active_sanctions_freeze
from app.engine.fssp import summarize_productions
from app.engine.rules import TriggeredRule, evaluate_hard_stops
from app.engine.schema import RulesetConfig
from app.engine.state_machine import transition
from app.engine.versions import get_active_ruleset_version, load_config
from app.integrations.adapter import write_decision_status
from app.integrations.base import PartnerClient
from app.integrations.status_mapping import build_operator_comment, get_status_mapping, render_comment
from app.models import Application, Check, Client, Decision, Document, FeatureSnapshot, OcrResult
from app.ocr.base import OcrProvider
from app.ocr.service import run_ocr
from app.sources.base import Subject
from app.sources.blacklist import BlacklistSource
from app.sources.factory import build_fedresurs_source, build_fns_source, build_fssp_source
from app.sources.fssp import FsspSource
from app.sources.mvd import MvdSource
from app.sources.rosfinmon import RosfinmonSource
from app.sources.runner import run_and_record_check
from app.storage.base import Storage

logger = structlog.get_logger()

# Заявка ингестится адаптером из списка «новых» (status_="new"), поэтому на момент
# первой записи статуса стадии А мы вправе ожидать именно этот статус у партнёра.
_EXPECTED_PARTNER_STATUS_ON_INGEST = "new"


class NoActiveRulesetError(Exception):
    """Нет активной версии конфига правил на текущий момент — решение принять нельзя."""


class DocumentNotFoundError(Exception):
    def __init__(self, application_id: UUID, kind: str) -> None:
        self.application_id = application_id
        self.kind = kind
        super().__init__(f"нет документа {kind!r} для заявки {application_id}")


def _serialize_triggered_rules(rules: list[TriggeredRule]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "field": r.field,
            "op": r.op.value,
            "expected": r.expected,
            "actual": r.actual,
            "reason": r.reason,
            "action": r.action,
            "freeze": r.freeze,
        }
        for r in rules
    ]


async def _load_photo(session, storage: Storage, application_id: UUID, kind: str) -> bytes:
    document = (
        await session.execute(
            select(Document)
            .where(Document.application_id == application_id, Document.kind == kind)
            .order_by(Document.uploaded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if document is None:
        raise DocumentNotFoundError(application_id, kind)
    return await storage.read(document.storage_key)


def _subject_from_ocr(ocr_fields: dict, application: Application) -> Subject:
    def value(name: str) -> str | None:
        return (ocr_fields.get(name) or {}).get("value")

    birth_date_raw = value("birth_date")
    return Subject(
        last_name=value("last_name"),
        first_name=value("first_name"),
        middle_name=value("middle_name"),
        birth_date=date.fromisoformat(birth_date_raw) if birth_date_raw else None,
        passport_series=value("passport_series"),
        passport_number=value("passport_number"),
        phone=(application.profile or {}).get("phone"),
    )


async def _resolve_client(session, application: Application, ocr_fields: dict) -> UUID | None:
    """Привязывает application.client_id, если данных достаточно (ФИО, дата
    рождения, паспорт, телефон) — иначе оставляет None, как и при ocr_failed:
    привязка появится позже вместе с данными, не подставляем частичную запись
    (T09, п. «подключение таблицы clients»). Не коммитит — на совести
    вызывающего кода."""

    def value(name: str) -> str | None:
        return (ocr_fields.get(name) or {}).get("value")

    last_name = value("last_name")
    first_name = value("first_name")
    middle_name = value("middle_name")
    birth_date_raw = value("birth_date")
    series = value("passport_series")
    number = value("passport_number")
    phone = (application.profile or {}).get("phone")

    if not (last_name and first_name and birth_date_raw and series and number and phone):
        return None

    client, _passport_changed = await find_or_create_client(
        session,
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        birth_date=date.fromisoformat(birth_date_raw),
        passport_series=series,
        passport_number=number,
        phone=phone,
    )
    application.client_id = client.id
    await session.flush()
    return client.id


async def _compute_local_features(
    session, config: RulesetConfig, application: Application, ocr_fields: dict
) -> tuple[dict[str, Any], Subject]:
    """Признаки по паспорту + локальные источники (МВД/Росфинмониторинг/чёрный
    список) + внутренние дубли — всё, что не требует внешнего HTTP (T07/T08)."""
    department_code_check_enabled = config.sources.passport.department_code_check_enabled
    known_department_codes = (
        load_department_codes(get_settings().department_codes_path)
        if department_code_check_enabled
        else frozenset()
    )
    features = compute_stage_a_features(
        ocr_fields,
        department_code_check_enabled=department_code_check_enabled,
        known_department_codes=known_department_codes,
        blank_year_max_issue_offset_years=config.sources.passport.blank_year_max_issue_offset_years,
    )

    subject = _subject_from_ocr(ocr_fields, application)

    sources = (
        MvdSource(session),
        RosfinmonSource(session, name_match_min_similarity=config.sources.rosfinmon.name_match_min_similarity),
        BlacklistSource(session),
    )
    for source in sources:
        await run_and_record_check(
            session,
            source,
            application_id=application.id,
            subject_type="applicant",
            subject_id=None,
            subject=subject,
        )

    if subject.passport_series and subject.passport_number and subject.last_name and subject.first_name:
        features["passport_reused"] = await is_passport_reused_with_different_identity(
            session,
            application_id=application.id,
            passport_series=subject.passport_series,
            passport_number=subject.passport_number,
            last_name=subject.last_name,
            first_name=subject.first_name,
            middle_name=subject.middle_name,
            birth_date=(ocr_fields.get("birth_date") or {}).get("value"),
        )

    return features, subject


async def _latest_checks(session, application_id: UUID) -> dict[str, dict[str, Any]]:
    stmt = (
        select(Check)
        .where(Check.application_id == application_id)
        .order_by(Check.source, Check.started_at.desc())
        .distinct(Check.source)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {c.source: {"status": c.status, "parsed": c.parsed} for c in rows}


async def _write_back(
    session_factory: async_sessionmaker,
    partner_client: PartnerClient,
    application: Application,
    our_status: str,
    *,
    reason_code: str | None,
    triggered_rules: list[dict[str, Any]] | None = None,
) -> None:
    mapping = get_status_mapping(our_status)
    reason_text = build_operator_comment(reason_code=reason_code, triggered_rules=triggered_rules)
    comment = render_comment(mapping, reason_text=reason_text)
    await write_decision_status(
        session_factory,
        partner_client,
        application_id=application.id,
        external_id=application.external_id,
        expected_current_status=_EXPECTED_PARTNER_STATUS_ON_INGEST,
        new_status=mapping.partner_status,
        comment=comment,
    )


async def _reject_for_active_freeze(
    session,
    session_factory: async_sessionmaker,
    application: Application,
    ruleset_row,
    reason_code: str,
    partner_client: PartnerClient,
) -> str:
    """Заявка того же паспорта, что уже был заморожен раньше (T09, п. «Отдельно —
    заморозка при санкциях»): отказ с тем же reason_code, без единой проверки.
    error, а не info — это не техническая ошибка, а событие, которое должно
    быть видно в мониторинге."""
    logger.error(
        "sanctions_freeze_reapplied",
        application_id=str(application.id),
        reason_code=reason_code,
    )
    decision = Decision(
        application_id=application.id,
        stage="A",
        outcome="reject",
        reason_code=reason_code,
        freeze=True,
        triggered_rules=[],
        ruleset_version_id=ruleset_row.id,
        feature_snapshot_id=None,
    )
    session.add(decision)
    await session.flush()

    transition(session, application, "rejected_a", actor_type="system")
    await session.commit()

    await _write_back(
        session_factory, partner_client, application, "rejected_a", reason_code=reason_code,
    )
    return application.status


async def run_stage_a(
    session_factory: async_sessionmaker,
    application_id: UUID,
    *,
    ocr_provider: OcrProvider,
    partner_client: PartnerClient,
    storage: Storage,
    fns_source=None,
    fedresurs_source=None,
    fssp_source: FsspSource | None = None,
) -> str:
    """Прогоняет заявку через стадию А до точки, откуда решение можно принять
    сразу (локальный hard_stop уже сработал, или ФССП не принял запрос), либо
    до постановки в очередь на опрос ФССП. Возвращает статус заявки на этот
    момент — не обязательно финальный, если ФССП ещё не ответил (T09)."""
    settings = get_settings()
    fns_source = fns_source or build_fns_source(settings)
    fedresurs_source = fedresurs_source or build_fedresurs_source(settings)
    fssp_source = fssp_source or build_fssp_source(settings)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        if application is None:
            raise ValueError(f"заявка {application_id} не найдена")

        transition(session, application, "ocr", actor_type="system")
        await session.commit()

        passport_main = await _load_photo(session, storage, application.id, "passport_main")
        passport_reg = await _load_photo(session, storage, application.id, "passport_reg")

        ocr_outcome = await run_ocr(
            session,
            ocr_provider,
            application=application,
            passport_main=passport_main,
            passport_reg=passport_reg,
        )
        await session.commit()

        if ocr_outcome.below_threshold:
            # Уходит оператору без проверок (ТЗ, «Стадия А, шаг 1. OCR»).
            transition(session, application, "ocr_failed", actor_type="system")
            transition(session, application, "to_operator", actor_type="system")
            await session.commit()

            logger.info(
                "stage_a_ocr_failed",
                application_id=str(application.id),
                min_confidence=ocr_outcome.min_confidence,
            )
            await _write_back(session_factory, partner_client, application, "ocr_failed", reason_code=None)
            return application.status

        transition(session, application, "stage_a", actor_type="system")
        await session.commit()

        ruleset_row = await get_active_ruleset_version(session)
        if ruleset_row is None:
            raise NoActiveRulesetError("нет активной версии конфига правил")
        config = load_config(ruleset_row)

        # Клиент (T09, довершает T02): ищется/создаётся сразу после успешного
        # OCR — только неполных данных достаточно, чтобы пропустить (останется
        # без привязки, как и при ocr_failed; привязка — позже, когда данные
        # появятся). Заморозка ниже работает по client_id, поэтому клиент
        # нужен раньше неё.
        client_id = await _resolve_client(session, application, ocr_outcome.fields)

        # Заморозка (T09): тот же клиент уже давал санкционный хит раньше (тем
        # же паспортом или другим, см. app.engine.clients) — отказ немедленно,
        # без единой новой проверки.
        if client_id is not None:
            freeze_reason = await find_active_sanctions_freeze(session, client_id=client_id)
            if freeze_reason:
                return await _reject_for_active_freeze(
                    session, session_factory, application, ruleset_row, freeze_reason, partner_client
                )

        features, subject = await _compute_local_features(session, config, application, ocr_outcome.fields)
        features["checks"] = {
            source: {"status": status["status"]} for source, status in (await _latest_checks(session, application.id)).items()
        }
        await session.commit()

        # T09, п.1: локальный hard_stop уже решает всё — внешние источники шага 3
        # не вызываются вовсе (ни ФНС/Федресурс, ни тем более заявка на ФССП).
        local_stop_outcome = evaluate_hard_stops(config, features)
        if local_stop_outcome.matched:
            return await finalize_stage_a(session_factory, application_id, partner_client=partner_client)

        await run_fns_and_fedresurs(
            session,
            settings=settings,
            application_id=application.id,
            subject_type="applicant",
            subject_id=None,
            subject=subject,
            fns_source=fns_source,
            fedresurs_source=fedresurs_source,
        )
        fssp_settled = await submit_fssp(
            session,
            settings,
            application_id=application.id,
            subject_type="applicant",
            subject_id=None,
            stage="A",
            subject=subject,
            fssp_source=fssp_source,
        )
        await session.commit()

    if fssp_settled:
        return await finalize_stage_a(session_factory, application_id, partner_client=partner_client)

    logger.info("stage_a_awaiting_fssp", application_id=str(application_id))
    return application.status


async def finalize_stage_a(
    session_factory: async_sessionmaker, application_id: UUID, *, partner_client: PartnerClient
) -> str:
    """Хвост стадии А: признаки -> движок -> решение -> статус -> запись в
    систему заказчика. Общий для быстрого пути (локальный hard_stop, или ФССП
    сразу отказал в приёме запроса) и отложенного (опрос ФССП разрешился или
    истёк дедлайн). Идемпотентна: SELECT ... FOR UPDATE на строке applications
    гарантирует, что при двух параллельных вызовах только один довершит
    решение — второй увидит статус, отличный от "stage_a", и не сделает ничего
    (T09, п.3 поправок)."""
    async with session_factory() as session:
        application = (
            await session.execute(
                select(Application).where(Application.id == application_id).with_for_update()
            )
        ).scalar_one()

        if application.status != "stage_a":
            return application.status

        ruleset_row = await get_active_ruleset_version(session)
        if ruleset_row is None:
            raise NoActiveRulesetError("нет активной версии конфига правил")
        config = load_config(ruleset_row)

        ocr_result = (
            await session.execute(
                select(OcrResult)
                .where(OcrResult.application_id == application_id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        ocr_fields = dict((ocr_result.fields or {}).get("fields") or {})

        department_code_check_enabled = config.sources.passport.department_code_check_enabled
        known_department_codes = (
            load_department_codes(get_settings().department_codes_path)
            if department_code_check_enabled
            else frozenset()
        )
        features = compute_stage_a_features(
            ocr_fields,
            department_code_check_enabled=department_code_check_enabled,
            known_department_codes=known_department_codes,
            blank_year_max_issue_offset_years=config.sources.passport.blank_year_max_issue_offset_years,
        )

        subject = _subject_from_ocr(ocr_fields, application)
        if subject.passport_series and subject.passport_number and subject.last_name and subject.first_name:
            features["passport_reused"] = await is_passport_reused_with_different_identity(
                session,
                application_id=application_id,
                passport_series=subject.passport_series,
                passport_number=subject.passport_number,
                last_name=subject.last_name,
                first_name=subject.first_name,
                middle_name=subject.middle_name,
                birth_date=(ocr_fields.get("birth_date") or {}).get("value"),
            )

        # Клиент (T09): применение уже привязанного client_id (сама привязка —
        # в run_stage_a, до постановки в очередь на ФССП). is_repeat — имя поля
        # фиксировано матрицей в dev.yaml.
        if application.client_id is not None:
            features.update(
                await compute_client_history_features(
                    session, client_id=application.client_id, exclude_application_id=application_id
                )
            )
            client = await session.get(Client, application.client_id)
            features["client_passport_changed"] = bool(
                client
                and subject.passport_series
                and subject.passport_number
                and (client.passport_series, client.passport_number)
                != (subject.passport_series, subject.passport_number)
            )

        checks = await _latest_checks(session, application_id)
        features["checks"] = {source: {"status": c["status"]} for source, c in checks.items()}

        # Вложенный неймспейс "features" (не опечатка) — так же, как features.pti,
        # уже задумано в ТЗ/dev.yaml с T06: hard_stop fssp_credit_debt и soft_rule
        # fssp_minor ссылаются именно на "features.fssp_*", а не на плоский путь.
        fssp_check = checks.get("fssp")
        if fssp_check and fssp_check.get("parsed"):
            productions = fssp_check["parsed"].get("productions", [])
            features["features"] = summarize_productions(productions, config.sources.fssp.classification)

        stop_outcome = evaluate_hard_stops(config, features)

        feature_snapshot = FeatureSnapshot(
            application_id=application_id,
            stage="A",
            features=features,
            feature_set_ver=STAGE_A_FEATURE_SET_VERSION,
        )
        session.add(feature_snapshot)
        await session.flush()

        if stop_outcome.freeze:
            # error, а не info — заморозка должна быть видна в мониторинге, не
            # затеряться среди обычных логов решений (T09).
            logger.error(
                "sanctions_freeze_triggered",
                application_id=str(application_id),
                reason_code=stop_outcome.reason_code,
            )

        decision = Decision(
            application_id=application_id,
            stage="A",
            outcome="reject" if stop_outcome.matched else "manual_review",
            reason_code=stop_outcome.reason_code,
            freeze=stop_outcome.freeze,
            triggered_rules=_serialize_triggered_rules(stop_outcome.triggered_rules),
            ruleset_version_id=ruleset_row.id,
            feature_snapshot_id=feature_snapshot.id,
        )
        session.add(decision)
        await session.flush()

        new_status = "rejected_a" if stop_outcome.matched else "to_operator"
        transition(session, application, new_status, actor_type="system")
        await session.commit()

        logger.info(
            "stage_a_decision",
            application_id=str(application_id),
            outcome=decision.outcome,
            reason_code=decision.reason_code,
            new_status=new_status,
        )
        reason_code = stop_outcome.reason_code
        triggered_rules = _serialize_triggered_rules(stop_outcome.triggered_rules)

    await _write_back(
        session_factory, partner_client, application, new_status,
        reason_code=reason_code, triggered_rules=triggered_rules,
    )
    return application.status
