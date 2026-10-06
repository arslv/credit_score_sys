"""Внутренние дубли: один номер паспорта в нашей базе с разными ФИО или датой
рождения (T08). clients пока не создаются (заполняются только после успешного
распознавания — см. T02, задача ещё не реализована), поэтому источник правды на
этом этапе — последние результаты OCR прошлых заявок."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OcrResult
from app.sources.name_normalize import full_name, normalize_name


async def is_passport_reused_with_different_identity(
    session: AsyncSession,
    *,
    application_id: UUID,
    passport_series: str,
    passport_number: str,
    last_name: str,
    first_name: str,
    middle_name: str | None,
    birth_date: str | None,
) -> bool:
    """True — та же серия+номер уже встречались у ДРУГОЙ заявки с другим ФИО или
    датой рождения (сравнение по последнему OCR каждой такой заявки)."""
    stmt = (
        select(OcrResult.application_id, OcrResult.fields)
        .where(
            # JSONB-путь ("fields", ...) — та же обёртка {"fields": {...}},
            # что распаковывает app.engine.pipeline.unwrap_ocr_fields для
            # Python-кода; здесь путь строится для SQL-запроса напрямую,
            # unwrap_ocr_fields тут не применить (сравнение значения внутри
            # БД, не чтение объекта в Python) — тот же первый уровень обёртки.
            OcrResult.application_id != application_id,
            OcrResult.fields[("fields", "passport_series", "value")].astext == passport_series,
            OcrResult.fields[("fields", "passport_number", "value")].astext == passport_number,
        )
        .order_by(OcrResult.application_id, OcrResult.created_at.desc())
        .distinct(OcrResult.application_id)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return False

    query_name = normalize_name(full_name(last_name, first_name, middle_name))

    for _, fields in rows:
        other_fields: dict[str, Any] = (fields or {}).get("fields", {})
        other_name = normalize_name(
            full_name(
                (other_fields.get("last_name") or {}).get("value") or "",
                (other_fields.get("first_name") or {}).get("value") or "",
                (other_fields.get("middle_name") or {}).get("value"),
            )
        )
        other_birth_date = (other_fields.get("birth_date") or {}).get("value")
        if other_name != query_name or other_birth_date != birth_date:
            return True
    return False
