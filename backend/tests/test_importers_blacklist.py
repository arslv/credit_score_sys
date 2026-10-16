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


# --- T23: загрузка файлом через API (Справочники → Чёрный список) ---------------

async def test_blacklist_import_api_excel_csv_with_semicolons_and_russian_dates(admin_client, session_factory):
    from app.models import AuditLog
    from tests._admin_helpers import create_employee, login

    await create_employee(session_factory, email="bl-import@example.com", role="admin")
    await login(admin_client, "bl-import@example.com")
    content = (
        "\ufefflast_name;first_name;middle_name;birth_date;passport_series;passport_number;phone;reason\n"
        "Тестов;Пётр;Иванович;05.03.1980;8201;123456;;долг по прошлой рассрочке (синтетические данные)\n"
        "Пробный;Олег;;1979-12-01;;;;мошенничество (синтетические данные)\n"
    ).encode("utf-8")
    url = "/api/references/blacklist/import"
    first = await admin_client.post(url, files={"file": ("blacklist.csv", content, "text/csv")})
    assert first.status_code == 200, first.text
    assert first.json() == {"imported": 2, "skipped": 0}
    second = await admin_client.post(url, files={"file": ("blacklist.csv", content, "text/csv")})
    assert second.status_code == 200, second.text
    assert second.json() == {"imported": 0, "skipped": 2}

    async with session_factory() as session:
        rows = (await session.execute(select(Blacklist).where(Blacklist.last_name == "Тестов"))).scalars().all()
        assert len(rows) == 1 and rows[0].added_by is not None
        assert str(rows[0].birth_date) == "1980-03-05"
        audits = (await session.execute(select(AuditLog).where(AuditLog.object_type == "blacklist"))).scalars().all()
        assert [a.details["imported"] for a in audits if a.action == "import_data"] == [2, 0]


async def test_blacklist_import_api_rejects_bad_row_and_adds_nothing(admin_client, session_factory):
    from tests._admin_helpers import create_employee, login

    await create_employee(session_factory, email="bl-import2@example.com", role="admin")
    await login(admin_client, "bl-import2@example.com")
    content = (
        "last_name,first_name,passport_series,passport_number,reason\n"
        "Хороший,Иван,8201,123456,причина (синтетические данные)\n"
        "Плохой,Иван,82,123456,причина (синтетические данные)\n"
    ).encode("utf-8")
    response = await admin_client.post(
        "/api/references/blacklist/import", files={"file": ("bad.csv", content, "text/csv")},
    )
    assert response.status_code == 400
    assert "строка 2" in response.json()["detail"]
    async with session_factory() as session:
        assert (await session.execute(select(Blacklist).where(Blacklist.last_name == "Хороший"))).first() is None


async def test_blacklist_import_api_forbidden_for_operator(admin_client, session_factory):
    from tests._admin_helpers import create_employee, login

    await create_employee(session_factory, email="bl-import3@example.com", role="operator")
    await login(admin_client, "bl-import3@example.com")
    response = await admin_client.post(
        "/api/references/blacklist/import",
        files={"file": ("x.csv", b"last_name,reason\n\xd0\x90,x\n", "text/csv")},
    )
    assert response.status_code == 403
