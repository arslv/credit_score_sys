import asyncio

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.integrations.adapter import sync_applications
from app.integrations.fake_partner_client import FakePartnerClient
from app.storage.local import LocalDirectoryStorage


async def _run() -> dict[str, int]:
    settings = get_settings()
    client = FakePartnerClient(settings.partner_base_url, settings.partner_token)
    storage = LocalDirectoryStorage(settings.storage_dir)
    # Свой движок на вызов — asyncio.run() каждый раз даёт новый event loop,
    # а закэшированный в app.db движок привязан к первому из них (см. app.db.new_sessionmaker).
    engine, session_factory = new_sessionmaker()
    try:
        return await sync_applications(session_factory, client, storage)
    finally:
        await client.aclose()
        await engine.dispose()


@celery_app.task(name="integration.sync_applications")
def sync_applications_task() -> dict[str, int]:
    return asyncio.run(_run())
