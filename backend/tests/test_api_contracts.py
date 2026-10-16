"""T15: карточка договора — тип платежа и текущая просрочка."""

import uuid
from datetime import date
from decimal import Decimal

from app.engine.delinquency_recompute import recompute_all_contracts
from app.models import Contract, Payment, PaymentSchedule
from tests._admin_helpers import create_employee, login


async def test_contract_detail_shows_payment_kind_and_delinquency(admin_client, session_factory):
    await create_employee(session_factory, email="rm-contract@example.com", role="admin")
    await login(admin_client, "rm-contract@example.com")

    async with session_factory() as session:
        contract = Contract(
            application_id=None, external_number=f"C-{uuid.uuid4().hex[:8]}", principal=Decimal("2000"),
            markup=Decimal("0"), total_amount=Decimal("2000"), down_payment=Decimal("0"), term_months=2,
            issued_at=date(2026, 1, 1), status="active",
        )
        session.add(contract)
        await session.flush()
        session.add(PaymentSchedule(contract_id=contract.id, period_no=1, due_date=date(2026, 2, 1), due_amount=Decimal("1000")))
        session.add(PaymentSchedule(contract_id=contract.id, period_no=2, due_date=date(2026, 3, 1), due_amount=Decimal("1000")))
        session.add(Payment(contract_id=contract.id, paid_at=date(2026, 2, 1), amount=Decimal("1000"), source="import", kind="regular"))
        session.add(Payment(contract_id=contract.id, paid_at=date(2026, 2, 5), amount=Decimal("50"), source="import", kind="penalty"))
        await session.commit()
        contract_id = contract.id

    async with session_factory() as session:
        await recompute_all_contracts(session, upto_date=date(2026, 3, 20))
        await session.commit()

    response = await admin_client.get(f"/api/contracts/{contract_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["application_id"] is None
    kinds = {p["kind"] for p in body["payments"]}
    assert kinds == {"regular", "penalty"}

    assert body["current_delinquency"] is not None
    assert body["current_delinquency"]["days_past_due"] == 14  # (2026-03-20 - 2026-03-01) - grace_days(5)
    assert body["current_delinquency"]["overdue_amount"] == "1000.00"
