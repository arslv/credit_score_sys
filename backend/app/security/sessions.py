"""Сессии сотрудников (T12): непрозрачный токен в httpOnly-cookie, сам токен
не хранится — только его хеш (тот же принцип, что у пароля). Отдельное
приложение админки на другом origin — поэтому SameSite=Strict плюс проверка
служебного заголовка на изменяющих запросах (app/security/csrf.py), а не
привычная same-site форма."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Employee, Session

COOKIE_NAME = "session"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_session_cookie(response: Response, token: str, *, max_age_seconds: int) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=max_age_seconds, httponly=True,
        secure=get_settings().cookie_secure, samesite="strict", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def create_session(session: AsyncSession, *, employee_id, request: Request | None = None) -> str:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session.add(
        Session(
            employee_id=employee_id,
            token_hash=_hash_token(token),
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
            last_seen_at=now,
            ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
    await session.flush()
    return token


async def get_valid_session(session: AsyncSession, token: str | None) -> tuple[Session, Employee] | None:
    """None — токена нет, сессия не найдена/отозвана/истёк абсолютный срок или
    тайм-аут бездействия. Не различает эти случаи наружу (правило единообразного
    401 — см. app/security/deps.py) — не даёт понять снаружи, какая именно
    причина отказа."""
    if not token:
        return None

    settings = get_settings()
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(Session, Employee)
            .join(Employee, Employee.id == Session.employee_id)
            .where(Session.token_hash == _hash_token(token))
        )
    ).first()
    if row is None:
        return None
    session_row, employee = row

    if session_row.revoked_at is not None:
        return None
    if session_row.expires_at <= now:
        return None
    idle_deadline = session_row.last_seen_at + timedelta(minutes=settings.session_idle_timeout_minutes)
    if idle_deadline <= now:
        return None
    if not employee.active:
        return None

    session_row.last_seen_at = now
    await session.flush()
    return session_row, employee


async def revoke_session(session: AsyncSession, token: str) -> None:
    row = (
        await session.execute(select(Session).where(Session.token_hash == _hash_token(token)))
    ).scalar_one_or_none()
    if row is not None:
        row.revoked_at = datetime.now(timezone.utc)
    await session.flush()


async def revoke_all_sessions_for_employee(session: AsyncSession, employee_id) -> None:
    """Вызывается при смене пароля/роли/деактивации (T12, ваше уточнение) —
    жёстко удаляем, не просто помечаем: не оставляем шанс токену пережить это
    через гонку с ещё не закоммиченным revoked_at."""
    await session.execute(delete(Session).where(Session.employee_id == employee_id))
    await session.flush()
