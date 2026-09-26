"""Синхронизатор файловых реестров: скачать -> проверить -> залить в новую таблицу
-> атомарно переключить -> registry_sync_log (ТЗ, «Файловые реестры»).

DDL в PostgreSQL транзакционна: CREATE/ALTER/DROP TABLE откатываются вместе с
остальными операциями сессии. Поэтому весь reload — одна транзакция: при сбое на
любом шаге откат возвращает всё как было, включая переименования таблиц —
предыдущая версия реестра не затирается.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import RegistrySyncLog

logger = structlog.get_logger()


class RegistrySyncError(Exception):
    pass


async def atomic_registry_reload(
    session_factory: async_sessionmaker,
    *,
    registry: str,
    target_table: str,
    rows: list[dict[str, Any]],
    insert_columns: list[str],
) -> RegistrySyncLog:
    started_at = datetime.now(timezone.utc)
    staging_table = f"{target_table}_staging_{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        try:
            await session.execute(
                text(f'CREATE TABLE "{staging_table}" (LIKE "{target_table}" INCLUDING ALL)')
            )

            if rows:
                columns_sql = ", ".join(f'"{c}"' for c in insert_columns)
                placeholders = ", ".join(f":{c}" for c in insert_columns)
                await session.execute(
                    text(f'INSERT INTO "{staging_table}" ({columns_sql}) VALUES ({placeholders})'),
                    rows,
                )

            old_table = f"{target_table}_old_{uuid.uuid4().hex[:8]}"
            await session.execute(text(f'ALTER TABLE "{target_table}" RENAME TO "{old_table}"'))
            await session.execute(text(f'ALTER TABLE "{staging_table}" RENAME TO "{target_table}"'))
            await session.execute(text(f'DROP TABLE "{old_table}"'))
        except Exception as exc:
            await session.rollback()

            async with session_factory() as log_session:
                log_row = RegistrySyncLog(
                    registry_name=registry,
                    status="error",
                    rows_loaded=None,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    error_text=str(exc),
                )
                log_session.add(log_row)
                await log_session.commit()

            logger.error("registry_sync_failed", registry=registry, error=str(exc))
            raise RegistrySyncError(str(exc)) from exc

        finished_at = datetime.now(timezone.utc)
        log_row = RegistrySyncLog(
            registry_name=registry,
            status="ok",
            rows_loaded=len(rows),
            started_at=started_at,
            finished_at=finished_at,
        )
        session.add(log_row)
        await session.commit()

        logger.info("registry_sync_ok", registry=registry, rows_loaded=len(rows))
        return log_row


async def is_registry_stale(session_factory: async_sessionmaker, *, registry: str, max_staleness_days: int) -> bool:
    """Алерт, если реестр старше N дней — здесь только вычисление факта; доставка
    алерта (почта/чат) не входит в рамки T08."""
    from sqlalchemy import select

    async with session_factory() as session:
        last_ok = (
            await session.execute(
                select(RegistrySyncLog)
                .where(RegistrySyncLog.registry_name == registry, RegistrySyncLog.status == "ok")
                .order_by(RegistrySyncLog.finished_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if last_ok is None or last_ok.finished_at is None:
        return True

    age_days = (datetime.now(timezone.utc) - last_ok.finished_at).days
    return age_days > max_staleness_days
