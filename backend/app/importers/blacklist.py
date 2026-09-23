"""Импорт чёрного списка из CSV (Word-файл конвертируется вручную — ТЗ, T08).
В отличие от реестров МВД/Росфинмониторинга это небольшой curated список: строки
добавляются (append), а не атомарно заменяют всю таблицу — но повторный импорт
того же файла не должен плодить дубли (см. _row_exists)."""

import csv
import io
from datetime import date
from typing import Any

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


async def import_blacklist_csv(session: AsyncSession, content: bytes) -> int:
    """Ожидаемые колонки CSV: last_name, first_name, middle_name, birth_date
    (ISO), passport_series, passport_number, phone, reason. reason обязателен,
    остальное — по наличию данных. Возвращает число реально добавленных строк
    (без учёта пропущенных дублей)."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    imported = 0
    for i, row in enumerate(reader, start=1):
        reason = _clean(row.get("reason"))
        if not reason:
            raise BlacklistImportError(f"строка {i}: не заполнен reason")

        birth_date_raw = _clean(row.get("birth_date"))
        birth_date: date | None = None
        if birth_date_raw:
            try:
                birth_date = date.fromisoformat(birth_date_raw)
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

        if await _row_exists(session, fields):
            continue

        session.add(Blacklist(**fields))
        imported += 1

    await session.flush()
    return imported
