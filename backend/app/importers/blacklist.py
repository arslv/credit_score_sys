"""Импорт чёрного списка из CSV (Word-файл конвертируется вручную — ТЗ, T08).
В отличие от реестров МВД/Росфинмониторинга это небольшой curated список: строки
добавляются (append), а не атомарно заменяют всю таблицу — но повторный импорт
того же файла не должен плодить дубли (см. _row_exists)."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Blacklist


class BlacklistImportError(Exception):
    pass


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _eq_or_null(column: Any, value: Any):
    return column.is_(None) if value is None else column == value


async def _row_exists(session: AsyncSession, fields: dict[str, Any]) -> bool:
    conditions = [_eq_or_null(getattr(Blacklist, name), value) for name, value in fields.items()]
    existing = (await session.execute(select(Blacklist.id).where(*conditions).limit(1))).scalar_one_or_none()
    return existing is not None


@dataclass
class BlacklistImportResult:
    imported: int  # реально добавлено
    skipped: int  # уже были в списке (дубли)


def _parse_birth_date(raw: str) -> date:
    """ГГГГ-ММ-ДД или ДД.ММ.ГГГГ (так дату сохраняет русский Excel)."""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return datetime.strptime(raw, "%d.%m.%Y").date()


def _reader(content: bytes) -> csv.DictReader:
    """T23: UTF-8 с BOM и без; разделитель «,» или «;» (русский Excel пишет «;»)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BlacklistImportError("файл не в кодировке UTF-8 — сохраните его как «CSV UTF-8»") from exc
    header = text.split("\n", 1)[0]
    delimiter = ";" if header.count(";") > header.count(",") else ","
    return csv.DictReader(io.StringIO(text), delimiter=delimiter)


async def import_blacklist_csv(session: AsyncSession, content: bytes) -> int:
    """Число реально добавленных строк (без пропущенных дублей) — см.
    import_blacklist_csv_report."""
    return (await import_blacklist_csv_report(session, content)).imported


async def import_blacklist_csv_report(
    session: AsyncSession, content: bytes, *, added_by: UUID | None = None,
) -> BlacklistImportResult:
    """Ожидаемые колонки CSV: last_name, first_name, middle_name, birth_date
    (ГГГГ-ММ-ДД или ДД.ММ.ГГГГ), passport_series, passport_number, phone,
    reason. reason обязателен, остальное — по наличию данных. Ошибка в любой
    строке — исключение, вызывающий не фиксирует транзакцию (файл не
    загружается частично). Формат предварительный — уточняется с заказчиком."""
    reader = _reader(content)
    if reader.fieldnames is None or "reason" not in reader.fieldnames:
        raise BlacklistImportError("в файле нет колонки reason (причина) — проверьте первую строку")
    imported = 0
    skipped = 0
    for i, row in enumerate(reader, start=1):
        reason = _clean(row.get("reason"))
        if not reason:
            raise BlacklistImportError(f"строка {i}: не заполнен reason")

        birth_date_raw = _clean(row.get("birth_date"))
        birth_date: date | None = None
        if birth_date_raw:
            try:
                birth_date = _parse_birth_date(birth_date_raw)
            except ValueError as exc:
                raise BlacklistImportError(f"строка {i}: некорректная дата рождения {birth_date_raw!r}") from exc

        fields = {
            "last_name": _clean(row.get("last_name")),
            "first_name": _clean(row.get("first_name")),
            "middle_name": _clean(row.get("middle_name")),
            "birth_date": birth_date,
            "passport_series": _clean(row.get("passport_series")),
            "passport_number": _clean(row.get("passport_number")),
            "phone": _clean(row.get("phone")),
            "reason": reason,
        }

        series, number = fields["passport_series"], fields["passport_number"]
        if series is not None and not (len(series) == 4 and series.isdigit()):
            raise BlacklistImportError(f"строка {i}: серия паспорта — 4 цифры, получено {series!r}")
        if number is not None and not (len(number) == 6 and number.isdigit()):
            raise BlacklistImportError(f"строка {i}: номер паспорта — 6 цифр, получено {number!r}")

        if await _row_exists(session, fields):
            skipped += 1
            continue

        session.add(Blacklist(**fields, added_by=added_by))
        imported += 1

    await session.flush()
    return BlacklistImportResult(imported=imported, skipped=skipped)
