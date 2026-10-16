"""T12: договоры — выданное и платежи (ТЗ, «Договоры»). Данные сами по себе
не содержат ПД заявителя (суммы/сроки/статус), поэтому без маскирования —
привязка к заявке/клиенту, если нужна, идёт через отдельные эндпоинты."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Contract, DelinquencyDaily, Employee, Payment, PaymentSchedule
from app.security.deps import require_roles
from app.security.pagination import Page, PageParams

router = APIRouter(prefix="/api/contracts", tags=["contracts"])

_VIEW_ROLES = ("admin", "operator", "viewer")  # T23: operator — бывшие права compliance


class ContractListItem(BaseModel):
    id: UUID
    # T15: nullable — договор из импорта не всегда привязан к заявке
    # (app.engine.imports, ContractLinkCandidate — привязку подтверждает
    # человек, автоматически только по однозначному идентификатору).
    application_id: UUID | None
    external_number: str | None
    total_amount: Decimal
    term_months: int
    status: str
    issued_at: date


class PaymentOut(BaseModel):
    id: UUID
    paid_at: date
    amount: Decimal
    source: str
    kind: str  # T15: regular | penalty | reversal


class ScheduleItemOut(BaseModel):
    period_no: int
    due_date: date
    due_amount: Decimal


class DelinquencyOut(BaseModel):
    as_of: date
    days_past_due: int
    overdue_amount: Decimal


class ContractDetail(BaseModel):
    id: UUID
    application_id: UUID | None
    external_number: str | None
    principal: Decimal
    markup: Decimal
    total_amount: Decimal
    down_payment: Decimal
    term_months: int
    status: str
    issued_at: date
    closed_at: date | None
    # T15, ваше замечание: закрытие — не всегда погашение; причина и её
    # трактовка (config/import_mappings/contracts.yaml::closure_outcome_map)
    # видны на карточке, не только внутренний таргет.
    closure_reason: str | None
    closure_outcome: str | None
    # T15, ваше замечание: график — импортом, генерация только запасной
    # вариант; видно на карточке, каким графиком реально пользуется расчёт.
    schedule_source: str
    schedule: list[ScheduleItemOut]
    payments: list[PaymentOut]
    current_delinquency: DelinquencyOut | None  # T15: последний пересчитанный снимок


@router.get("", response_model=Page[ContractListItem])
async def list_contracts(
    params: PageParams = Depends(),
    status_filter: str | None = None,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[ContractListItem]:
    stmt = select(Contract)
    if status_filter:
        stmt = stmt.where(Contract.status == status_filter)
    total = len((await session.execute(stmt)).scalars().all())
    contracts = (
        await session.execute(stmt.order_by(Contract.issued_at.desc()).offset(params.offset).limit(params.page_size))
    ).scalars().all()

    return Page(
        items=[
            ContractListItem(
                id=c.id, application_id=c.application_id, external_number=c.external_number,
                total_amount=c.total_amount, term_months=c.term_months, status=c.status, issued_at=c.issued_at,
            )
            for c in contracts
        ],
        total=total, page=params.page, page_size=params.page_size,
    )


@router.get("/{contract_id}", response_model=ContractDetail)
async def get_contract(
    contract_id: UUID,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ContractDetail:
    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="договор не найден")

    schedule = (
        await session.execute(
            select(PaymentSchedule).where(PaymentSchedule.contract_id == contract_id).order_by(PaymentSchedule.period_no)
        )
    ).scalars().all()
    payments = (
        await session.execute(
            select(Payment).where(Payment.contract_id == contract_id).order_by(Payment.paid_at)
        )
    ).scalars().all()
    latest_delinquency = (
        await session.execute(
            select(DelinquencyDaily)
            .where(DelinquencyDaily.contract_id == contract_id)
            .order_by(DelinquencyDaily.as_of.desc())
            .limit(1)
        )
    ).scalars().first()

    return ContractDetail(
        id=contract.id, application_id=contract.application_id, external_number=contract.external_number,
        principal=contract.principal, markup=contract.markup, total_amount=contract.total_amount,
        down_payment=contract.down_payment, term_months=contract.term_months, status=contract.status,
        issued_at=contract.issued_at, closed_at=contract.closed_at,
        closure_reason=contract.closure_reason, closure_outcome=contract.closure_outcome,
        schedule_source=contract.schedule_source,
        current_delinquency=(
            DelinquencyOut(
                as_of=latest_delinquency.as_of, days_past_due=latest_delinquency.days_past_due,
                overdue_amount=latest_delinquency.overdue_amount,
            )
            if latest_delinquency is not None else None
        ),
        schedule=[
            ScheduleItemOut(period_no=s.period_no, due_date=s.due_date, due_amount=s.due_amount) for s in schedule
        ],
        payments=[
            PaymentOut(id=p.id, paid_at=p.paid_at, amount=p.amount, source=p.source, kind=p.kind) for p in payments
        ],
    )
