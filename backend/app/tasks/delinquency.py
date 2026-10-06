"""T15: ежедневный пересчёт delinquency_daily (ТЗ: «пересчитывается ежедневной
задачей из графика и фактических платежей»). По образцу остальных задач
(app/tasks/stage_a.py и т.п.) — свой session_factory/event loop на вызов."""

import asyncio

import structlog

from app.celery_app import celery_app
from app.db import new_sessionmaker
from app.engine.delinquency_recompute import recompute_all_contracts

logger = structlog.get_logger()


async def _run() -> dict[str, int]:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            summary = await recompute_all_contracts(session)
            await session.commit()
        logger.info("delinquency_recomputed", **summary)
        return summary
    finally:
        await engine.dispose()


@celery_app.task(name="delinquency.recompute_daily")
def recompute_daily_task() -> dict[str, int]:
    return asyncio.run(_run())
