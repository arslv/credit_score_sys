"""T12: договоры — выданное и платежи (ТЗ, «Договоры»). Данные сами по себе
не содержат ПД заявителя (суммы/сроки/статус), поэтому без маскирования —
привязка к заявке/клиенту, если нужна, идёт через отдельные эндпоинты."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
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
    # Для списка (по отзыву): сколько оплачено по графику и последняя
    # пересчитанная просрочка — видно без захода в договор.
    paid_amount: Decimal
    days_past_due: int | None
    overdue_amount: Decimal | None


class ContractsSummary(BaseModel):
    """Сколько договоров в каждом состоянии и сколько действующих в просрочке
    — для вкладок над списком."""

    by_status: dict[str, int]
    overdue: int


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
    paid_amount: Decimal
    schedule: list[ScheduleItemOut]
    payments: list[PaymentOut]
    current_delinquency: DelinquencyOut | None  # T15: последний пересчитанный снимок


def _paid_expr():
    """Оплачено по графику: обычные платежи минус сторно; неустойка не гасит
    график (как в расчёте просрочки, app/engine/delinquency.py)."""
    return func.coalesce(
        func.sum(
            case(
                (Payment.kind == "regular", Payment.amount),
                (Payment.kind == "reversal", -Payment.amount),
                else_=0,
            )
        ),
        0,
    )


def _latest_delinquency_subquery():
    ranked = select(
        DelinquencyDaily.contract_id,
        DelinquencyDaily.days_past_due,
        DelinquencyDaily.overdue_amount,
        func.row_number()
        .over(partition_by=DelinquencyDaily.contract_id, order_by=DelinquencyDaily.as_of.desc())
        .label("rn"),
    ).subquery()
    return select(ranked).where(ranked.c.rn == 1).subquery()


@router.get("", response_model=Page[ContractListItem])
async def list_contracts(
    params: PageParams = Depends(),
    status_filter: str | None = None,
    overdue: bool = False,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[ContractListItem]:
    stmt = select(Contract)
    if status_filter:
        stmt = stmt.where(Contract.status == status_filter)
    if overdue:
        # Действующие с просрочкой по последнему пересчёту — как в /summary.
        latest = _latest_delinquency_subquery()
        stmt = stmt.join(latest, latest.c.contract_id == Contract.id).where(
            Contract.status == "active", latest.c.days_past_due > 0
        )
    total = len((await session.execute(stmt)).scalars().all())
    contracts = (
        await session.execute(stmt.order_by(Contract.issued_at.desc()).offset(params.offset).limit(params.page_size))
    ).scalars().all()

    ids = [c.id for c in contracts]
    paid: dict[UUID, Decimal] = {}
    delinquency: dict[UUID, tuple[int, Decimal]] = {}
    if ids:
        paid = dict(
            (
                await session.execute(
                    select(Payment.contract_id, _paid_expr()).where(Payment.contract_id.in_(ids)).group_by(Payment.contract_id)
                )
            ).all()
        )
        latest = _latest_delinquency_subquery()
        delinquency = {
            row.contract_id: (row.days_past_due, row.overdue_amount)
            for row in (await session.execute(select(latest).where(latest.c.contract_id.in_(ids)))).all()
        }

    return Page(
        items=[
            ContractListItem(
                id=c.id, application_id=c.application_id, external_number=c.external_number,
                total_amount=c.total_amount, term_months=c.term_months, status=c.status, issued_at=c.issued_at,
                paid_amount=paid.get(c.id, Decimal("0")),
                days_past_due=delinquency[c.id][0] if c.id in delinquency else None,
                overdue_amount=delinquency[c.id][1] if c.id in delinquency else None,
            )
            for c in contracts
        ],
        total=total, page=params.page, page_size=params.page_size,
    )


@router.get("/summary", response_model=ContractsSummary)
async def contracts_summary(
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ContractsSummary:
    by_status = dict((await session.execute(select(Contract.status, func.count()).group_by(Contract.status))).all())
    latest = _latest_delinquency_subquery()
    overdue = (
        await session.execute(
            select(func.count())
            .select_from(Contract)
            .join(latest, latest.c.contract_id == Contract.id)
            .where(Contract.status == "active", latest.c.days_past_due > 0)
        )
    ).scalar_one()
    return ContractsSummary(by_status=by_status, overdue=overdue)


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
    paid_amount = (
        await session.execute(select(_paid_expr()).where(Payment.contract_id == contract_id))
    ).scalar_one()
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
        paid_amount=paid_amount,
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
