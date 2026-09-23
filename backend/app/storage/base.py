from typing import Protocol


class Storage(Protocol):
    """Файловое хранилище для фото документов. В БД попадает только storage_key,
    хеш и метаданные — сами байты сюда."""

    async def save(self, key: str, data: bytes) -> str:
        """Сохраняет данные под ключом key, возвращает итоговый storage_key."""
        ...

    async def read(self, key: str) -> bytes:
        ...
