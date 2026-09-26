"""FastAPI-зависимости: текущий сотрудник по сессии, проверка роли на каждом
эндпоинте (T12, ТЗ «Роли, доступ и персданные»)."""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Employee
from app.security.sessions import COOKIE_NAME, get_valid_session

# risk_manager/admin/viewer/compliance — только risk_manager и compliance
# видят персданные заявителей в заявках/клиентах (ваше уточнение: admin —
# как viewer для этих разделов, ему для работы нужны сотрудники/справочники/
# аудит/состояние системы, не паспорта клиентов).
ROLES_WITH_FULL_PII = ("risk_manager", "compliance")


async def get_current_employee(request: Request, session: AsyncSession = Depends(get_session)) -> Employee:
    token = request.cookies.get(COOKIE_NAME)
    result = await get_valid_session(session, token)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="не авторизовано")
    _session_row, employee = result
    request.state.employee = employee
    return employee


def require_roles(*roles: str):
    async def _dependency(employee: Employee = Depends(get_current_employee)) -> Employee:
        if employee.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="недостаточно прав")
        return employee

    return _dependency


def has_full_pii(employee: Employee) -> bool:
    return employee.role in ROLES_WITH_FULL_PII


def require_any_authenticated():
    return require_roles("risk_manager", "admin", "viewer", "compliance")
