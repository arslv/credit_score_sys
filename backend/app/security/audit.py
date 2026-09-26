"""Запись в audit_log — общий хелпер, чтобы формат не расходился между
эндпоинтами (T12, ТЗ «Требования по персональным данным», п.6/8)."""

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    employee_id,
    action: str,
    object_type: str | None = None,
    object_id=None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            employee_id=employee_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            ip=request.client.host if request and request.client else None,
            details=details,
        )
    )
    await session.flush()
