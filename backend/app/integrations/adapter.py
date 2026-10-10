import asyncio
import hashlib
import json
from decimal import Decimal
from typing import Any, Awaitable, Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.integrations.base import (
    PartnerConflictError,
    PartnerUnavailableError,
)
from app.integrations.partner_notifier import PartnerNotifier
from app.models import Decision, IntegrationEvent, ManualOverride, PartnerOutboxEvent

logger = structlog.get_logger()


def content_hash(payload: dict[str, Any]) -> str:
    """T18: больше не идемпотентность (та — по event_id на вебхуке), только
    подсказка фронту для схлопывания одинаковых снимков подряд
    (frontend/src/components/SnapshotHistory.tsx) — ExternalSnapshot.content_hash
    всё ещё NOT NULL и используется для отображения."""
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _log_event(
    session_factory: async_sessionmaker,
    *,
    application_id,
    direction: str,
    operation: str,
    status: str,
    attempt: int,
    response: dict[str, Any] | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            IntegrationEvent(
                application_id=application_id,
                direction=direction,
                operation=operation,
                status=status,
                attempt=attempt,
                response=response,
            )
        )
        await session.commit()


async def _call_with_retry(
    session_factory: async_sessionmaker,
    *,
    application_id,
    direction: str,
    operation: str,
    func: Callable[[], Awaitable[Any]],
) -> Any:
    """Несколько попыток подряд с нарастающей задержкой (счастливый путь —
    партнёр отвечает сразу, как и раньше). Каждая попытка — своя строка в
    integration_events. PartnerConflictError не перехватывается — это не
    временный сбой. Если и последняя попытка — PartnerUnavailableError,
    исключение уходит вызывающему коду (write_decision_status/write_comment
    ниже), который кладёт событие в outbox вместо того, чтобы его потерять
    (T18, ваше явное указание «ничего не теряем»)."""
    settings = get_settings()
    delay = settings.integration_retry_base_delay_seconds

    for attempt in range(1, settings.integration_retry_max_attempts + 1):
        try:
            result = await func()
        except PartnerUnavailableError as exc:
            is_last = attempt == settings.integration_retry_max_attempts
            await _log_event(
                session_factory,
                application_id=application_id,
                direction=direction,
                operation=operation,
                status="error" if is_last else "retry",
                attempt=attempt,
                response={"error": str(exc)},
            )
            if is_last:
                raise
            await asyncio.sleep(min(delay, settings.integration_retry_max_delay_seconds))
            delay *= 2
            continue
        else:
            await _log_event(
                session_factory,
                application_id=application_id,
                direction=direction,
                operation=operation,
                status="ok",
                attempt=attempt,
            )
            return result

    raise AssertionError("unreachable")  # pragma: no cover


async def _fallback_to_outbox(
    session_factory: async_sessionmaker,
    *,
    application_id,
    external_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Немедленная доставка исчерпала попытки — строка остаётся pending,
    подбирается повторами app/tasks/partner_outbox.py (растущий бэкофф, без
    верхней границы попыток: «ничего не теряем»)."""
    async with session_factory() as session:
        session.add(
            PartnerOutboxEvent(
                application_id=application_id,
                external_id=external_id,
                event_type=event_type,
                payload=payload,
            )
        )
        await session.commit()
    logger.warning(
        "partner_outbound_deferred_to_outbox",
        application_id=str(application_id), external_id=external_id, event_type=event_type,
    )


async def write_decision_status(
    session_factory: async_sessionmaker,
    notifier: PartnerNotifier,
    *,
    application_id,
    external_id: str,
    expected_current_status: str | None,
    new_status: str,
    comment: str | None = None,
    score: Decimal | int | float | None = None,
) -> str:
    """Пишет наш исход в систему заказчика. Если статус там уже не совпадает
    с prev_status (оператор сменил вручную), запись отменяется — событие
    conflict (правило 12 CLAUDE.md); current_status/reason — из тела ИХ
    409-ответа (PartnerConflictError), не из отдельного запроса."""
    try:
        await _call_with_retry(
            session_factory,
            application_id=application_id,
            direction="outbound",
            operation="write_status",
            func=lambda: notifier.write_status(
                external_id, expected_current_status, new_status, comment, score=score,
            ),
        )
    except PartnerConflictError as exc:
        await _log_event(
            session_factory,
            application_id=application_id,
            direction="outbound",
            operation="write_status",
            status="conflict",
            attempt=1,
            response={"current_status": exc.current_status, "reason": exc.reason},
        )
        await _record_manual_override(
            session_factory, application_id=application_id,
            our_status=new_status, actual_status=exc.current_status, operator_reason=exc.reason,
        )
        return "conflict"
    except PartnerUnavailableError:
        await _fallback_to_outbox(
            session_factory, application_id=application_id, external_id=external_id,
            event_type="decision",
            payload={
                "prev_status": expected_current_status, "status": new_status,
                "reason": comment, "score": int(score) if score is not None else None,
            },
        )
        return "deferred"
    return "ok"


async def _record_manual_override(
    session_factory: async_sessionmaker, *, application_id, our_status: str,
    actual_status: str | None, operator_reason: str | None,
) -> None:
    """T10: оператор сменил статус вручную до нашей записи (правило 12
    CLAUDE.md, PartnerConflictError выше) — это и есть его решение. Пишем
    расхождение в manual_overrides отдельной строкой, БЕЗ сотрудника-актора с
    нашей стороны (employee_id=NULL — см. app/models/override.py)."""
    reason_code = (
        "PARTNER_OPERATOR_OVERRIDE_APPROVE" if actual_status == "approved" else "PARTNER_OPERATOR_OVERRIDE_REJECT"
    )

    async with session_factory() as session:
        decision_id = (
            await session.execute(
                select(Decision.id)
                .where(Decision.application_id == application_id)
                .order_by(Decision.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        session.add(
            ManualOverride(
                application_id=application_id,
                decision_id=decision_id,
                employee_id=None,
                from_outcome=our_status,
                to_outcome=actual_status or "unknown",
                reason_code=reason_code,
                comment=operator_reason,
            )
        )
        await session.commit()

    logger.info(
        "manual_override_recorded", application_id=str(application_id),
        from_outcome=our_status, to_outcome=actual_status,
    )


async def write_comment(
    session_factory: async_sessionmaker,
    notifier: PartnerNotifier,
    *,
    application_id,
    external_id: str,
    text: str,
) -> None:
    try:
        await _call_with_retry(
            session_factory,
            application_id=application_id,
            direction="outbound",
            operation="add_comment",
            func=lambda: notifier.add_comment(external_id, text),
        )
    except PartnerUnavailableError:
        await _fallback_to_outbox(
            session_factory, application_id=application_id, external_id=external_id,
            event_type="comment", payload={"text": text},
        )


async def send_ocr_result(
    session_factory: async_sessionmaker,
    notifier: PartnerNotifier,
    *,
    application_id,
    external_id: str,
    fields: dict[str, Any],
) -> None:
    """T18: распознанные паспортные поля — сразу после OCR, не дожидаясь
    решения стадии А."""
    try:
        await _call_with_retry(
            session_factory,
            application_id=application_id,
            direction="outbound",
            operation="send_ocr_result",
            func=lambda: notifier.send_ocr_result(external_id, fields),
        )
    except PartnerUnavailableError:
        await _fallback_to_outbox(
            session_factory, application_id=application_id, external_id=external_id,
            event_type="ocr_result", payload={"fields": fields},
        )
