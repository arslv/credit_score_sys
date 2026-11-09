"""Загрузка справочника кодов подразделений (T08, п.3) — единственный модуль
движка, которому нужен файловый I/O: остальное в app.engine.passport — чистые
функции. Источник, дата и лицензия — config/reference/fms_unit.SOURCE.md и
docs/registries.md. Кэшируется на процесс: справочник большой (~7500 кодов) и
не меняется в рантайме — при обновлении файла процессы api/worker нужно
перезапустить."""

import csv
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4)
def load_department_codes(path: str) -> frozenset[str]:
    with Path(path).open(encoding="utf-8") as f:
        return frozenset(row["code"] for row in csv.DictReader(f))


@lru_cache(maxsize=4)
def load_passport_series_regions(path: str) -> dict[str, frozenset[str]]:
    """Первые 2 цифры серии (ОКАТО региона) → допустимые первые 2 цифры кода
    подразделения (номер региона), config/reference/passport_series_regions.csv."""
    with Path(path).open(encoding="utf-8") as f:
        return {
            row["series"]: frozenset(code.strip() for code in row["department"].split(";") if code.strip())
            for row in csv.DictReader(f)
        }
