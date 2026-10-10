"""Периодическая задача стадии Б (T10/T11) — по образцу stage_a.py. Подбирает
заявки со снимком анкеты стадии Б, ещё не обработанным: "to_operator" —
после «Рассмотреть», ещё не решали; "gray_zone" — уже решали, но серая зона
(например, БКИ было недоступно/не настроено или истекло согласие на КИ) и
оператор мог поправить данные и снова нажать «Рассмотреть» (ТЗ: повторный
снимок — новое решение); "stage_b" — застряла на платном обращении в бюро
(другой запрос того же клиента уже был в полёте, см. app/engine/bki_cache.py)
— пере-подбор довершает БКИ, не гоняя заново поручителя и внешние проверки
(finalize_stage_b, не run_stage_b). "rejected_b"/"approved" терминальны
(state_machine.py) — сознательно не переоткрываются автоматически."""

import asyncio

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.stage_b_pipeline import finalize_stage_b, run_stage_b
from app.integrations.partner_notifier import PartnerNotifier
from app.models import Application, ExternalSnapshot

logger = structlog.get_logger()

_PICKUP_STATUSES = ("to_operator", "gray_zone", "stage_b")


async def _run() -> dict[str, int]:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    partner_client = PartnerNotifier(
        settings.partner_outbound_url, settings.partner_outbound_token,
        timeout_seconds=settings.partner_outbound_timeout_seconds,
    )

    summary = {"processed": 0, "errors": 0}
    try:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Application.id, Application.status)
                    .join(ExternalSnapshot, ExternalSnapshot.application_id == Application.id)
                    .where(Application.status.in_(_PICKUP_STATUSES), ExternalSnapshot.stage == "B")
                    .distinct()
                )
            ).all()

        for application_id, status in rows:
            try:
                if status == "stage_b":
                    # Уже прошла полнoту/шариат/лимиты/поручителя — довершаем
                    # только БКИ, не пересоздаём поручителя заново.
                    await finalize_stage_b(session_factory, application_id, partner_client=partner_client)
                else:
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


async def _run_one(application_id: str) -> None:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    partner_client = PartnerNotifier(
        settings.partner_outbound_url, settings.partner_outbound_token,
        timeout_seconds=settings.partner_outbound_timeout_seconds,
    )
    try:
        await run_stage_b(session_factory, application_id, partner_client=partner_client)
    finally:
        await partner_client.aclose()
        await engine.dispose()


@celery_app.task(name="stage_b.process_single_application")
def process_single_application_task(application_id: str) -> None:
    """T18: диспетчер вебхука POST /api/v1/partner/review ставит эту задачу
    сразу после сохранения снимка анкеты (.delay()) — не ждём следующего
    тика process_reviewed_applications. Всегда run_stage_b (не
    finalize_stage_b) — свежий снимок, тот же путь, что и в sweep выше для
    статусов, отличных от "stage_b" (см. докстринг модуля)."""
    asyncio.run(_run_one(application_id))
