from datetime import date, datetime, timezone

from app.models import Blacklist, InvalidPassport
from app.sources.base import CheckStatus, Subject
from app.sources.blacklist import BlacklistSource
from app.sources.mvd import MvdSource


async def test_mvd_no_data_without_passport(db_session):
    result = await MvdSource(db_session).check(Subject())
    assert result.status == CheckStatus.NO_DATA


async def test_mvd_ok_when_not_in_registry(db_session):
    result = await MvdSource(db_session).check(Subject(passport_series="1111", passport_number="222222"))
    assert result.status == CheckStatus.OK


async def test_mvd_hit_when_in_registry(db_session):
    db_session.add(
        InvalidPassport(passport_series="1111", passport_number="222222", loaded_at=datetime.now(timezone.utc))
    )
    await db_session.flush()

    result = await MvdSource(db_session).check(Subject(passport_series="1111", passport_number="222222"))
    assert result.status == CheckStatus.HIT
    assert "loaded_at" in result.parsed


async def test_blacklist_no_data_without_identifiers(db_session):
    result = await BlacklistSource(db_session).check(Subject())
    assert result.status == CheckStatus.NO_DATA


async def test_blacklist_hit_by_passport(db_session):
    db_session.add(
        Blacklist(passport_series="3333", passport_number="444444", reason="тестовая причина", active=True)
    )
    await db_session.flush()

    result = await BlacklistSource(db_session).check(
        Subject(passport_series="3333", passport_number="444444")
    )
    assert result.status == CheckStatus.HIT
    assert result.parsed["reason"] == "тестовая причина"


async def test_blacklist_hit_by_phone(db_session):
    db_session.add(Blacklist(phone="+79990001122", reason="тестовая причина", active=True))
    await db_session.flush()

    result = await BlacklistSource(db_session).check(Subject(phone="+79990001122"))
    assert result.status == CheckStatus.HIT


async def test_blacklist_ignores_inactive_entries(db_session):
    db_session.add(Blacklist(phone="+79990001122", reason="снято с контроля", active=False))
    await db_session.flush()

    result = await BlacklistSource(db_session).check(Subject(phone="+79990001122"))
    assert result.status == CheckStatus.OK


async def test_blacklist_ok_when_no_match(db_session):
    result = await BlacklistSource(db_session).check(
        Subject(passport_series="9999", passport_number="999999", phone="+70000000000")
    )
    assert result.status == CheckStatus.OK
