"""T12: вход/выход. Ответ на неудачный вход одинаковый независимо от причины
(нет такого email, неверный пароль, блокировка учётной записи) — и по коду
ответа, и по времени (ваше уточнение): для несуществующего email тоже считаем
хеш пароля, чтобы не давать понять по задержке, что аккаунта не существует."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Employee
from app.redis import get_redis
from app.security.audit import record_audit
from app.security.deps import get_current_employee
from app.security.passwords import verify_dummy_password, verify_password
from app.security.rate_limit import is_ip_blocked, register_failed_login_and_check_ip_blocked
from app.security.sessions import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    revoke_session,
    set_session_cookie,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_GENERIC_LOGIN_ERROR = "неверный email или пароль"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EmployeeOut(BaseModel):
    id: str
    full_name: str
    email: str
    role: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=EmployeeOut)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> EmployeeOut:
    redis = get_redis()
    ip = _client_ip(request)
    if await is_ip_blocked(redis, ip):
        # Отдельный код (429) — не выдаёт, есть ли такой email, только что
        # именно с этого адреса стоит подождать.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="слишком много попыток входа, попробуйте позже"
        )

    employee = (
        await session.execute(select(Employee).where(Employee.email == payload.email))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)

    # Пароль (или пустышку) считаем ВСЕГДА, независимо от того, что дальше
    # решит исход — иначе разные ветки занимают разное время и по задержке
    # ответа можно понять, существует ли email.
    if employee is None:
        verify_dummy_password(payload.password)
        password_ok = False
    else:
        password_ok = verify_password(employee.password_hash, payload.password)

    account_locked = employee is not None and employee.locked_until is not None and employee.locked_until > now
    success = employee is not None and password_ok and employee.active and not account_locked

    if not success:
        # Возврат учитываем СРАЗУ: попытка, которая переводит счётчик за порог,
        # получает 429 немедленно — иначе ровно одна лишняя попытка (порог+1-я)
        # проскакивала бы как обычный 401, и блокировка видна только с (порог+2)-й.
        ip_now_blocked = await register_failed_login_and_check_ip_blocked(redis, ip)
        if employee is not None and not account_locked:
            settings = get_settings()
            employee.failed_login_count += 1
            if employee.failed_login_count >= settings.login_account_max_failed_attempts:
                employee.locked_until = now + timedelta(minutes=settings.login_account_lockout_minutes)
        await record_audit(
            session, employee_id=employee.id if employee else None, action="login_failed",
            object_type="employee" if employee else None, object_id=employee.id if employee else None,
            request=request,
        )
        await session.commit()
        if ip_now_blocked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="слишком много попыток входа, попробуйте позже",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    employee.failed_login_count = 0
    employee.locked_until = None
    token = await create_session(session, employee_id=employee.id, request=request)
    await record_audit(session, employee_id=employee.id, action="login", request=request)
    await session.commit()

    set_session_cookie(response, token, max_age_seconds=get_settings().session_ttl_hours * 3600)
    return EmployeeOut(id=str(employee.id), full_name=employee.full_name, email=employee.email, role=employee.role)


@router.post("/logout")
async def logout(
    request: Request, response: Response,
    employee: Employee = Depends(get_current_employee), session: AsyncSession = Depends(get_session),
) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await revoke_session(session, token)
    await record_audit(session, employee_id=employee.id, action="logout", request=request)
    await session.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=EmployeeOut)
async def me(employee: Employee = Depends(get_current_employee)) -> EmployeeOut:
    return EmployeeOut(id=str(employee.id), full_name=employee.full_name, email=employee.email, role=employee.role)
