"""T13c: дашборд — блоки на уже существующих данных (ТЗ, «Дашборд»; винтажный
анализ и просрочка — заглушка до T15, импорта платежей, ваше уточнение).
Агрегаты считает бэкенд в SQL, не браузер (ваше явное указание) — эндпоинт
отдаёт только готовые числа.

Недели и месяцы — по московскому времени, не UTC (ваше уточнение): заявка,
поданная в 23:30 по Москве 4 января, это уже 5 января по Москве, но ещё
4 января по UTC — при группировке/фильтрации по UTC она попала бы не в тот
день/неделю/месяц. Поэтому фильтр периода и группировка по неделям/месяцам
считаются от `payload AT TIME ZONE 'Europe/Moscow'`, а не от сырого UTC-поля.

Роли: risk_manager и viewer — только агрегаты, без персданных (ваше
уточнение); дашборд физически не возвращает ни одного поля с ФИО/паспортом/
телефоном, поэтому viewer здесь ничем не отличается от risk_manager."""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Application, BkiReport, Check, Decision, Employee, ManualOverride
from app.security.deps import require_roles

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_VIEW_ROLES = ("risk_manager", "viewer")
_MSK_TZ = "Europe/Moscow"


def _msk(column: Any) -> Any:
    """Колонка (timestamptz, UTC на диске) как naive-время по Москве —
    для группировки/сравнения с period_from/period_to (см. докстринг модуля)."""
    return func.timezone(_MSK_TZ, column)


def _period_filter(column: Any, period_from: date | None, period_to: date | None) -> list[Any]:
    clauses = []
    if period_from is not None:
        clauses.append(_msk(column) >= period_from)
    if period_to is not None:
        clauses.append(_msk(column) < period_to)
    return clauses


class FunnelItem(BaseModel):
    status: str
    count: int


class WeeklyApprovalItem(BaseModel):
    # Понедельник ISO-недели по Москве (date.fromisoformat подходящий формат).
    week_start: date
    approved: int
    total: int


class RejectionReasonItem(BaseModel):
    reason_code: str
    count: int


class OverridesSummary(BaseModel):
    applications_total: int
    overridden: int
    discrepancies: int


class BkiMonthlyItem(BaseModel):
    month: str  # "YYYY-MM" по Москве
    attempts: int
    successful: int


class SourceAvailabilityItem(BaseModel):
    source: str
    unavailable: int
    total: int


class DashboardSummary(BaseModel):
    period_from: date | None
    period_to: date | None
    funnel: list[FunnelItem]
    weekly_approval: list[WeeklyApprovalItem]
    rejection_reasons: list[RejectionReasonItem]
    overrides: OverridesSummary
    bki_monthly: list[BkiMonthlyItem]
    source_availability: list[SourceAvailabilityItem]


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    period_from: date | None = None,
    period_to: date | None = None,
    _employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    # --- воронка по состояниям (Application.submitted_at) ---------------------
    funnel_stmt = select(Application.status, func.count()).group_by(Application.status)
    for clause in _period_filter(Application.submitted_at, period_from, period_to):
        funnel_stmt = funnel_stmt.where(clause)
    funnel_rows = (await session.execute(funnel_stmt)).all()
    funnel = [FunnelItem(status=status, count=count) for status, count in funnel_rows]

    # --- доля одобрений по неделям (Decision.stage == "B", created_at) --------
    week_col = func.date_trunc("week", _msk(Decision.created_at)).label("week_start")
    weekly_stmt = (
        select(
            week_col,
            func.count().filter(Decision.outcome == "approve").label("approved"),
            func.count().label("total"),
        )
        .where(Decision.stage == "B")
        .group_by(week_col)
        .order_by(week_col)
    )
    for clause in _period_filter(Decision.created_at, period_from, period_to):
        weekly_stmt = weekly_stmt.where(clause)
    weekly_rows = (await session.execute(weekly_stmt)).all()
    weekly_approval = [
        WeeklyApprovalItem(week_start=week_start.date(), approved=approved, total=total)
        for week_start, approved, total in weekly_rows
    ]

    # --- причины отказов по частоте (Decision.outcome == "reject") ------------
    reasons_stmt = (
        select(Decision.reason_code, func.count())
        .where(Decision.outcome == "reject", Decision.reason_code.is_not(None))
        .group_by(Decision.reason_code)
        .order_by(func.count().desc())
    )
    for clause in _period_filter(Decision.created_at, period_from, period_to):
        reasons_stmt = reasons_stmt.where(clause)
    reason_rows = (await session.execute(reasons_stmt)).all()
    rejection_reasons = [
        RejectionReasonItem(reason_code=reason_code, count=count) for reason_code, count in reason_rows
    ]

    # --- доля ручных переопределений и расхождений с оператором ---------------
    apps_stmt = select(Application.id)
    for clause in _period_filter(Application.submitted_at, period_from, period_to):
        apps_stmt = apps_stmt.where(clause)
    application_ids = (await session.execute(apps_stmt)).scalars().all()

    overridden = 0
    discrepancies = 0
    for application_id in application_ids:
        override = (
            await session.execute(
                select(ManualOverride)
                .where(ManualOverride.application_id == application_id)
                .order_by(ManualOverride.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if override is None:
            continue
        overridden += 1

        latest_decision = (
            await session.execute(
                select(Decision)
                .where(Decision.application_id == application_id)
                .order_by(Decision.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if latest_decision is not None and override.to_outcome != latest_decision.outcome:
            discrepancies += 1

    overrides = OverridesSummary(
        applications_total=len(application_ids), overridden=overridden, discrepancies=discrepancies,
    )

    # --- БКИ по месяцам: платные попытки и успешные отчёты отдельно -----------
    month_col = func.date_trunc("month", _msk(BkiReport.requested_at)).label("month")
    bki_stmt = (
        select(
            month_col,
            func.count().label("attempts"),
            func.count().filter(BkiReport.status == "ok").label("successful"),
        )
        .group_by(month_col)
        .order_by(month_col)
    )
    for clause in _period_filter(BkiReport.requested_at, period_from, period_to):
        bki_stmt = bki_stmt.where(clause)
    bki_rows = (await session.execute(bki_stmt)).all()
    bki_monthly = [
        BkiMonthlyItem(month=month.strftime("%Y-%m"), attempts=attempts, successful=successful)
        for month, attempts, successful in bki_rows
    ]

    # --- доля source_unavailable по источнику (Check.started_at) --------------
    source_stmt = (
        select(
            Check.source,
            func.count().filter(Check.status == "source_unavailable").label("unavailable"),
            func.count().label("total"),
        )
        .group_by(Check.source)
        .order_by(Check.source)
    )
    for clause in _period_filter(Check.started_at, period_from, period_to):
        source_stmt = source_stmt.where(clause)
    source_rows = (await session.execute(source_stmt)).all()
    source_availability = [
        SourceAvailabilityItem(source=source, unavailable=unavailable, total=total)
        for source, unavailable, total in source_rows
    ]

    return DashboardSummary(
        period_from=period_from, period_to=period_to,
        funnel=funnel, weekly_approval=weekly_approval, rejection_reasons=rejection_reasons,
        overrides=overrides, bki_monthly=bki_monthly, source_availability=source_availability,
    )
