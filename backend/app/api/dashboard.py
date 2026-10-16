"""T13c/T15: дашборд — блоки на уже существующих данных (ТЗ, «Дашборд»).
Винтажный анализ и просрочка (T13c откладывал их до T15, импорта платежей) —
теперь считаются из contracts/delinquency_daily/target_bad_60_6 (T15). Все
остальные числа — SQL в бэкенде, не браузер (ваше явное указание) — эндпоинт
отдаёт только готовые числа.

Недели и месяцы — по московскому времени, не UTC (ваше уточнение): заявка,
поданная в 23:30 по Москве 4 января, это уже 5 января по Москве, но ещё
4 января по UTC — при группировке/фильтрации по UTC она попала бы не в тот
день/неделю/месяц. Поэтому фильтр периода и группировка по неделям/месяцам
считаются от `payload AT TIME ZONE 'Europe/Moscow'`, а не от сырого UTC-поля.

Роли (T23): admin и viewer (раньше risk_manager и viewer) — только агрегаты, без персданных (ваше
уточнение); дашборд физически не возвращает ни одного поля с ФИО/паспортом/
телефоном, поэтому viewer здесь ничем не отличается от risk_manager."""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pydantic import ValidationError
from sqlalchemy import and_, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine.versions import get_active_ruleset_version, load_config
from app.models import (
    Application, BkiReport, Check, Decision, Employee, ManualOverride, StatusHistory,
)
from app.security.deps import require_roles

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_VIEW_ROLES = ("admin", "viewer")
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


# T17, часть 5 (ваше указание): воронка — КОНКРЕТНАЯ последовательность
# ТЗ/CLAUDE.md «Как устроен поток», а не произвольные статусы. «Прошли
# стадию А» намеренно нет — на стадии А одобрения не бывает, только отказ или
# передача оператору. Каждый шаг — отдельный EXISTS (заявка когда-либо дошла
# до шага), одним запросом с FILTER — не N+1 по заявкам.
class FunnelStepItem(BaseModel):
    step: str  # submitted | to_operator | stage_b | approved
    count: int


class ScoreBucketItem(BaseModel):
    bucket_start: int
    count: int


class ScoreThresholds(BaseModel):
    auto_approve_above: float | None
    auto_reject_below: float | None


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


class VintageItem(BaseModel):
    # Месяц выдачи (contracts.issued_at) по календарю — не timestamptz, "Москва
    # vs UTC" здесь не стоит: issued_at уже обычная дата, не момент времени.
    cohort_month: str  # "YYYY-MM"
    total_contracts: int
    mature_contracts: int  # ТЗ, T15 п.5: таргет известен только для зрелых
    bad_contracts: int | None  # None — ни один договор когорты ещё не зрел
    bad_rate: float | None


class DelinquencyBucketItem(BaseModel):
    bucket: str  # "0" | "1-29" | "30-59" | "60-89" | "90+"
    contracts: int


