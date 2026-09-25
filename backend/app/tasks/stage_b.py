"""Периодическая задача стадии Б (T10) — по образцу stage_a.py. Подбирает
заявки со снимком анкеты стадии Б, ещё не обработанным: "to_operator" —
после «Рассмотреть», ещё не решали; "gray_zone" — уже решали, но серая зона
(например, истекло согласие на КИ) и оператор мог поправить данные и снова
нажать «Рассмотреть» (ТЗ: повторный снимок — новое решение). "rejected_b"
терминален (state_machine.py) — сознательно не переоткрывается автоматически."""

import asyncio

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.stage_b_pipeline import run_stage_b
from app.integrations.fake_partner_client import FakePartnerClient
from app.models import Application, ExternalSnapshot

logger = structlog.get_logger()

_PICKUP_STATUSES = ("to_operator", "gray_zone")


async def _run() -> dict[str, int]:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    partner_client = FakePartnerClient(settings.partner_base_url, settings.partner_token)

    summary = {"processed": 0, "errors": 0}
    try:
        async with session_factory() as session:
            application_ids = (
                await session.execute(
                    select(Application.id)
                    .join(ExternalSnapshot, ExternalSnapshot.application_id == Application.id)
                    .where(Application.status.in_(_PICKUP_STATUSES), ExternalSnapshot.stage == "B")
                    .distinct()
                )
            ).scalars().all()

        for application_id in application_ids:
            try:
                await run_stage_b(session_factory, application_id, partner_client=partner_client)
                summary["processed"] += 1
            except Exception:
                summary["errors"] += 1
                logger.exception("stage_b_processing_failed", application_id=str(application_id))
    finally:
        await partner_client.aclose()
        await engine.dispose()

    return summary


@celery_app.task(name="stage_b.process_reviewed_applications")
def process_reviewed_applications_task() -> dict[str, int]:
    return asyncio.run(_run())
