import asyncio
from datetime import datetime, timezone

import structlog

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.models import RegistrySyncLog
from app.registries.downloader import LocalFileDownloader
from app.registries.jobs import sync_invalid_passports, sync_sanctions_list
from app.registries.sync import RegistrySyncError

logger = structlog.get_logger()

_REGISTRIES = ("mvd_invalid_passports", "rosfinmon_sanctions")


async def _sync_one(session_factory, downloader, registry: str) -> str:
    job = sync_invalid_passports if registry == "mvd_invalid_passports" else sync_sanctions_list
    try:
        await job(session_factory, downloader)
        return "ok"
    except RegistrySyncError:
        # Уже залогировано и записано в registry_sync_log самим atomic_registry_reload.
        return "sync_error"
    except Exception as exc:
        # Сбой до атомарной замены (файла нет, не читается) — atomic_registry_reload
        # даже не вызывался, поэтому фиксируем сами: иначе такой сбой не виден
        # ни в registry_sync_log, ни в проверке устаревания.
        logger.error("registry_download_failed", registry=registry, error=str(exc))
        async with session_factory() as session:
            session.add(
                RegistrySyncLog(
                    registry=registry,
                    status="error",
                    rows_loaded=None,
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                    error_text=str(exc),
                )
            )
            await session.commit()
        return "download_error"


async def _run() -> dict[str, str]:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    downloader = LocalFileDownloader(settings.registries_dir)

    summary: dict[str, str] = {}
    try:
        for registry in _REGISTRIES:
            summary[registry] = await _sync_one(session_factory, downloader, registry)
    finally:
        await engine.dispose()

    return summary


@celery_app.task(name="registries.sync_all")
def sync_all_registries_task() -> dict[str, str]:
    return asyncio.run(_run())
