"""T12: журнал аудита — кто что смотрел и менял (ТЗ, «Журнал аудита»: admin)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditLog, Employee
from app.security.deps import require_roles
from app.security.pagination import Page, PageParams

router = APIRouter(prefix="/api/audit-log", tags=["audit"])


class AuditLogOut(BaseModel):
    id: int
    employee_id: UUID | None
    action: str
    object_type: str | None
    object_id: UUID | None
    ip: str | None
    created_at: datetime


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_log(
    params: PageParams = Depends(),
    action: str | None = None,
    employee_id: UUID | None = None,
    _employee: Employee = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if employee_id:
        stmt = stmt.where(AuditLog.employee_id == employee_id)

    total = len((await session.execute(stmt)).scalars().all())
    rows = (
        await session.execute(stmt.order_by(AuditLog.created_at.desc()).offset(params.offset).limit(params.page_size))
    ).scalars().all()

    return Page(
        items=[
            AuditLogOut(
                id=r.id, employee_id=r.employee_id, action=r.action, object_type=r.object_type,
                object_id=r.object_id, ip=str(r.ip) if r.ip else None, created_at=r.created_at,
            )
            for r in rows
        ],
        total=total, page=params.page, page_size=params.page_size,
    )
