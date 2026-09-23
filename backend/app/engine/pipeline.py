"""Оркестратор стадии А: заявка уже получена адаптером (T04) -> распознавание ->
признаки -> движок -> решение -> статус в систему заказчика (T07)."""

from datetime import date
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.features import STAGE_A_FEATURE_SET_VERSION, compute_stage_a_features
from app.engine.rules import TriggeredRule, evaluate_hard_stops
from app.engine.schema import RulesetConfig
from app.engine.state_machine import transition
from app.engine.versions import get_active_ruleset_version, load_config
from app.integrations.adapter import write_decision_status
from app.integrations.base import PartnerClient
from app.integrations.status_mapping import get_status_mapping, render_comment
from app.models import Application, Decision, Document, FeatureSnapshot
from app.ocr.base import OcrProvider
from app.ocr.service import run_ocr
from app.sources.base import Subject
from app.sources.blacklist import BlacklistSource
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


async def _run_local_checks(
    session, config: RulesetConfig, application: Application, subject: Subject
) -> dict[str, dict[str, str]]:
    """МВД, Росфинмониторинг, чёрный список — локальные источники стадии А, шаг 2
    (ТЗ; T08). Каждый — через SourceBase, результат в checks и в признаки движка."""
    sources = (
        MvdSource(session),
        RosfinmonSource(session, name_match_min_similarity=config.sources.rosfinmon.name_match_min_similarity),
        BlacklistSource(session),
    )
    checks: dict[str, dict[str, str]] = {}
    for source in sources:
        check = await run_and_record_check(
            session,
            source,
            application_id=application.id,
            subject_type="applicant",
            subject_id=None,
            subject=subject,
        )
        checks[source.source_name] = {"status": check.status}
    return checks


async def _write_back(
    session_factory: async_sessionmaker,
    partner_client: PartnerClient,
    application: Application,
    our_status: str,
    *,
    reason_code: str | None,
) -> None:
    mapping = get_status_mapping(our_status)
    comment = render_comment(mapping, reason_code=reason_code or "-")
    await write_decision_status(
        session_factory,
        partner_client,
        application_id=application.id,
        external_id=application.external_id,
        expected_current_status=_EXPECTED_PARTNER_STATUS_ON_INGEST,
        new_status=mapping.partner_status,
        comment=comment,
    )


async def run_stage_a(
    session_factory: async_sessionmaker,
    application_id: UUID,
    *,
    ocr_provider: OcrProvider,
    partner_client: PartnerClient,
    storage: Storage,
) -> str:
    """Прогоняет одну заявку через стадию А целиком. Возвращает финальный статус."""
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

        features = compute_stage_a_features(
            ocr_outcome.fields,
            department_code_check_enabled=config.sources.passport.department_code_check_enabled,
        )
        subject = _subject_from_ocr(ocr_outcome.fields, application)
        features["checks"] = await _run_local_checks(session, config, application, subject)
        await session.commit()

        stop_outcome = evaluate_hard_stops(config, features)

        feature_snapshot = FeatureSnapshot(
            application_id=application.id,
            stage="A",
            features=features,
            feature_set_ver=STAGE_A_FEATURE_SET_VERSION,
        )
        session.add(feature_snapshot)
        await session.flush()

        decision = Decision(
            application_id=application.id,
            stage="A",
            outcome="reject" if stop_outcome.matched else "manual_review",
            reason_code=stop_outcome.reason_code,
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
            application_id=str(application.id),
            outcome=decision.outcome,
            reason_code=decision.reason_code,
            new_status=new_status,
        )

        await _write_back(
            session_factory, partner_client, application, new_status, reason_code=stop_outcome.reason_code
        )
        return application.status
