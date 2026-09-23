"""Источник файла реестра. Онлайн-форма МВД защищена капчей, у Росфинмониторинга
нет задокументированного автоматического формата (ТЗ, «Файловые реестры») — поэтому
на старте файл кладёт оператор (вручную или полуавтоматическим скриптом), а
синхронизатор забирает его отсюда. Интерфейс тот же, что и у остальных заменяемых
модулей — подключить настоящую автоматическую выгрузку можно одним файлом."""

import asyncio
from pathlib import Path
from typing import Protocol


class RegistryDownloader(Protocol):
    async def download(self, registry: str) -> bytes: ...


class LocalFileDownloader:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    async def download(self, registry: str) -> bytes:
        path = self._base_dir / f"{registry}.csv"
        return await asyncio.to_thread(path.read_bytes)
