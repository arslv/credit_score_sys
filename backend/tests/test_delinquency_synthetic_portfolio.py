"""T15, ваше явное указание: генератор синтетического портфеля, где для
каждого договора заранее известна правильная просрочка, и тест сверяет
каждое число — не один агрегат.

Часть 1 — чистый движок (app.engine.delinquency), сценарии как данные:
график + платежи + словарь {дата: ожидаемый (days_past_due, overdue_amount)}.
Часть 2 — через БД (app.engine.delinquency_recompute): полный пересчёт с нуля
должен давать то же самое, что накопленные "ежедневные" вызовы, и корректно
подхватывать исправление задним числом (ваше явное указание, п.6)."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.engine.delinquency import ScheduleItem, compute_delinquency
from app.engine.delinquency_recompute import recompute_contract
from app.models import Contract, DelinquencyDaily, Payment, PaymentSchedule
from sqlalchemy import select

_GRACE_DAYS = 5

_SCHEDULE = [
    ScheduleItem(1, date(2026, 1, 1), Decimal("1000")),
    ScheduleItem(2, date(2026, 2, 1), Decimal("1000")),
    ScheduleItem(3, date(2026, 3, 1), Decimal("1000")),
]


@dataclass(frozen=True)
class Scenario:
    name: str
    payments: list[tuple[date, Decimal]]  # уже подписанные: regular (+), reversal (-); penalty не передаётся
    checks: dict[date, tuple[int, Decimal]]  # as_of -> (days_past_due, overdue_amount)


_SCENARIOS = [
    Scenario(
        name="вовремя и полностью",
        payments=[
            (date(2026, 1, 1), Decimal("1000")),
            (date(2026, 2, 1), Decimal("1000")),
            (date(2026, 3, 1), Decimal("1000")),
        ],
        checks={
            date(2026, 1, 15): (0, Decimal("0")),
            date(2026, 3, 15): (0, Decimal("0")),
        },
    ),
    Scenario(
        name="пропущенный первый взнос",
        payments=[],
        checks={
            date(2026, 1, 3): (0, Decimal("0")),  # в пределах льготного срока
            date(2026, 1, 6): (0, Decimal("0")),  # ровно grace_days — ещё не просрочка
            date(2026, 1, 7): (1, Decimal("1000")),  # +1 день сверх льготного срока
            date(2026, 1, 20): (14, Decimal("1000")),  # период 2 (due 2026-02-01) ещё не наступил
        },
    ),
    Scenario(
        name="частичный платёж",
        payments=[(date(2026, 1, 1), Decimal("400"))],
        checks={
            date(2026, 1, 20): (14, Decimal("600")),  # остаток 600 по первому периоду
        },
    ),
    Scenario(
        name="переплата гасит следующий период (досрочное погашение)",
        payments=[(date(2026, 1, 1), Decimal("2500"))],
        checks={
            # период 1 (1000) и период 2 (1000) погашены, на период 3 ушло 500 —
            # на 2026-02-10 период 2 ещё не наступил, остаток периода 3 не просрочен (due 2026-03-01).
            date(2026, 2, 10): (0, Decimal("0")),
        },
    ),
    Scenario(
        name="сторно отменяет платёж — просрочка как будто платежа не было",
        payments=[(date(2026, 1, 1), Decimal("1000")), (date(2026, 1, 2), Decimal("-1000"))],
        checks={
            date(2026, 1, 20): (14, Decimal("1000")),
        },
    ),
]


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s.name)
def test_synthetic_scenarios_exact_numbers(scenario: Scenario):
    for as_of, (expected_dpd, expected_overdue) in scenario.checks.items():
        result = compute_delinquency(_SCHEDULE, scenario.payments, as_of, _GRACE_DAYS)
        assert (result.days_past_due, result.overdue_amount) == (expected_dpd, expected_overdue), (
            f"{scenario.name} @ {as_of}: получили {result}"
        )


async def _make_contract(session, *, external_number: str) -> Contract:
    contract = Contract(
        application_id=None, external_number=external_number, principal=Decimal("3000"), markup=Decimal("0"),
        total_amount=Decimal("3000"), down_payment=Decimal("0"), term_months=3, issued_at=date(2026, 1, 1),
        status="active",
    )
    session.add(contract)
    await session.flush()
    for item in _SCHEDULE:
        session.add(
            PaymentSchedule(
                contract_id=contract.id, period_no=item.period_no, due_date=item.due_date, due_amount=item.due_amount,
            )
        )
    return contract


async def _all_rows(session, contract_id) -> dict[date, tuple[int, Decimal]]:
    rows = (
        await session.execute(select(DelinquencyDaily).where(DelinquencyDaily.contract_id == contract_id))
    ).scalars().all()
    return {r.as_of: (r.days_past_due, r.overdue_amount) for r in rows}


async def test_full_recompute_matches_accumulated_daily_runs(session_factory):
    """Ваше явное указание (п.6): полный пересчёт с нуля даёт то же самое, что
    накопленные ежедневными задачами — т.е. вызов recompute_contract РОВНО
    один раз с конечной датой эквивалентен вызову его же каждый день от
    issued_at до этой даты (эмулируем "ежедневный тик")."""
    start, end = date(2026, 1, 1), date(2026, 4, 1)

    async with session_factory() as session:
        accumulated = await _make_contract(session, external_number="SYN-DAILY")
        session.add(Payment(contract_id=accumulated.id, paid_at=date(2026, 1, 15), amount=Decimal("500"), source="import", kind="regular"))
        await session.commit()
        accumulated_id = accumulated.id

    # "Ежедневный тик" — тот же вызов, что app/tasks/delinquency.py, каждый день отдельно.
    day = start
    async with session_factory() as session:
        while day <= end:
            await recompute_contract(session, await session.get(Contract, accumulated_id), upto_date=day, grace_days=_GRACE_DAYS)
            day += timedelta(days=1)
        await session.commit()

    async with session_factory() as session:
        accumulated_rows = await _all_rows(session, accumulated_id)

    async with session_factory() as session:
        full = await _make_contract(session, external_number="SYN-FULL")
        session.add(Payment(contract_id=full.id, paid_at=date(2026, 1, 15), amount=Decimal("500"), source="import", kind="regular"))
        await session.commit()
        full_id = full.id

    async with session_factory() as session:
        await recompute_contract(session, await session.get(Contract, full_id), upto_date=end, grace_days=_GRACE_DAYS)
        await session.commit()

    async with session_factory() as session:
        full_rows = await _all_rows(session, full_id)

    assert accumulated_rows == full_rows
    assert len(full_rows) == (end - start).days + 1


async def test_backdated_correction_updates_past_days_on_next_recompute(session_factory):
    """Ваше явное указание (п.6): исправление задним числом (платёж пришёл
    позже, с датой в прошлом) должно быть видно после следующего пересчёта —
    delinquency_daily перезаписывается (разрешено CLAUDE.md правилом 1, явное
    исключение для этой таблицы), сами payments при этом не трогаются
    (append-only — исправление добавляет новую строку, не меняет старую)."""
    async with session_factory() as session:
        contract = await _make_contract(session, external_number="SYN-CORRECTION")
        await session.commit()
        contract_id = contract.id

    # "Вчера" ничего не знали про платёж — просрочка видна.
    async with session_factory() as session:
        await recompute_contract(session, await session.get(Contract, contract_id), upto_date=date(2026, 1, 20), grace_days=_GRACE_DAYS)
        await session.commit()
    async with session_factory() as session:
        row = (
            await session.execute(
                select(DelinquencyDaily).where(
                    DelinquencyDaily.contract_id == contract_id, DelinquencyDaily.as_of == date(2026, 1, 20)
                )
            )
        ).scalar_one()
        assert row.days_past_due == 14

    # Сегодня пришла запись о платеже, фактически сделанном 2026-01-01 (backdated).
    async with session_factory() as session:
        session.add(Payment(contract_id=contract_id, paid_at=date(2026, 1, 1), amount=Decimal("1000"), source="import", kind="regular"))
        await session.commit()

    # Следующий пересчёт (та же ежедневная задача) должен ИСПРАВИТЬ прошлую строку.
    async with session_factory() as session:
        await recompute_contract(session, await session.get(Contract, contract_id), upto_date=date(2026, 1, 21), grace_days=_GRACE_DAYS)
        await session.commit()
    async with session_factory() as session:
        row = (
            await session.execute(
                select(DelinquencyDaily).where(
                    DelinquencyDaily.contract_id == contract_id, DelinquencyDaily.as_of == date(2026, 1, 20)
                )
            )
        ).scalar_one()
        assert row.days_past_due == 0  # исправлено задним числом
        assert row.overdue_amount == Decimal("0")

        payments = (
            await session.execute(select(Payment).where(Payment.contract_id == contract_id))
        ).scalars().all()
        assert len(payments) == 1  # сам платёж — одна строка, ничего не UPDATE'или
