"""T15: ежедневная задача пересчёта delinquency_daily."""

from datetime import date
from decimal import Decimal

import app.tasks.delinquency as delinquency_task_module
from app.models import Contract, DelinquencyDaily, Payment, PaymentSchedule
from sqlalchemy import select


class _FakeEngine:
    async def dispose(self) -> None:
        pass


async def test_recompute_daily_task_writes_delinquency_rows(session_factory, monkeypatch):
    monkeypatch.setattr(delinquency_task_module, "new_sessionmaker", lambda: (_FakeEngine(), session_factory))

    async with session_factory() as session:
        contract = Contract(
            application_id=None, external_number="TASK-1", principal=Decimal("2000"), markup=Decimal("0"),
            total_amount=Decimal("2000"), down_payment=Decimal("0"), term_months=2,
            issued_at=date(2026, 1, 1), status="active",
        )
        session.add(contract)
        await session.flush()
        session.add(PaymentSchedule(contract_id=contract.id, period_no=1, due_date=date(2026, 2, 1), due_amount=Decimal("1000")))
        session.add(PaymentSchedule(contract_id=contract.id, period_no=2, due_date=date(2026, 3, 1), due_amount=Decimal("1000")))
        await session.commit()
        contract_id = contract.id

    summary = await delinquency_task_module._run()
    assert summary["contracts"] >= 1

    async with session_factory() as session:
        rows = (
            await session.execute(select(DelinquencyDaily).where(DelinquencyDaily.contract_id == contract_id))
        ).scalars().all()
        assert len(rows) > 0  # хотя бы issued_at..сегодня пересчитано
