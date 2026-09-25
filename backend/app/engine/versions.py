"""Версии конфига в ruleset_versions. Активная версия определяется по effective_from
(ТЗ, «Конфиг правил и пороги» -> «Версионирование»). В отличие от app.engine.rules,
этот модуль работает с БД — это осознанно вынесено из чистой функции движка."""

import hashlib
import json
from datetime import datetime, time, timezone
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import structlog
import yaml
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.schema import RulesetConfig, load_ruleset_from_yaml
from app.models import RulesetVersion

logger = structlog.get_logger()


def compute_config_hash(config: RulesetConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def save_ruleset_version(
    session: AsyncSession,
    config: RulesetConfig,
    *,
    author_id: UUID | None = None,
    config_hash: str | None = None,
) -> RulesetVersion:
    """version в БД — не то же самое, что config.version из YAML. config.version —
    человеко-читаемая метка ("подняли до версии 7" в комментарии), а настоящая
    последовательность назначается здесь автоматически (MAX+1): при автосинхронизации
    с файлом (см. sync_ruleset_from_file) никто не гарантирует, что человек не забудет
    поднять число в YAML, а конфликт по unique(version) не должен ронять синхронизацию."""
    next_version = (
        await session.execute(select(func.coalesce(func.max(RulesetVersion.version), 0) + 1))
    ).scalar_one()

    row = RulesetVersion(
        version=next_version,
        config=config.model_dump(mode="json"),
        config_hash=config_hash or compute_config_hash(config),
        comment=config.comment,
        author_id=author_id,
        effective_from=datetime.combine(config.effective_from, time.min, tzinfo=timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


async def get_active_ruleset_version(
    session: AsyncSession, at: datetime | None = None
) -> RulesetVersion | None:
    moment = at or datetime.now(timezone.utc)
    result = await session.execute(
        select(RulesetVersion)
        .where(RulesetVersion.effective_from <= moment)
        # version как вторичный ключ сортировки: несколько версий с одинаковым
        # effective_from (например, dev.yaml с фиксированной датой пересинхронизируется
        # при каждом перезапуске api и без этого тай-брейка ORDER BY по одному
        # effective_from возвращает произвольную из них, не обязательно последнюю) —
        # без него "активная" версия становится недетерминированной.
        .order_by(RulesetVersion.effective_from.desc(), RulesetVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def load_config(row: RulesetVersion) -> RulesetConfig:
    """Собирает обратно валидированный RulesetConfig из строки ruleset_versions."""
    return RulesetConfig.model_validate(row.config)


class RulesetSyncResult(StrEnum):
    UNCHANGED = "unchanged"
    CREATED = "created"
    CONFLICT = "conflict"
    FILE_INVALID = "file_invalid"


async def sync_ruleset_from_file(
    session_factory: async_sessionmaker, ruleset_path: str | Path
) -> RulesetSyncResult:
    """Запускается при старте приложения (app.main.lifespan).

    Файл — источник правды для БАЗОВОЙ конфигурации, БД хранит историю и ручные
    правки риск-менеджера через будущую админку (T14). Логика:

    - Файл не читается или не проходит схему -> FILE_INVALID, громкий error-лог,
      активная версия в БД (если есть) НЕ трогается — старая, но валидная
      конфигурация продолжает работать, сервис не падает целиком из-за опечатки
      в YAML на диске.
    - Активной версии в БД ещё нет -> CREATED, первая версия из файла, author=system.
    - Хеш файла совпадает с хешем активной версии -> UNCHANGED, ничего не делаем.
    - Хеш отличается, но у активной версии НЕТ автора (author_id is None, т.е. она
      сама когда-то пришла из файла, а не сохранена человеком через админку) ->
      файл просто уехал вперёд, CREATED, новая версия из файла, author=system.
    - Хеш отличается, а у активной версии ЕСТЬ автор (человек её сохранил через
      будущую админку) -> CONFLICT: файл не должен молча затереть ручные правки.
      Warning в лог с номером активной версии, новая версия НЕ создаётся — пусть
      человек разберётся (обновит файл под ручные правки или наоборот).
    """
    try:
        config = load_ruleset_from_yaml(ruleset_path)
    except (ValidationError, yaml.YAMLError) as exc:
        logger.error("ruleset_file_invalid", ruleset_path=str(ruleset_path), error=str(exc))
        return RulesetSyncResult.FILE_INVALID

    file_hash = compute_config_hash(config)

    async with session_factory() as session:
        active = await get_active_ruleset_version(session)

        if active is not None and active.config_hash == file_hash:
            logger.info("ruleset_sync_unchanged", active_version=active.version)
            return RulesetSyncResult.UNCHANGED

        if active is not None and active.author_id is not None:
            logger.warning(
                "ruleset_sync_conflict",
                active_version=active.version,
                active_author_id=str(active.author_id),
                ruleset_path=str(ruleset_path),
            )
            return RulesetSyncResult.CONFLICT

        row = await save_ruleset_version(session, config, author_id=None, config_hash=file_hash)
        await session.commit()
        logger.info(
            "ruleset_synced_from_file",
            version=row.version,
            author="system",
            previous_version=active.version if active else None,
        )
        return RulesetSyncResult.CREATED
