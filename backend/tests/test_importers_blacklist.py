from pathlib import Path

import pytest
from sqlalchemy import select

from app.importers.blacklist import BlacklistImportError, import_blacklist_csv
from app.models import Blacklist

FIXTURE = Path(__file__).parent / "fixtures" / "blacklist_sample.csv"


async def test_import_blacklist_csv_creates_rows(db_session):
    content = FIXTURE.read_bytes()
    imported = await import_blacklist_csv(db_session, content)

    assert imported == 2

    rows = (await db_session.execute(select(Blacklist))).scalars().all()
    assert len(rows) == 2
    assert {r.reason for r in rows} == {
        "мошенничество (синтетические данные)",
        "долг по прошлой рассрочке (синтетические данные)",
    }


async def test_repeated_import_of_same_csv_does_not_duplicate(db_session):
    content = FIXTURE.read_bytes()

    first_pass = await import_blacklist_csv(db_session, content)
    second_pass = await import_blacklist_csv(db_session, content)

    assert first_pass == 2
    assert second_pass == 0  # всё уже было — новых строк нет

    rows = (await db_session.execute(select(Blacklist))).scalars().all()
    assert len(rows) == 2


async def test_import_blacklist_missing_reason_raises(db_session):
    content = b"last_name,first_name,reason\n\xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xbe\xd0\xb2,\xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd,\n"
    with pytest.raises(BlacklistImportError):
        await import_blacklist_csv(db_session, content)


async def test_import_blacklist_bad_birth_date_raises(db_session):
    content = (
        "last_name,first_name,birth_date,reason\n"
        "Тестов,Тест,не-дата,причина\n"
    ).encode("utf-8")
    with pytest.raises(BlacklistImportError):
        await import_blacklist_csv(db_session, content)
