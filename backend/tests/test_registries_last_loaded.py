"""«Состояние системы»: дата последней успешной загрузки реестра (решение
владельца 16.11.2026 — у перечня Росфинмониторинга показывать дату)."""

from datetime import datetime, timedelta, timezone

from app.models import RegistrySyncLog
from app.registries.sync import registry_last_loaded_at


async def test_last_loaded_at_is_latest_successful_sync(session_factory):
    registry = "test_registry_last_loaded"
    assert await registry_last_loaded_at(session_factory, registry=registry) is None

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add_all([
            RegistrySyncLog(registry_name=registry, status="ok", started_at=now - timedelta(days=3),
                            finished_at=now - timedelta(days=3)),
            RegistrySyncLog(registry_name=registry, status="ok", started_at=now - timedelta(days=1),
                            finished_at=now - timedelta(days=1)),
            # Неудачная загрузка позже — не считается.
            RegistrySyncLog(registry_name=registry, status="error", started_at=now, finished_at=now),
        ])
        await session.commit()

    loaded = await registry_last_loaded_at(session_factory, registry=registry)
    assert loaded is not None
    assert abs((loaded - (now - timedelta(days=1))).total_seconds()) < 1
