import asyncio

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.pipeline import recover_stuck_ocr_applications, run_stage_a
from app.integrations.fake_partner_client import FakePartnerClient
from app.models import Application
from app.ocr.factory import build_ocr_provider
from app.storage.local import LocalDirectoryStorage

logger = structlog.get_logger()


async def _run() -> dict[str, int]:
    settings = get_settings()
    # Свой движок на вызов — см. app.db.new_sessionmaker: asyncio.run() каждый раз
    # даёт новый event loop, кэшированный процессный движок это не переживает.
    engine, session_factory = new_sessionmaker()
    partner_client = FakePartnerClient(settings.partner_base_url, settings.partner_token)
    ocr_provider = build_ocr_provider(settings)
    storage = LocalDirectoryStorage(settings.storage_dir)

    summary = {"processed": 0, "errors": 0}
    try:
        async with session_factory() as session:
            application_ids = (
                await session.execute(select(Application.id).where(Application.status == "received"))
            ).scalars().all()

        for application_id in application_ids:
            try:
                await run_stage_a(
                    session_factory,
                    application_id,
                    ocr_provider=ocr_provider,
                    partner_client=partner_client,
                    storage=storage,
                )
                summary["processed"] += 1
            except Exception:
                summary["errors"] += 1
                logger.exception("stage_a_processing_failed", application_id=str(application_id))

        # Ваше явное указание: заявка, зависшая в статусе "ocr" дольше
        # OCR_STUCK_MINUTES (процесс убит посреди вызова и т.п., retry внутри
        # run_ocr не спас), не должна молча висеть — тем же тиком уводим её
        # оператору, не дожидаясь отдельного расписания.
        summary["recovered_stuck_ocr"] = await recover_stuck_ocr_applications(
            session_factory, partner_client=partner_client, stuck_minutes=settings.ocr_stuck_minutes,
        )
    finally:
        await partner_client.aclose()
        await engine.dispose()

    return summary


@celery_app.task(name="stage_a.process_new_applications")
def process_new_applications_task() -> dict[str, int]:
    return asyncio.run(_run())
