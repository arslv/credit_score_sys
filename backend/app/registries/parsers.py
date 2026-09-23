"""Разбор файлов реестров. Формат — наш собственный CSV: ни МВД (капча на форме),
ни fedsfm.ru не документируют публичный автоматический формат выгрузки, поэтому
реальный формат уточняется при подключении (ТЗ, «Файловые реестры»)."""

import csv
import io
from datetime import date, datetime, timezone
from typing import Any

from app.sources.name_normalize import normalize_name


class RegistryParseError(Exception):
    pass


def parse_invalid_passports_csv(content: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    loaded_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(reader, start=1):
        try:
            series = row["passport_series"].strip()
            number = row["passport_number"].strip()
        except KeyError as exc:
            raise RegistryParseError(f"строка {i}: не хватает колонки {exc}") from exc
        if len(series) != 4 or not series.isdigit():
            raise RegistryParseError(f"строка {i}: некорректная серия {series!r}")
        if len(number) != 6 or not number.isdigit():
            raise RegistryParseError(f"строка {i}: некорректный номер {number!r}")
        rows.append({"passport_series": series, "passport_number": number, "loaded_at": loaded_at})
    return rows


_VALID_LIST_TYPES = {"te", "fromu", "mvk", "un"}


def parse_sanctions_csv(content: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    loaded_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(reader, start=1):
        try:
            list_type = row["list_type"].strip()
            full_name = row["full_name"].strip()
        except KeyError as exc:
            raise RegistryParseError(f"строка {i}: не хватает колонки {exc}") from exc
        if list_type not in _VALID_LIST_TYPES:
            raise RegistryParseError(f"строка {i}: неизвестный list_type {list_type!r}")
        if not full_name:
            raise RegistryParseError(f"строка {i}: пустое full_name")

        birth_date_raw = (row.get("birth_date") or "").strip()
        birth_date: date | None = None
        if birth_date_raw:
            try:
                birth_date = date.fromisoformat(birth_date_raw)
            except ValueError as exc:
                raise RegistryParseError(f"строка {i}: некорректная дата рождения {birth_date_raw!r}") from exc

        rows.append(
            {
                "list_type": list_type,
                "full_name": full_name,
                "name_normalized": normalize_name(full_name),
                "birth_date": birth_date,
                "loaded_at": loaded_at,
            }
        )
    return rows
