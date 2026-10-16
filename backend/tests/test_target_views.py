"""T15: представления target_bad_60_6/target_bad_90_12 — зрелость от даты
данных (ваше явное указание), не от сегодня. Случаи, когда таргет известен,
плюс контрольный случай «устаревшие данные — договор незрелый, хотя по
календарю окно уже прошло».

Ваше замечание после первой версии: закрытый договор — не всегда хороший
исход. Тестируем все три ветки closure_outcome отдельно (good/bad/unknown) —
«Тесты на каждую ветку»."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.engine.delinquency_recompute import recompute_all_contracts
from app.models import Contract, ImportBatch, Payment, PaymentSchedule
from tests._admin_helpers import create_employee


async def _contract(
    session, *, external_number, issued_at, term_months=6, closed_at=None, status="active",
    closure_reason=None, closure_outcome=None,
):
    contract = Contract(
        application_id=None, external_number=external_number, principal=Decimal("6000"), markup=Decimal("0"),
        total_amount=Decimal("6000"), down_payment=Decimal("0"), term_months=term_months,
        issued_at=issued_at, closed_at=closed_at, status=status,
        closure_reason=closure_reason, closure_outcome=closure_outcome,
    )
    session.add(contract)
    await session.flush()
    for n in range(1, term_months + 1):
        month = issued_at.month + n
        year = issued_at.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        session.add(
            PaymentSchedule(
                contract_id=contract.id, period_no=n, due_date=date(year, month, 1), due_amount=Decimal("1000"),
            )
        )
    return contract


async def _payments_batch(session, *, data_as_of, employee_id, suffix: str):
    batch = ImportBatch(
        kind="payments", filename="x.csv", file_sha256=suffix.rjust(64, "0"), storage_key="x",
        uploaded_by=employee_id, status="completed", data_as_of=data_as_of,
    )
    session.add(batch)
    await session.flush()
    return batch


async def _bad_and_mature(session, contract_id, view: str) -> tuple[int | None, bool]:
    row = (
        await session.execute(text(f"SELECT bad, is_mature FROM {view} WHERE contract_id = :cid"), {"cid": contract_id})
    ).one()
    return row.bad, row.is_mature


async def test_target_views_three_known_cases_and_stale_data(session_factory):
    employee_id = await create_employee(session_factory, email="rm-target@example.com", role="admin")

    async with session_factory() as session:
        # A: окно (6 мес.) полностью покрыто данными, вовремя погашено -> bad=0, зрелый.
        good = await _contract(session, external_number="VIEW-GOOD", issued_at=date(2025, 1, 1))
        for n in range(1, 7):
            month = 1 + n
            year = 2025 + (month - 1) // 12
            month = (month - 1) % 12 + 1
            session.add(Payment(contract_id=good.id, paid_at=date(year, month, 1), amount=Decimal("1000"), source="import", kind="regular"))

        # B: просрочка 60+ уже наступила внутри окна -> bad=1 (платежей вовсе нет).
        bad = await _contract(session, external_number="VIEW-BAD", issued_at=date(2025, 1, 1))

        # C: погашен досрочно внутри окна (closure_outcome=good) -> bad=0, зрелый,
        # даже если бы данных дальше не было.
        closed_good = await _contract(
            session, external_number="VIEW-CLOSED-GOOD", issued_at=date(2026, 6, 1), closed_at=date(2026, 7, 15),
            status="closed", closure_reason="Погашен досрочно", closure_outcome="good",
        )
        session.add(Payment(contract_id=closed_good.id, paid_at=date(2026, 7, 10), amount=Decimal("6000"), source="import", kind="regular"))

        # E (ваше замечание): товар изъят -> дефолт, bad=1, даже без единого
        # дня просрочки по графику (первый платёж ещё не наступил к upto_date).
        closed_bad = await _contract(
            session, external_number="VIEW-CLOSED-BAD", issued_at=date(2026, 8, 15), closed_at=date(2026, 8, 20),
            status="repossessed", closure_reason="Товар изъят", closure_outcome="bad",
        )

        # F (ваше замечание): закрыт, но причина не распознана конфигом ->
        # NULL, а не 0 — тоже без единого дня просрочки по графику.
        closed_unknown = await _contract(
            session, external_number="VIEW-CLOSED-UNKNOWN", issued_at=date(2026, 8, 15), closed_at=date(2026, 8, 25),
            status="closed", closure_reason="Расторгнут по суду", closure_outcome="unknown",
        )

        # D: по календарю окно уже прошло (issued 8 мес. назад), но данные есть только на 2 месяца вперёд -> НЕЗРЕЛЫЙ.
        stale = await _contract(session, external_number="VIEW-STALE", issued_at=date(2026, 1, 1))
        session.add(Payment(contract_id=stale.id, paid_at=date(2026, 2, 1), amount=Decimal("1000"), source="import", kind="regular"))

        await _payments_batch(session, data_as_of=date(2026, 3, 1), employee_id=employee_id, suffix="1")
        await session.commit()

    async with session_factory() as session:
        await recompute_all_contracts(session, upto_date=date(2026, 9, 1))
        await session.commit()

    async with session_factory() as session:
        for view in ("target_bad_60_6", "target_bad_90_12"):
            assert await _bad_and_mature(session, good.id, view) == (0, True), view
            assert await _bad_and_mature(session, bad.id, view) == (1, True), view
            assert await _bad_and_mature(session, closed_good.id, view) == (0, True), view
            assert await _bad_and_mature(session, closed_bad.id, view) == (1, True), view
            assert await _bad_and_mature(session, closed_unknown.id, view) == (None, False), view
            assert await _bad_and_mature(session, stale.id, view) == (None, False), view
