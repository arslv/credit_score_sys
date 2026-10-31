import asyncio

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.pipeline import recover_stuck_ocr_applications, run_stage_a, run_stage_a_from_crm_data
from app.engine.stage_b_pipeline import run_stage_b
from app.integrations.partner_notifier import PartnerNotifier
from app.models import Application, OcrResult
from app.ocr.factory import build_ocr_provider
from app.storage.local import LocalDirectoryStorage

logger = structlog.get_logger()


async def _run() -> dict[str, int]:
    settings = get_settings()
    # Свой движок на вызов — см. app.db.new_sessionmaker: asyncio.run() каждый раз
    # даёт новый event loop, кэшированный процессный движок это не переживает.
    engine, session_factory = new_sessionmaker()
    partner_client = PartnerNotifier(
        settings.partner_outbound_url, settings.partner_outbound_token,
        timeout_seconds=settings.partner_outbound_timeout_seconds,
    )
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

        # Распознавание выключено: заявки, по которым CRM уже прислала
        # паспортные данные в /review, но задача из вебхука не отработала
        # (воркер был недоступен) — запасной путь, как sweep выше.
        async with session_factory() as session:
            crm_data_ids = (
                await session.execute(
                    select(Application.id)
                    .join(OcrResult, OcrResult.application_id == Application.id)
                    .where(Application.status == "awaiting_data")
                    .distinct()
                )
            ).scalars().all()
        for application_id in crm_data_ids:
            try:
                await _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client)
                summary["processed"] += 1
            except Exception:
                summary["errors"] += 1
                logger.exception("stage_a_crm_data_processing_failed", application_id=str(application_id))

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


async def _run_one(application_id: str) -> None:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    partner_client = PartnerNotifier(
        settings.partner_outbound_url, settings.partner_outbound_token,
        timeout_seconds=settings.partner_outbound_timeout_seconds,
    )
    ocr_provider = build_ocr_provider(settings)
    storage = LocalDirectoryStorage(settings.storage_dir)
    try:
        await run_stage_a(
            session_factory, application_id,
            ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
        )
    finally:
        await partner_client.aclose()
        await engine.dispose()


@celery_app.task(name="stage_a.process_single_application")
def process_single_application_task(application_id: str) -> None:
    """T18: диспетчер вебхука POST /api/v1/crm/new-order ставит эту
    задачу сразу после сохранения заявки (.delay(), как queue_fns_checks) —
    не ждём следующего тика process_new_applications. Сам sweep выше
    остаётся запасным путём на случай, если воркер был недоступен в момент
    вебхука (идемпотентность — SELECT ... FOR UPDATE в finalize_stage_a)."""
    asyncio.run(_run_one(application_id))


async def _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client: PartnerNotifier) -> None:
    """Распознавание выключено: паспортные данные пришли от CRM вместе с
    «Рассмотреть» — стадия А по ним и, если она не отказала, сразу стадия Б
    по уже сохранённому снимку анкеты (оператор уже позвонил)."""
    status = await run_stage_a_from_crm_data(session_factory, application_id, partner_client=partner_client)
    if status == "to_operator":
        await run_stage_b(session_factory, application_id, partner_client=partner_client)


async def _run_crm_data(application_id: str) -> None:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    partner_client = PartnerNotifier(
        settings.partner_outbound_url, settings.partner_outbound_token,
        timeout_seconds=settings.partner_outbound_timeout_seconds,
    )
    try:
        await _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client)
    finally:
        await partner_client.aclose()
        await engine.dispose()


@celery_app.task(name="stage_a.process_crm_data")
def process_crm_data_task(application_id: str) -> None:
    """Ставит вебхук POST /api/v1/crm/scoring, когда заявка ждала
    паспортные данные от CRM (awaiting_data) и они пришли."""
    asyncio.run(_run_crm_data(application_id))
