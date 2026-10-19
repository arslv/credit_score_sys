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


# Найденный баг (T14, ваше замечание): version/comment/effective_from —
# метаданные версии, не бизнес-правила. Если они участвуют в хеше, два конфига
# с ОДИНАКОВЫМИ правилами, но разной датой вступления или другим
# комментарием (например, после отката — там effective_from всегда
# сегодняшнее число, см. app/api/rulesets.py::rollback) считаются "разными" —
# "принять версию из файла" никогда не может совпасть с файлом, даже когда
# правила реально совпадают, и плодит версии при каждом нажатии.
_HASH_EXCLUDED_FIELDS = ("version", "comment", "effective_from")


def compute_config_hash(config: RulesetConfig) -> str:
    data = config.model_dump(mode="json")
    for field in _HASH_EXCLUDED_FIELDS:
        data.pop(field, None)
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def effective_from_datetime(config: RulesetConfig) -> datetime:
    """effective_from из YAML — дата; в ruleset_versions — начало этого дня по UTC."""
    return datetime.combine(config.effective_from, time.min, tzinfo=timezone.utc)


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
        effective_from=effective_from_datetime(config),
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


class FileVsActive(StrEnum):
    """Как файл на диске соотносится с активной версией. Одна функция и для
    синхронизации при старте, и для плашки «файл разошёлся» в админке
    (app/api/rulesets.py::list_versions) — чтобы они не разъехались."""

    SAME = "same"  # правила совпадают по хешу
    HUMAN_EDITS = "human_edits"  # активную версию сохранил человек — файл её не затирает
    FILE_OLDER = "file_older"  # effective_from файла раньше активной — версия из файла не стала бы активной
    FILE_AHEAD = "file_ahead"  # файл можно завести новой версией


def compare_file_with_active(
    file_config: RulesetConfig, file_hash: str, active: RulesetVersion | None
) -> FileVsActive:
    if active is None:
        return FileVsActive.FILE_AHEAD
    if active.config_hash == file_hash:
        return FileVsActive.SAME
    if active.author_id is not None:
        return FileVsActive.HUMAN_EDITS
    # Строго раньше: при равной дате get_active_ruleset_version выберет
    # более новую по номеру, т.е. версию из файла, — её заводить имеет смысл.
    if effective_from_datetime(file_config) < active.effective_from:
        return FileVsActive.FILE_OLDER
    return FileVsActive.FILE_AHEAD


class RulesetSyncResult(StrEnum):
    UNCHANGED = "unchanged"
    CREATED = "created"
    CONFLICT = "conflict"
    FILE_INVALID = "file_invalid"
    FILE_OUTDATED = "file_outdated"
    ALREADY_SAVED = "already_saved"


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
    - Хеш отличается, а у активной версии ЕСТЬ автор (человек её сохранил через
      будущую админку) -> CONFLICT: файл не должен молча затереть ручные правки.
      Warning в лог с номером активной версии, новая версия НЕ создаётся — пусть
      человек разберётся (обновит файл под ручные правки или наоборот).
    - Хеш отличается, у активной версии нет автора, но effective_from файла
      РАНЬШЕ, чем у активной (типично после «Принять версию из файла» — та
      ставит сегодняшнюю дату, а в файле дата старая) -> FILE_OUTDATED:
      версия из файла всё равно не стала бы активной (get_active_ruleset_version
      выбирает по effective_from). Раньше она создавалась на каждом
      перезапуске api и копилась мёртвыми строками в истории. Warning в лог,
      ничего не пишем; админка показывает это как конфликт с файлом, решается
      кнопкой «Принять версию из файла» или новой датой в файле.
    - Иначе — файл просто уехал вперёд: CREATED, новая версия из файла,
      author=system. Кроме случая, когда версия с тем же хешем и той же
      effective_from уже есть (файл с датой в будущем: заведена при прошлом
      старте, ждёт своей даты) -> ALREADY_SAVED, повторно не пишем.
      Дедупликация по паре, не по одному хешу: файл, возвращённый к правилам
      старой версии с новой датой, — законная новая версия.
    """
    try:
        config = load_ruleset_from_yaml(ruleset_path)
    except (ValidationError, yaml.YAMLError) as exc:
        logger.error("ruleset_file_invalid", ruleset_path=str(ruleset_path), error=str(exc))
        return RulesetSyncResult.FILE_INVALID

    file_hash = compute_config_hash(config)

    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        verdict = compare_file_with_active(config, file_hash, active)

        if verdict == FileVsActive.SAME:
            logger.info("ruleset_sync_unchanged", active_version=active.version)
            return RulesetSyncResult.UNCHANGED

        if verdict == FileVsActive.HUMAN_EDITS:
            logger.warning(
                "ruleset_sync_conflict",
                active_version=active.version,
                active_author_id=str(active.author_id),
                ruleset_path=str(ruleset_path),
            )
            return RulesetSyncResult.CONFLICT

        if verdict == FileVsActive.FILE_OLDER:
            logger.warning(
                "ruleset_sync_file_outdated",
                active_version=active.version,
                active_effective_from=active.effective_from.isoformat(),
                file_effective_from=config.effective_from.isoformat(),
                ruleset_path=str(ruleset_path),
            )
            return RulesetSyncResult.FILE_OUTDATED

        existing = (
            await session.execute(
                select(RulesetVersion)
                .where(
                    RulesetVersion.config_hash == file_hash,
                    RulesetVersion.effective_from == effective_from_datetime(config),
                )
                .order_by(RulesetVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "ruleset_sync_already_saved",
                version=existing.version,
                active_version=active.version if active else None,
            )
            return RulesetSyncResult.ALREADY_SAVED

        row = await save_ruleset_version(session, config, author_id=None, config_hash=file_hash)
        await session.commit()
        logger.info(
            "ruleset_synced_from_file",
            version=row.version,
            author="system",
            previous_version=active.version if active else None,
        )
        return RulesetSyncResult.CREATED
