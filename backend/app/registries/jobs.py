"""Полный цикл синхронизации конкретных реестров: скачать -> разобрать -> атомарно
залить. Используется и периодической задачей, и тестами (с фейковым downloader'ом)."""

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import RegistrySyncLog
from app.registries.downloader import RegistryDownloader
from app.registries.parsers import parse_invalid_passports_csv, parse_sanctions_csv
from app.registries.sync import atomic_registry_reload


async def sync_invalid_passports(
    session_factory: async_sessionmaker, downloader: RegistryDownloader
) -> RegistrySyncLog:
    content = await downloader.download("mvd_invalid_passports")
    rows = parse_invalid_passports_csv(content)
    return await atomic_registry_reload(
        session_factory,
        registry="mvd_invalid_passports",
        target_table="invalid_passports",
        rows=rows,
        insert_columns=["passport_series", "passport_number", "loaded_at"],
    )


async def sync_sanctions_list(
    session_factory: async_sessionmaker, downloader: RegistryDownloader
) -> RegistrySyncLog:
    content = await downloader.download("rosfinmon_sanctions")
    rows = parse_sanctions_csv(content)
    return await atomic_registry_reload(
        session_factory,
        registry="rosfinmon_sanctions",
        target_table="sanctions_list",
        rows=rows,
        insert_columns=["list_type", "full_name", "name_normalized", "birth_date", "loaded_at"],
    )
