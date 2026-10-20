"""Настройки сервиса в админке (только admin): тумблер распознавания фото
паспорта. API-ключ провайдера здесь не задаётся и не показывается — он только
в переменной окружения ANTHROPIC_API_KEY (правило 8 CLAUDE.md); экран видит
лишь, задан ли он."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Employee
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.system_settings import get_ocr_setting, set_ocr_enabled

router = APIRouter(prefix="/api/settings", tags=["settings"])


class OcrSettingsOut(BaseModel):
    enabled: bool
    api_key_configured: bool
    model: str
    changed_at: datetime | None
    changed_by: str | None


class OcrSettingsIn(BaseModel):
    enabled: bool


async def _ocr_settings_out(session: AsyncSession) -> OcrSettingsOut:
    settings = get_settings()
    enabled, changed_at, employee_id = await get_ocr_setting(session)
    changed_by = None
    if employee_id is not None:
        employee = await session.get(Employee, employee_id)
        changed_by = employee.full_name if employee else None
    return OcrSettingsOut(
        enabled=enabled,
        api_key_configured=bool(settings.anthropic_api_key.strip()),
        model=settings.ocr_model,
        changed_at=changed_at,
        changed_by=changed_by,
    )


@router.get("/ocr", response_model=OcrSettingsOut)
async def get_ocr_settings(
    _employee: Employee = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> OcrSettingsOut:
    return await _ocr_settings_out(session)


@router.put("/ocr", response_model=OcrSettingsOut)
async def update_ocr_settings(
    payload: OcrSettingsIn,
    request: Request,
    employee: Employee = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> OcrSettingsOut:
    enabled, _, _ = await get_ocr_setting(session)
    if payload.enabled != enabled:
        row = await set_ocr_enabled(session, payload.enabled, employee_id=employee.id)
        await record_audit(
            session, employee_id=employee.id, action="change_setting", object_type="system_setting",
            object_id=row.id, request=request,
            details={"key": row.key, "from": enabled, "to": payload.enabled},
        )
        await session.commit()
    return await _ocr_settings_out(session)

