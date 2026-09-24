import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.models import InvalidPassport, RegistrySyncLog
from app.registries.sync import RegistrySyncError, atomic_registry_reload, is_registry_stale
from tests.conftest import unique_valid_passport_series


async def _temp_tables(session_factory, target_table: str) -> list[str]:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND (tablename LIKE :staging OR tablename LIKE :old)"
            ),
            {"staging": f"{target_table}_staging_%", "old": f"{target_table}_old_%"},
        )
        return [row[0] for row in result.all()]


async def test_successful_sync_replaces_content_and_leaves_no_temp_tables(session_factory):
    series = unique_valid_passport_series()
    rows = [
        {"passport_series": series, "passport_number": "111111", "loaded_at": datetime.now(timezone.utc)}
    ]

    log = await atomic_registry_reload(
        session_factory,
        registry="mvd_invalid_passports",
        target_table="invalid_passports",
        rows=rows,
        insert_columns=["passport_series", "passport_number", "loaded_at"],
    )

    assert log.status == "ok"
    assert log.rows_loaded == 1

    async with session_factory() as session:
        found = (
            await session.execute(
                select(InvalidPassport).where(
                    InvalidPassport.passport_series == series,
                    InvalidPassport.passport_number == "111111",
                )
            )
        ).scalar_one_or_none()
        assert found is not None

    assert await _temp_tables(session_factory, "invalid_passports") == []


async def test_sync_failure_does_not_clobber_previous_version(session_factory):
    series_ok = unique_valid_passport_series()
    # Сначала успешная загрузка — она играет роль «предыдущей версии».
    await atomic_registry_reload(
        session_factory,
        registry="mvd_invalid_passports",
        target_table="invalid_passports",
        rows=[
            {
                "passport_series": series_ok,
                "passport_number": "222222",
                "loaded_at": datetime.now(timezone.utc),
            }
        ],
        insert_columns=["passport_series", "passport_number", "loaded_at"],
    )

    # Ломаем загрузку на вставке: passport_series NOT NULL, а мы передаём None.
    broken_rows = [
        {"passport_series": None, "passport_number": "333333", "loaded_at": datetime.now(timezone.utc)}
    ]

    with pytest.raises(RegistrySyncError):
        await atomic_registry_reload(
            session_factory,
            registry="mvd_invalid_passports",
            target_table="invalid_passports",
            rows=broken_rows,
            insert_columns=["passport_series", "passport_number", "loaded_at"],
        )

    # Предыдущая версия осталась доступна и не тронута.
    async with session_factory() as session:
        found = (
            await session.execute(
                select(InvalidPassport).where(
                    InvalidPassport.passport_series == series_ok,
                    InvalidPassport.passport_number == "222222",
                )
            )
        ).scalar_one_or_none()
        assert found is not None

        # Битые данные точно не просочились.
        broken = (
            await session.execute(
                select(InvalidPassport).where(InvalidPassport.passport_number == "333333")
            )
        ).scalar_one_or_none()
        assert broken is None

    async with session_factory() as session:
        error_log = (
            await session.execute(
                select(RegistrySyncLog)
                .where(
                    RegistrySyncLog.registry == "mvd_invalid_passports",
                    RegistrySyncLog.status == "error",
                )
                .order_by(RegistrySyncLog.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert error_log is not None
        assert error_log.error_text  # причина сбоя записана

    assert await _temp_tables(session_factory, "invalid_passports") == []


async def test_is_registry_stale_true_when_never_synced(session_factory):
    registry = f"test_registry_{uuid.uuid4().hex[:8]}"
    assert await is_registry_stale(session_factory, registry=registry, max_staleness_days=2) is True


async def test_is_registry_stale_false_right_after_successful_sync(session_factory):
    registry = f"test_registry_{uuid.uuid4().hex[:8]}"
    await atomic_registry_reload(
        session_factory,
        registry=registry,
        target_table="invalid_passports",
        rows=[],
        insert_columns=["passport_series", "passport_number", "loaded_at"],
    )
    assert await is_registry_stale(session_factory, registry=registry, max_staleness_days=2) is False


async def test_is_registry_stale_ignores_failed_syncs(session_factory):
    """Реестр, у которого только ошибочные попытки — считается устаревшим:
    последней УСПЕШНОЙ синхронизации не было."""
    registry = f"test_registry_{uuid.uuid4().hex[:8]}"
    with pytest.raises(RegistrySyncError):
        await atomic_registry_reload(
            session_factory,
            registry=registry,
            target_table="invalid_passports",
            rows=[{"passport_series": None, "passport_number": "444444", "loaded_at": datetime.now(timezone.utc)}],
            insert_columns=["passport_series", "passport_number", "loaded_at"],
        )

    assert await is_registry_stale(session_factory, registry=registry, max_staleness_days=2) is True
