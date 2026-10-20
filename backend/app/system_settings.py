"""Настройки сервиса, которые админ меняет в админке (таблица system_settings,
только INSERT — действует последняя строка по ключу). Сейчас одна: включено
ли распознавание фото паспорта.

Распознавание выключено -> новая заявка не уходит на распознавание (фото не
отправляются провайдеру), получает статус awaiting_data и ждёт паспортные
данные от CRM в /review (docs/integration_contract.md, блок applicant).
Без строки в таблице распознавание включено — прежнее поведение."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

OCR_ENABLED_KEY = "ocr_enabled"


async def _latest(session: AsyncSession, key: str) -> SystemSetting | None:
    return (
        await session.execute(
            select(SystemSetting).where(SystemSetting.key == key).order_by(SystemSetting.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def get_ocr_setting(session: AsyncSession) -> tuple[bool, datetime | None, UUID | None]:
    """(включено, когда изменено, кем). Без записи — включено, изменений не было."""
    row = await _latest(session, OCR_ENABLED_KEY)
    if row is None:
        return True, None, None
    return bool(row.value.get("enabled", True)), row.created_at, row.employee_id


async def is_ocr_enabled(session: AsyncSession) -> bool:
    enabled, _, _ = await get_ocr_setting(session)
    return enabled


async def set_ocr_enabled(session: AsyncSession, enabled: bool, *, employee_id: UUID | None) -> SystemSetting:
    """Не коммитит — коммит (вместе с записью аудита) на вызывающем коде."""
    # created_at — из приложения, а не now() базы: now() одинаков для всей
    # транзакции, и два изменения в одной транзакции не упорядочились бы.
    row = SystemSetting(
        key=OCR_ENABLED_KEY, value={"enabled": enabled}, employee_id=employee_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row
