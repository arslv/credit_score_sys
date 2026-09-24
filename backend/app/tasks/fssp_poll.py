"""Периодический опрос ФССП по строкам fssp_requests (T09). Отдельная задача,
не цикл со sleep внутри стадии А — воркер между тиками свободен для другой
работы. SELECT ... FOR UPDATE SKIP LOCKED защищает от двойной обработки одной
строки, если тики/воркеры пересекаются по времени."""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.pipeline import finalize_stage_a
from app.integrations.fake_partner_client import FakePartnerClient
from app.models import FsspRequest
from app.sources.base import CheckResult, CheckStatus
from app.sources.factory import build_fssp_source
from app.sources.runner import record_check

logger = structlog.get_logger()


async def _poll_one(session_factory, fssp_source, partner_client, request_id) -> str:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(FsspRequest)
                .where(FsspRequest.id == request_id, FsspRequest.status == "pending")
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            # Уже обработана конкурентным тиком/воркером, или статус успел измениться.
            return "skipped"

        application_id = row.application_id
        now = datetime.now(timezone.utc)

        if now >= row.deadline:
            row.status = "expired"
            await record_check(
                session,
                application_id=application_id,
                subject_type="applicant",
                subject_id=None,
                source_name="fssp",
                result=CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="дедлайн опроса истёк"),
                started_at=now,
                finished_at=now,
                duration_ms=0,
            )
            await session.commit()
        else:
            row.attempts += 1
            row.last_polled_at = now
            result = await fssp_source.poll(row.external_request_id)
            if result is None:
                await session.commit()
                return "still_pending"

            row.status = "done"
            await record_check(
                session,
                application_id=application_id,
                subject_type="applicant",
                subject_id=None,
                source_name="fssp",
                result=result,
                started_at=now,
                finished_at=datetime.now(timezone.utc),
                duration_ms=0,
            )
            await session.commit()

    await finalize_stage_a(session_factory, application_id, partner_client=partner_client)
    return "finalized"


async def _run() -> dict[str, int]:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    partner_client = FakePartnerClient(settings.partner_base_url, settings.partner_token)
    fssp_source = build_fssp_source(settings)

    summary: dict[str, int] = {}
    try:
        async with session_factory() as session:
            pending_ids = (
                await session.execute(select(FsspRequest.id).where(FsspRequest.status == "pending"))
            ).scalars().all()

        for request_id in pending_ids:
            try:
                outcome = await _poll_one(session_factory, fssp_source, partner_client, request_id)
            except Exception:
                outcome = "errors"
                logger.exception("fssp_poll_failed", fssp_request_id=str(request_id))
            summary[outcome] = summary.get(outcome, 0) + 1
    finally:
        await partner_client.aclose()
        await engine.dispose()

    return summary


@celery_app.task(name="fssp.poll_pending")
def poll_pending_task() -> dict[str, int]:
    return asyncio.run(_run())
