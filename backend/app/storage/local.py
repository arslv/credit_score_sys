import asyncio
from pathlib import Path


class LocalDirectoryStorage:
    """Реализация Storage поверх локального каталога — для разработки и тестов.
    Реальное хранилище (шифрованное, в РФ) подключается тем же интерфейсом."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    def _path_for(self, key: str) -> Path:
        path = (self._base_dir / key).resolve()
        if self._base_dir.resolve() not in path.parents and path != self._base_dir.resolve():
            raise ValueError(f"invalid storage key: {key!r}")
        return path

    async def save(self, key: str, data: bytes) -> str:
        path = self._path_for(key)
        await asyncio.to_thread(self._write, path, data)
        return key

    async def read(self, key: str) -> bytes:
        path = self._path_for(key)
        return await asyncio.to_thread(path.read_bytes)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
