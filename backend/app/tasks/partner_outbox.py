"""T18: подбирает partner_outbox_events (статус pending, подошёл срок
next_attempt_at) и повторяет доставку — «ничего не теряем» (ваше явное
указание): немедленная попытка внутри write_decision_status/write_comment/
send_ocr_result (app/integrations/adapter.py) не добралась — строка осталась
pending, эта задача подбирает её дальше бесконечно, с растущим бэкоффом до
потолка (partner_outbox_max_delay_seconds), без ограничения числа попыток."""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.integrations.adapter import _log_event, _record_manual_override
from app.integrations.base import PartnerConflictError, PartnerUnavailableError
from app.integrations.partner_notifier import PartnerNotifier
from app.models import PartnerOutboxEvent

logger = structlog.get_logger()


async def _drain() -> dict[str, int]:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    notifier = PartnerNotifier(
        settings.partner_outbound_url, settings.partner_outbound_token,
        timeout_seconds=settings.partner_outbound_timeout_seconds,
    )
    summary = {"sent": 0, "conflict": 0, "still_pending": 0}
    try:
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(PartnerOutboxEvent)
                    .where(PartnerOutboxEvent.status == "pending", PartnerOutboxEvent.next_attempt_at <= now)
                    .order_by(PartnerOutboxEvent.created_at)
                )
            ).scalars().all()
            # Снимок нужных полей — строки ниже живут в отдельных сессиях
            # (правило 13 CLAUDE.md: не держим блокировку/сессию во время
            # внешнего HTTP-вызова).
            batch = [
                (r.id, r.application_id, r.external_id, r.event_type, dict(r.payload), r.attempt)
                for r in rows
            ]

        for row_id, application_id, external_id, event_type, payload, attempt in batch:
            event_id = str(row_id)
            operation = f"outbox:{event_type}"
            try:
                await notifier.resend(event_type, external_id, payload, event_id)
            except PartnerConflictError as exc:
                async with session_factory() as session:
                    row = await session.get(PartnerOutboxEvent, row_id)
                    row.status = "conflict"
                    row.last_error = f"current_status={exc.current_status!r}"
                    await session.commit()
                await _log_event(
                    session_factory, application_id=application_id, direction="outbound",
                    operation=operation, status="conflict", attempt=attempt + 1,
                    response={"current_status": exc.current_status, "reason": exc.reason},
                )
                if event_type == "decision":
                    await _record_manual_override(
                        session_factory, application_id=application_id,
                        our_status=payload.get("status"), actual_status=exc.current_status,
                        operator_reason=exc.reason,
                    )
                summary["conflict"] += 1
            except PartnerUnavailableError as exc:
                next_delay = min(
                    settings.partner_outbox_base_delay_seconds * (2**attempt),
                    settings.partner_outbox_max_delay_seconds,
                )
                async with session_factory() as session:
                    row = await session.get(PartnerOutboxEvent, row_id)
                    row.attempt = attempt + 1
                    row.last_error = str(exc)
                    row.next_attempt_at = now + timedelta(seconds=next_delay)
                    await session.commit()
                await _log_event(
                    session_factory, application_id=application_id, direction="outbound",
                    operation=operation, status="retry", attempt=attempt + 1, response={"error": str(exc)},
                )
                logger.warning(
                    "partner_outbox_retry_scheduled", application_id=str(application_id),
                    event_type=event_type, attempt=attempt + 1, next_delay_seconds=next_delay,
                )
                summary["still_pending"] += 1
            else:
                async with session_factory() as session:
                    row = await session.get(PartnerOutboxEvent, row_id)
                    row.status = "sent"
                    row.sent_at = now
                    await session.commit()
                await _log_event(
                    session_factory, application_id=application_id, direction="outbound",
                    operation=operation, status="ok", attempt=attempt + 1,
                )
                summary["sent"] += 1
    finally:
        await notifier.aclose()
        await engine.dispose()
    return summary


@celery_app.task(name="partner_outbox.drain")
def drain_outbox_task() -> dict[str, int]:
    return asyncio.run(_drain())
