"""T11: кэш отчёта БКИ (по client_id) и защита от двойной оплаты на уровне БД
(частичный уникальный индекс bki_requests_in_flight). Использует настоящий
session_factory (не db_session с rollback) — claim_in_flight/record_attempt
сами управляют своими транзакциями и должны видеть закоммиченные данные друг
друга, как в проде."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.engine.bki_cache import claim_in_flight, get_cached_report_row, record_attempt, release_in_flight
from app.models import Application, BkiRequestInFlight, Client


async def _make_client(session_factory) -> uuid.UUID:
    async with session_factory() as session:
        client = Client(
            last_name="Тестов", first_name="Тест", birth_date=datetime(1990, 1, 1).date(),
            passport_series=f"{uuid.uuid4().int % 10000:04d}", passport_number=f"{uuid.uuid4().int % 1000000:06d}",
            phone="+79990000000",
        )
        session.add(client)
        await session.flush()
        client_id = client.id
        await session.commit()
    return client_id


async def _make_application(session_factory, *, client_id=None) -> uuid.UUID:
    async with session_factory() as session:
        application = Application(
            client_id=client_id, external_id=f"ext-{uuid.uuid4().hex[:10]}", status="stage_b",
            goods_name="Холодильник", goods_price=Decimal("50000"), down_payment=Decimal("0"),
            term_months=12, tariff="standard", requested_amount=Decimal("50000"),
            profile={}, consent_pd_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.flush()
        application_id = application.id
        await session.commit()
    return application_id


# --- claim_in_flight / release_in_flight ---------------------------------------


async def test_claim_succeeds_when_no_other_request_in_flight(session_factory):
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    claim_id = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    assert claim_id is not None


async def test_second_claim_for_same_client_is_refused(session_factory):
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    first = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    second = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    assert first is not None
    assert second is None


async def test_claim_allowed_again_after_release(session_factory):
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    first = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    await release_in_flight(session_factory, first, failed=False)

    second = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    assert second is not None


async def test_stale_in_flight_row_is_expired_and_unblocks_new_claim(session_factory):
    """Воркер «упал» после записи in_flight, не завершив её — строка старше
    in_flight_timeout_minutes считается сбойной, новый запрос по клиенту
    становится возможен (уточнение к плану T11)."""
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    stuck_id = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    assert stuck_id is not None

    async with session_factory() as session:
        row = await session.get(BkiRequestInFlight, stuck_id)
        row.started_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        await session.commit()

    new_claim = await claim_in_flight(
        session_factory, client_id=client_id, application_id=application_id, timeout_minutes=10
    )
    assert new_claim is not None
    assert new_claim != stuck_id

    async with session_factory() as session:
        stuck_row = await session.get(BkiRequestInFlight, stuck_id)
        assert stuck_row.status == "failed"


async def test_claim_is_scoped_per_client(session_factory):
    client_a = await _make_client(session_factory)
    client_b = await _make_client(session_factory)
    application_a = await _make_application(session_factory, client_id=client_a)
    application_b = await _make_application(session_factory, client_id=client_b)

    claim_a = await claim_in_flight(
        session_factory, client_id=client_a, application_id=application_a, timeout_minutes=10
    )
    claim_b = await claim_in_flight(
        session_factory, client_id=client_b, application_id=application_b, timeout_minutes=10
    )
    assert claim_a is not None
    assert claim_b is not None


# --- record_attempt / get_cached_report_row -------------------------------------


async def test_no_cached_report_returns_none(session_factory):
    client_id = await _make_client(session_factory)
    async with session_factory() as session:
        row = await get_cached_report_row(session, client_id, 30)
    assert row is None


async def test_successful_attempt_is_visible_in_cache(session_factory):
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    await record_attempt(
        session_factory, client_id=client_id, application_id=application_id, status="ok",
        report_raw={"contracts": [], "inquiries": [], "score": 700}, features={"pti": 0.1},
        score=Decimal("700"), error_text=None,
    )

    async with session_factory() as session:
        row = await get_cached_report_row(session, client_id, 30)
    assert row is not None
    assert row.status == "ok"
    assert row.score == Decimal("700.00")


async def test_expired_cache_entry_is_not_returned(session_factory):
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    await record_attempt(
        session_factory, client_id=client_id, application_id=application_id, status="ok",
        report_raw={"contracts": [], "inquiries": [], "score": 700}, features={}, score=Decimal("700"),
        error_text=None,
    )
    async with session_factory() as session:
        from app.models import BkiReport as BkiReportRow

        report_row = (await session.execute(select(BkiReportRow))).scalars().one()
        report_row.requested_at = datetime.now(timezone.utc) - timedelta(days=31)
        await session.commit()

    async with session_factory() as session:
        cached = await get_cached_report_row(session, client_id, 30)
    assert cached is None


async def test_non_ok_attempts_are_not_cacheable(session_factory):
    """Журнал попыток пишет ВСЕ исходы (успех/ошибка/недоступность), но кэш
    отдаёт только status='ok' — сбойная попытка не должна выглядеть как
    успешный отчёт для следующей заявки того же клиента."""
    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    await record_attempt(
        session_factory, client_id=client_id, application_id=application_id, status="error",
        report_raw=None, features=None, score=None, error_text="таймаут бюро",
    )

    async with session_factory() as session:
        row = await get_cached_report_row(session, client_id, 30)
    assert row is None


async def test_record_attempt_writes_every_call_for_billing_reconciliation(session_factory):
    """Журнал оплаты — все попытки отдельно от успешных отчётов (уточнение к
    плану T11): ok/no_data/source_unavailable/error — каждая реальная попытка
    пишется отдельной строкой, доступной для подсчёта за месяц."""
    from app.models import BkiReport as BkiReportRow

    client_id = await _make_client(session_factory)
    application_id = await _make_application(session_factory, client_id=client_id)

    for status in ("ok", "no_data", "source_unavailable", "error"):
        await record_attempt(
            session_factory, client_id=client_id, application_id=application_id, status=status,
            report_raw=None, features=None, score=None, error_text=None if status == "ok" else "проблема",
        )

    async with session_factory() as session:
        rows = (
            await session.execute(select(BkiReportRow).where(BkiReportRow.client_id == client_id))
        ).scalars().all()
    assert {r.status for r in rows} == {"ok", "no_data", "source_unavailable", "error"}
    assert len(rows) == 4
