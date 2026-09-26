"""T12: сессии — создание, валидация, абсолютный TTL, тайм-аут бездействия,
отзыв (ваши уточнения)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Employee, Session
from app.security.passwords import hash_password
from app.security.sessions import (
    create_session,
    get_valid_session,
    revoke_all_sessions_for_employee,
    revoke_session,
)


async def _make_employee(db_session, **overrides) -> Employee:
    employee = Employee(
        full_name="Тест Тестов", email=f"t-{id(overrides)}@example.com", role="admin",
        password_hash=hash_password("x"), **overrides,
    )
    db_session.add(employee)
    await db_session.flush()
    return employee


async def test_create_and_validate_round_trip(db_session):
    employee = await _make_employee(db_session)
    token = await create_session(db_session, employee_id=employee.id)

    result = await get_valid_session(db_session, token)
    assert result is not None
    session_row, found_employee = result
    assert found_employee.id == employee.id


async def test_unknown_token_is_invalid(db_session):
    assert await get_valid_session(db_session, "not-a-real-token") is None


async def test_no_token_is_invalid(db_session):
    assert await get_valid_session(db_session, None) is None


async def test_expired_session_is_invalid(db_session):
    employee = await _make_employee(db_session)
    token = await create_session(db_session, employee_id=employee.id)

    row = (await db_session.execute(select(Session).where(Session.employee_id == employee.id))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.flush()

    assert await get_valid_session(db_session, token) is None


async def test_idle_timeout_invalidates_session_before_absolute_expiry(db_session):
    """Тайм-аут бездействия (30 минут, в конфиге) — поверх общего срока
    (ваше уточнение): сессия с ещё не истёкшим expires_at, но старым
    last_seen_at, недействительна."""
    employee = await _make_employee(db_session)
    token = await create_session(db_session, employee_id=employee.id)

    row = (await db_session.execute(select(Session).where(Session.employee_id == employee.id))).scalar_one()
    assert row.expires_at > datetime.now(timezone.utc) + timedelta(hours=1)  # абсолютный TTL ещё далеко
    row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    await db_session.flush()

    assert await get_valid_session(db_session, token) is None


async def test_revoked_session_is_invalid(db_session):
    employee = await _make_employee(db_session)
    token = await create_session(db_session, employee_id=employee.id)
    await revoke_session(db_session, token)
    assert await get_valid_session(db_session, token) is None


async def test_inactive_employee_session_is_invalid(db_session):
    employee = await _make_employee(db_session, active=False)
    token = await create_session(db_session, employee_id=employee.id)
    assert await get_valid_session(db_session, token) is None


async def test_revoke_all_sessions_for_employee_invalidates_every_token(db_session):
    employee = await _make_employee(db_session)
    token_1 = await create_session(db_session, employee_id=employee.id)
    token_2 = await create_session(db_session, employee_id=employee.id)

    await revoke_all_sessions_for_employee(db_session, employee.id)

    assert await get_valid_session(db_session, token_1) is None
    assert await get_valid_session(db_session, token_2) is None


async def test_valid_session_touches_last_seen_at(db_session):
    employee = await _make_employee(db_session)
    token = await create_session(db_session, employee_id=employee.id)

    row = (await db_session.execute(select(Session).where(Session.employee_id == employee.id))).scalar_one()
    row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.flush()
    old_last_seen = row.last_seen_at

    await get_valid_session(db_session, token)
    await db_session.refresh(row)
    assert row.last_seen_at > old_last_seen
