"""T12: подавление ложных совпадений — юнит-уровень (без прогона всего
конвейера, см. также tests/test_api_compliance.py для интеграционных)."""

import uuid
from datetime import date, datetime, timezone

from app.engine.compliance_overrides import suppress_reviewed_false_positive
from app.models import Client, ComplianceReview, Employee, SanctionsList
from app.security.passwords import hash_password


_KEY = "name_dob:т т|"


async def _seed_deps(db_session) -> tuple:
    """ComplianceReview.client_id/employee_id — реальные внешние ключи, для
    db_session (настоящее соединение с БД) нужны существующие строки,
    случайный uuid здесь не пройдёт flush(). sanctions_entry_key — не FK,
    просто строка (T12, ваше уточнение — устойчивый признак записи, не
    sanctions_list.id, см. app/models/auth.py)."""
    client = Client(
        last_name="Т", first_name="Т", birth_date=date(1990, 1, 1),
        passport_series=f"{uuid.uuid4().int % 10000:04d}", passport_number=f"{uuid.uuid4().int % 1000000:06d}",
        phone="+70000000000",
    )
    employee = Employee(full_name="Т", email=f"{uuid.uuid4().hex}@example.com", role="compliance", password_hash=hash_password("x"))
    db_session.add_all([client, employee])
    await db_session.flush()
    return client.id, employee.id


async def test_no_client_id_never_suppresses(db_session):
    status, parsed = await suppress_reviewed_false_positive(
        db_session, client_id=None, check_status="hit", check_parsed={"sanctions_entry_key": _KEY},
    )
    assert status == "hit"


async def test_non_hit_status_passes_through_unchanged(db_session):
    status, parsed = await suppress_reviewed_false_positive(
        db_session, client_id=uuid.uuid4(), check_status="ok", check_parsed=None,
    )
    assert status == "ok"
    assert parsed is None


async def test_hit_without_review_is_not_suppressed(db_session):
    status, _parsed = await suppress_reviewed_false_positive(
        db_session, client_id=uuid.uuid4(), check_status="hit",
        check_parsed={"sanctions_entry_key": _KEY},
    )
    assert status == "hit"


async def test_false_positive_review_suppresses_hit(db_session):
    client_id, employee_id = await _seed_deps(db_session)
    db_session.add(
        ComplianceReview(
            client_id=client_id, sanctions_entry_key=_KEY, employee_id=employee_id,
            verdict="false_positive", created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    status, parsed = await suppress_reviewed_false_positive(
        db_session, client_id=client_id, check_status="hit",
        check_parsed={"sanctions_entry_key": _KEY},
    )
    assert status == "ok"
    assert parsed["false_positive_reviewed"] is True


async def test_confirmed_review_does_not_suppress(db_session):
    client_id, employee_id = await _seed_deps(db_session)
    db_session.add(
        ComplianceReview(
            client_id=client_id, sanctions_entry_key=_KEY, employee_id=employee_id,
            verdict="confirmed", created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    status, _parsed = await suppress_reviewed_false_positive(
        db_session, client_id=client_id, check_status="hit",
        check_parsed={"sanctions_entry_key": _KEY},
    )
    assert status == "hit"


async def test_latest_verdict_for_pair_wins(db_session):
    client_id, employee_id = await _seed_deps(db_session)
    db_session.add(
        ComplianceReview(
            client_id=client_id, sanctions_entry_key=_KEY, employee_id=employee_id,
            verdict="false_positive", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        ComplianceReview(
            client_id=client_id, sanctions_entry_key=_KEY, employee_id=employee_id,
            verdict="confirmed", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
    )
    await db_session.flush()

    status, _parsed = await suppress_reviewed_false_positive(
        db_session, client_id=client_id, check_status="hit",
        check_parsed={"sanctions_entry_key": _KEY},
    )
    assert status == "hit"  # confirmed позже false_positive — не подавляет


async def test_different_sanctions_entry_key_not_suppressed(db_session):
    client_id, employee_id = await _seed_deps(db_session)
    db_session.add(
        ComplianceReview(
            client_id=client_id, sanctions_entry_key=_KEY, employee_id=employee_id,
            verdict="false_positive", created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    status, _parsed = await suppress_reviewed_false_positive(
        db_session, client_id=client_id, check_status="hit",
        check_parsed={"sanctions_entry_key": "name_dob:д д|"},
    )
    assert status == "hit"


class _BrokenSession:
    """Сессия, у которой execute() всегда падает — симулирует сбой БД именно
    в поиске пометки (ваше уточнение: при сбое — отказ, не подавление)."""

    async def execute(self, *args, **kwargs):
        raise RuntimeError("симулированный сбой БД")


async def test_lookup_failure_fails_closed_does_not_suppress(monkeypatch):
    status, parsed = await suppress_reviewed_false_positive(
        _BrokenSession(), client_id=uuid.uuid4(), check_status="hit",
        check_parsed={"sanctions_entry_key": _KEY},
    )
    assert status == "hit"  # не подавлено несмотря на найденную бы пометку
    assert parsed == {"sanctions_entry_key": _KEY}  # parsed не тронут


async def test_same_entry_reloaded_with_new_row_id_still_suppressed(db_session):
    """Реестр перезаливается целиком — id строки меняется даже у неизменной
    записи (app/registries/sync.py). Ключ подавления — sanctions_entry_key,
    не sanctions_list.id, поэтому переживает такую перезаливку (ваше
    уточнение): старая пометка не слетает, если содержимое записи то же."""
    client_id, employee_id = await _seed_deps(db_session)
    db_session.add(
        ComplianceReview(
            client_id=client_id, sanctions_entry_key=_KEY, employee_id=employee_id,
            verdict="false_positive", created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    # Тот же sanctions_entry_key, но от СОВЕРШЕННО другой строки (другой id) —
    # именно так выглядит совпадение после перезаливки реестра той же записью.
    reloaded_entry = SanctionsList(
        list_type="te", full_name="Т Т", name_normalized="т т",
        birth_date=None, loaded_at=datetime.now(timezone.utc),
    )
    db_session.add(reloaded_entry)
    await db_session.flush()
    assert reloaded_entry.id  # новый id, но тот же ключ ниже — вручную, как считает RosfinmonSource

    status, parsed = await suppress_reviewed_false_positive(
        db_session, client_id=client_id, check_status="hit",
        check_parsed={"sanctions_list_id": str(reloaded_entry.id), "sanctions_entry_key": _KEY},
    )
    assert status == "ok"
    assert parsed["false_positive_reviewed"] is True
