"""Провайдер распознавания для e2e-прогонов без ключа Anthropic (scripts/e2e_check.py).
Не для pytest — там уже есть FakeOcrProvider (app/ocr/fake_provider.py), которому
не нужен файл: он конструируется в процессе теста напрямую. Этому провайдеру
файл нужен именно потому, что настоящие Celery-задачи запускаются в отдельном
процессе воркера, которым скрипт не управляет напрямую — только через
docker compose exec с переменными окружения."""

import json
from pathlib import Path

from app.ocr.base import OcrRawResult
from app.ocr.fake_provider import build_ocr_result


class FixtureOcrProvider:
    """Перечитывает JSON-файл с overrides (тот же формат, что overrides у
    build_ocr_result) на КАЖДЫЙ recognize() — чтобы e2e-скрипт мог переписать
    фикстуру между сценариями, не перезапуская воркер. Файла нет/пустой ->
    дефолтный "чистый" результат build_ocr_result()."""

    name = "fixture"

    def __init__(self, path: str) -> None:
        self._path = Path(path) if path else None

    async def recognize(self, passport_main: bytes, passport_reg: bytes) -> OcrRawResult:
        overrides = None
        if self._path is not None and self._path.exists():
            overrides = json.loads(self._path.read_text(encoding="utf-8")) or None
        return build_ocr_result(overrides=overrides)