class DashboardSummary(BaseModel):
    period_from: date | None
    period_to: date | None
    funnel: list[FunnelStepItem]
    score_distribution: list[ScoreBucketItem]
    score_thresholds: ScoreThresholds | None
    source_unavailable_warn_threshold: float
    weekly_approval: list[WeeklyApprovalItem]
    rejection_reasons: list[RejectionReasonItem]
    overrides: OverridesSummary
    bki_monthly: list[BkiMonthlyItem]
    source_availability: list[SourceAvailabilityItem]
    vintage: list[VintageItem]
    delinquency_buckets: list[DelinquencyBucketItem]


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    period_from: date | None = None,
    period_to: date | None = None,
    _employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    # --- воронка: поступило → передано оператору → рассмотрено (стадия Б) →
    # одобрено. Один запрос, без цикла по заявкам (см. докстринг выше).
    reached_to_operator = exists(
        select(StatusHistory.id).where(
            StatusHistory.application_id == Application.id, StatusHistory.to_status == "to_operator",
        )
    )
    reached_stage_b = exists(
        select(Decision.id).where(Decision.application_id == Application.id, Decision.stage == "B")
    )
    reached_approved = exists(
        select(Decision.id).where(Decision.application_id == Application.id, Decision.outcome == "approve")
    )
    funnel_stmt = select(
        func.count().label("submitted"),
        func.count().filter(reached_to_operator).label("to_operator"),
        func.count().filter(reached_stage_b).label("stage_b"),
        func.count().filter(reached_approved).label("approved"),
    ).select_from(Application)
    for clause in _period_filter(Application.submitted_at, period_from, period_to):
        funnel_stmt = funnel_stmt.where(clause)
    funnel_row = (await session.execute(funnel_stmt)).one()
    funnel = [
        FunnelStepItem(step="submitted", count=funnel_row.submitted),
        FunnelStepItem(step="to_operator", count=funnel_row.to_operator),
        FunnelStepItem(step="stage_b", count=funnel_row.stage_b),
        FunnelStepItem(step="approved", count=funnel_row.approved),
    ]

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
    # Раньше — Python-цикл по application_ids (2 запроса на заявку, тот же
    # N+1, что был в очереди списка заявок, см. app/api/applications.py).
    # Теперь — DISTINCT ON (последний override/решение на заявку) и один join.
    latest_decision_sq = (
        select(Decision.application_id, Decision.outcome)
        .distinct(Decision.application_id)
        .order_by(Decision.application_id, Decision.created_at.desc())
        .subquery()
    )
    latest_override_sq = (
        select(ManualOverride.application_id, ManualOverride.to_outcome)
        .distinct(ManualOverride.application_id)
        .order_by(ManualOverride.application_id, ManualOverride.created_at.desc())
        .subquery()
    )
    overrides_stmt = (
        select(
            func.count().label("total"),
            func.count(latest_override_sq.c.application_id).label("overridden"),
            func.count().filter(
                and_(
                    latest_override_sq.c.application_id.is_not(None),
                    latest_override_sq.c.to_outcome != latest_decision_sq.c.outcome,
                )
            ).label("discrepancies"),
        )
        .select_from(Application)
        .outerjoin(latest_override_sq, latest_override_sq.c.application_id == Application.id)
        .outerjoin(latest_decision_sq, latest_decision_sq.c.application_id == Application.id)
    )
    for clause in _period_filter(Application.submitted_at, period_from, period_to):
        overrides_stmt = overrides_stmt.where(clause)
    overrides_row = (await session.execute(overrides_stmt)).one()
    overrides = OverridesSummary(
        applications_total=overrides_row.total,
        overridden=overrides_row.overridden,
        discrepancies=overrides_row.discrepancies,
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
    # МВД не в конвейере (T17, шаг 3) — исключаем в самом запросе, не на фронте.
    source_stmt = (
        select(
            Check.source,
            func.count().filter(Check.status == "source_unavailable").label("unavailable"),
            func.count().label("total"),
        )
        .where(Check.source != "mvd")
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

    # --- винтажный анализ: когорта по месяцу выдачи, таргет bad_60_6 (T15) ----
    # Период (period_from/period_to) здесь намеренно не фильтрует — винтаж
    # смотрят по всему портфелю, не по "заявкам за период"; contracts.issued_at
    # обычная дата, без UTC/МСК неоднозначности (ваше уточнение — конвертация
    # нужна только для timestamptz-полей, не для date).
    # --- распределение балла БКИ (стадия Б) с действующими порогами -----------
    score_bucket = (func.floor(Decision.score / 50) * 50).label("bucket_start")
    score_stmt = (
        select(score_bucket, func.count())
        .where(Decision.stage == "B", Decision.score.is_not(None))
        .group_by(score_bucket)
        .order_by(score_bucket)
    )
    for clause in _period_filter(Decision.created_at, period_from, period_to):
        score_stmt = score_stmt.where(clause)
    score_rows = (await session.execute(score_stmt)).all()
    score_distribution = [
        ScoreBucketItem(bucket_start=int(bucket), count=count) for bucket, count in score_rows
    ]

    # Пороги — из ДЕЙСТВУЮЩЕЙ версии правил (без учёта матрицы по сумме: для
    # аналитики по всему портфелю это общий ориентир, не порог решения
    # конкретной заявки — тот в карточке берётся из версии РЕШЕНИЯ, см.
    # app/api/applications.py). Битый конфиг — не роняем дашборд целиком.
    score_thresholds = None
    # Дефолт схемы (0.05) — на случай битого/отсутствующего конфига: страница
    # не должна зависеть от того, что именно случилось с версией правил.
    source_unavailable_warn_threshold = 0.05
    ruleset_row = await get_active_ruleset_version(session)
    if ruleset_row is not None:
        try:
            config = load_config(ruleset_row)
        except ValidationError:
            config = None
        if config is not None:
            score_thresholds = ScoreThresholds(
                auto_approve_above=config.score_thresholds.auto_approve_above,
                auto_reject_below=config.score_thresholds.auto_reject_below,
            )
            source_unavailable_warn_threshold = config.dashboard.source_unavailable_warn_threshold

    vintage_rows = (
        await session.execute(
            text(
                """
                SELECT
                    to_char(date_trunc('month', c.issued_at), 'YYYY-MM') AS cohort_month,
                    count(*) AS total_contracts,
                    count(*) FILTER (WHERE t.is_mature) AS mature_contracts,
                    count(*) FILTER (WHERE t.bad = 1) AS bad_contracts
                FROM contracts c
                JOIN target_bad_60_6 t ON t.contract_id = c.id
                GROUP BY date_trunc('month', c.issued_at)
                ORDER BY date_trunc('month', c.issued_at)
                """
            )
        )
    ).all()
    vintage = [
        VintageItem(
            cohort_month=row.cohort_month, total_contracts=row.total_contracts,
            mature_contracts=row.mature_contracts,
            bad_contracts=row.bad_contracts if row.mature_contracts else None,
            bad_rate=(row.bad_contracts / row.mature_contracts) if row.mature_contracts else None,
        )
        for row in vintage_rows
    ]

    # --- просрочка сейчас: распределение договоров по дням просрочки (T15) ----
    bucket_rows = (
        await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (contract_id) contract_id, days_past_due
                    FROM delinquency_daily
                    ORDER BY contract_id, as_of DESC
                )
                SELECT
                    CASE
                        WHEN days_past_due = 0 THEN '0'
                        WHEN days_past_due BETWEEN 1 AND 29 THEN '1-29'
                        WHEN days_past_due BETWEEN 30 AND 59 THEN '30-59'
                        WHEN days_past_due BETWEEN 60 AND 89 THEN '60-89'
                        ELSE '90+'
                    END AS bucket,
                    count(*) AS contracts
                FROM latest
                GROUP BY 1
                """
            )
        )
    ).all()
    delinquency_buckets = [
        DelinquencyBucketItem(bucket=row.bucket, contracts=row.contracts) for row in bucket_rows
    ]

    return DashboardSummary(
        period_from=period_from, period_to=period_to,
        funnel=funnel, weekly_approval=weekly_approval, rejection_reasons=rejection_reasons,
        overrides=overrides, bki_monthly=bki_monthly, source_availability=source_availability,
        vintage=vintage, delinquency_buckets=delinquency_buckets,
        score_distribution=score_distribution, score_thresholds=score_thresholds,
        source_unavailable_warn_threshold=source_unavailable_warn_threshold,
    )
