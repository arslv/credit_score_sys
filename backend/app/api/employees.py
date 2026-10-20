"""T12: сотрудники — учётные записи и роли (ТЗ, «Сотрудники»: меняет admin).
Смена пароля/роли/деактивации закрывает все сессии сотрудника; последнего
активного admin нельзя деактивировать или понизить (ваши уточнения).

T23: сотрудника можно удалить. Строку не стираем — на неё ссылается история
(audit_log, status_history, ruleset_versions… — append-only): ставим
deleted_at и active = false, закрываем сессии. Удалённый не виден в списке
(кроме include_deleted — для подписей в журнале аудита) и не меняется."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Employee
from app.models.employee import EMPLOYEE_ROLES
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.security.pagination import Page, PageParams
from app.security.passwords import hash_password
from app.security.sessions import revoke_all_sessions_for_employee

router = APIRouter(prefix="/api/employees", tags=["employees"])

_ADMIN_ONLY = ("admin",)


class EmployeeAccountOut(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str
    active: bool


class EmployeeCreate(BaseModel):
    full_name: str
    email: EmailStr
    role: str
    password: str


class EmployeeUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


async def _count_other_active_admins(session: AsyncSession, exclude_id: UUID) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.role == "admin", Employee.active.is_(True), Employee.deleted_at.is_(None),
                Employee.id != exclude_id,
            )
        )
    ).scalar_one()


@router.get("", response_model=Page[EmployeeAccountOut])
async def list_employees(
    params: PageParams = Depends(),
    include_deleted: bool = Query(False, description="и удалённые — для подписей в журнале аудита"),
    employee: Employee = Depends(require_roles(*_ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> Page[EmployeeAccountOut]:
    visible = [] if include_deleted else [Employee.deleted_at.is_(None)]
    total = (await session.execute(select(func.count()).select_from(Employee).where(*visible))).scalar_one()
    rows = (
        # id вторым ключом: при одинаковых ФИО порядок иначе не определён, и
        # соседние страницы пересекались — один сотрудник дважды, другой пропадал.
        await session.execute(
            select(Employee)
            .where(*visible)
            .order_by(Employee.full_name, Employee.id)
            .offset(params.offset)
            .limit(params.page_size)
        )
    ).scalars().all()
    return Page(
        items=[EmployeeAccountOut(id=r.id, full_name=r.full_name, email=r.email, role=r.role, active=r.active) for r in rows],
        total=total, page=params.page, page_size=params.page_size,
    )


@router.post("", response_model=EmployeeAccountOut, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate, request: Request,
    employee: Employee = Depends(require_roles(*_ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> EmployeeAccountOut:
    if payload.role not in EMPLOYEE_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"неизвестная роль: {payload.role!r}")
    existing = (
        await session.execute(select(Employee).where(Employee.email == payload.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="этот email был у удалённого сотрудника — укажите другой"
            if existing.deleted_at is not None
            else "сотрудник с таким email уже есть",
        )

    row = Employee(
        full_name=payload.full_name, email=payload.email, role=payload.role,
        password_hash=hash_password(payload.password),
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session, employee_id=employee.id, action="manage_employee",
        object_type="employee", object_id=row.id, request=request,
    )
    await session.commit()
    return EmployeeAccountOut(id=row.id, full_name=row.full_name, email=row.email, role=row.role, active=row.active)


@router.patch("/{employee_id}", response_model=EmployeeAccountOut)
async def update_employee(
    employee_id: UUID, payload: EmployeeUpdate, request: Request,
    employee: Employee = Depends(require_roles(*_ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> EmployeeAccountOut:
    row = await session.get(Employee, employee_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="сотрудник не найден")

    demoting = payload.role is not None and payload.role != "admin" and row.role == "admin"
    deactivating = payload.active is False and row.active

    if (demoting or deactivating) and row.role == "admin":
        if await _count_other_active_admins(session, exclude_id=row.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="нельзя деактивировать или понизить последнего активного администратора",
            )

    if payload.role is not None and payload.role not in EMPLOYEE_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"неизвестная роль: {payload.role!r}")

    security_sensitive_change = False
    if payload.full_name is not None:
        row.full_name = payload.full_name
    if payload.role is not None and payload.role != row.role:
        row.role = payload.role
        security_sensitive_change = True
    if payload.active is not None and payload.active != row.active:
        row.active = payload.active
        security_sensitive_change = True
    if payload.password:
        row.password_hash = hash_password(payload.password)
        security_sensitive_change = True

    if security_sensitive_change:
        # Смена пароля/роли/деактивации закрывает все сессии сотрудника (ваше
        # уточнение) — иначе украденная/оставленная открытой сессия продолжает
        # действовать под старыми правами до истечения TTL.
        await revoke_all_sessions_for_employee(session, row.id)

    await record_audit(
        session, employee_id=employee.id, action="manage_employee",
        object_type="employee", object_id=row.id, request=request,
    )
    await session.commit()
    return EmployeeAccountOut(id=row.id, full_name=row.full_name, email=row.email, role=row.role, active=row.active)


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_employee(
    employee_id: UUID, request: Request,
    employee: Employee = Depends(require_roles(*_ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await session.get(Employee, employee_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="сотрудник не найден")
    if row.id == employee.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="нельзя удалить самого себя")
    if row.role == "admin" and row.active and await _count_other_active_admins(session, exclude_id=row.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="нельзя удалить последнего активного администратора"
        )

    row.deleted_at = datetime.now(timezone.utc)
    row.active = False
    await revoke_all_sessions_for_employee(session, row.id)
    await record_audit(
        session, employee_id=employee.id, action="manage_employee",
        object_type="employee", object_id=row.id, request=request, details={"deleted": True},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
