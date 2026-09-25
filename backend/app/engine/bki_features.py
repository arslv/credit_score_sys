"""Признаки БКИ (T11, ТЗ «Стадия Б» -> «После получения КИ»). Чистые функции:
BkiReport/словари на входе, без БД и сети — тот же принцип, что и
app/engine/rules.py. "Месяц" здесь — 30 дней (синтетические данные,
устойчивая арифметика важнее календарной точности)."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.sources.bki import BkiReport

_ACTIVE = "active"
_DAYS_PER_MONTH = 30
_OVERDUE_BUCKETS = (30, 60, 90)
_OVERDUE_WINDOWS_MONTHS = (6, 12, 24)
_INQUIRY_WINDOWS_MONTHS = (1, 3, 6)


def _decimal(value, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(default)


def _within_months(today: date, past: date, months: int) -> bool:
    delta_days = (today - past).days
    return 0 <= delta_days < months * _DAYS_PER_MONTH


def _months_since(today: date, past: date) -> int:
    if past > today:
        return 0
    return (today - past).days // _DAYS_PER_MONTH


def _contract_touches_window(contract, today: date, months: int) -> bool:
    if contract.opened_at > today:
        return False
    end = contract.closed_at or today
    window_start = today - timedelta(days=months * _DAYS_PER_MONTH)
    return end >= window_start


def parse_declared_income_and_obligations(income: dict, obligations: dict) -> tuple[Decimal, Decimal]:
    """income/obligations — секции анкеты стадии Б (ТЗ, «Доход»/«Обязательства»),
    введённые оператором со слов клиента, не проверенные."""
    monthly_income = _decimal(income.get("declared_monthly")) + _decimal(income.get("additional_monthly"))
    monthly_obligations = _decimal(obligations.get("monthly_payment_total"))
    return monthly_income, monthly_obligations


def compute_declared_pti_features(
    monthly_income: Decimal, monthly_obligations: Decimal, *, pti_max: float
) -> dict:
    """До БКИ: ПДН по заявленным клиентом данным. Дохода нет/он нулевой ->
    посчитать нельзя — informational (declared_pti=None), отказа отсюда быть
    не должно (stage_b_pre_bki: declared_pti_too_high сработает только на
    явном True, не на отсутствующем/null поле — см. evaluate_rule_list)."""
    if monthly_income <= 0:
        return {"declared_pti": None, "declared_pti_exceeds_max": False}

    declared_pti = float(monthly_obligations / monthly_income)
    return {"declared_pti": declared_pti, "declared_pti_exceeds_max": declared_pti > pti_max}


def compute_bki_features(
    report: BkiReport, *, monthly_income: Decimal, declared_monthly_obligations: Decimal, today: date
) -> dict:
    """После получения отчёта: фактический ПДН и сигналы под features.* (тот
    же неймспейс, что features.fssp_* из T09). score — bare top-level
    признак, задаётся отдельно в stage_b_pipeline (см. evaluate_financial)."""
    active_contracts = [c for c in report.contracts if c.status == _ACTIVE]
    factual_monthly_obligations = sum((_decimal(c.monthly_payment) for c in active_contracts), Decimal("0"))
    pti = float(factual_monthly_obligations / monthly_income) if monthly_income > 0 else None

    overdue_by_window = {
        f"bki_max_overdue_{months}mo": max(
            (c.overdue_days_max for c in report.contracts if _contract_touches_window(c, today, months)),
            default=0,
        )
        for months in _OVERDUE_WINDOWS_MONTHS
    }
    overdue_counts = {
        f"bki_overdue_{bucket}_plus_count": sum(1 for c in report.contracts if c.overdue_days_max >= bucket)
        for bucket in _OVERDUE_BUCKETS
    }
    inquiry_counts = {
        f"bki_inquiries_{months}mo": sum(
            1 for inquiry in report.inquiries if _within_months(today, inquiry.requested_at, months)
        )
        for months in _INQUIRY_WINDOWS_MONTHS
    }
    history_depth_months = (
        max(_months_since(today, c.opened_at) for c in report.contracts) if report.contracts else 0
    )

    return {
        "features": {
            "pti": pti,
            "bki_active_contracts_count": len(active_contracts),
            "bki_credit_history_depth_months": history_depth_months,
            # Расхождение заявленных и фактических обязательств (ТЗ) —
            # справочный признак для оператора/будущей модели, не участвует
            # ни в одном текущем правиле.
            "bki_obligations_discrepancy": float(declared_monthly_obligations - factual_monthly_obligations),
            **overdue_by_window,
            **overdue_counts,
            **inquiry_counts,
        }
    }
