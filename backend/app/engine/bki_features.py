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


INSTALLMENT_SOURCE_PARTNER = "partner"
INSTALLMENT_SOURCE_TARIFF = "tariff"


def compute_installment_payment(
    *, partner_monthly_payment, goods_price: Decimal, down_payment: Decimal, term_months: int,
    tariff: str, tariffs: dict,
) -> tuple[Decimal | None, str | None]:
    """T21: ежемесячный платёж по НАШЕЙ рассрочке — для ПДН (ваше решение):
    1) deal.monthly_payment из /review — их система считает график с
       наценкой, это точная цифра;
    2) иначе — по тарифу из правил: (цена − взнос) × (1 + markup_pct) / срок;
    3) нет ни того, ни другого (тариф неизвестен или без markup_pct, срок 0)
       -> (None, None): ПДН «не вычислимо», а не занижен.
    Возвращает (платёж, источник: "partner" | "tariff" | None)."""
    partner = _decimal(partner_monthly_payment, default="-1")
    if partner > 0:
        return partner.quantize(Decimal("0.01")), INSTALLMENT_SOURCE_PARTNER

    tariff_config = tariffs.get(tariff)
    markup = getattr(tariff_config, "markup_pct", None)
    if markup is None or term_months <= 0 or goods_price <= down_payment:
        return None, None
    payment = (goods_price - down_payment) * (Decimal("1") + Decimal(str(markup))) / Decimal(term_months)
    return payment.quantize(Decimal("0.01")), INSTALLMENT_SOURCE_TARIFF


def add_installment_to_legacy_stage_b_features(
    features_b: dict, *, installment_payment: Decimal | None, installment_source: str | None,
    monthly_income: Decimal, pti_max: float,
) -> dict:
    """T21, симуляция «проверить на истории»: снимок стадии Б до T21 (ПДН без
    платежа по рассрочке) -> те же признаки, как их посчитала бы формула T21.
    Обе доли ПДН до T21 были «обязательства / доход», поэтому добавить к ним
    «платёж по рассрочке / доход» — ровно новая формула, без похода в БКИ.
    Снимок уже по новой формуле (есть installment_payment_source) или платёж
    не определить (версия правил без наценок и без поля от заказчика) —
    признаки как есть: так их и посчитал бы код с этой версией правил."""
    if "installment_payment_source" in features_b or installment_payment is None or monthly_income <= 0:
        return features_b
    share = float(installment_payment / monthly_income)
    upgraded = {
        **features_b,
        "installment_monthly_payment": float(installment_payment),
        "installment_payment_source": installment_source,
    }
    if features_b.get("declared_pti") is not None:
        upgraded["declared_pti"] = features_b["declared_pti"] + share
        upgraded["declared_pti_exceeds_max"] = upgraded["declared_pti"] > pti_max
    nested = features_b.get("features") or {}
    if nested.get("pti") is not None:
        upgraded["features"] = {**nested, "pti": nested["pti"] + share}
    return upgraded


def compute_declared_pti_features(
    monthly_income: Decimal, monthly_obligations: Decimal, installment_payment: Decimal | None,
    installment_source: str | None, *, pti_max: float,
) -> dict:
    """До БКИ: ПДН по заявленным клиентом данным + платёж по новой рассрочке
    (T21). Дохода нет/он нулевой или платёж по рассрочке не определить ->
    посчитать нельзя — informational (declared_pti=None), отказа отсюда быть
    не должно (stage_b_pre_bki: declared_pti_too_high сработает только на
    явном True, не на отсутствующем/null поле — см. evaluate_rule_list).

    installment_* — сверху снимка, чтобы карточка показала «с учётом платежа
    по рассрочке N ₽» и откуда он (float — признак для сравнения, правило 5;
    точная сумма — в снимке анкеты / в расчёте выше)."""
    installment = {
        "installment_monthly_payment": float(installment_payment) if installment_payment is not None else None,
        "installment_payment_source": installment_source,
    }
    # Почему ПДН (и заявленный, и фактический — у них один знаменатель и один
    # платёж по рассрочке) посчитать нельзя: для карточки «не вычислимо (…)».
    if monthly_income <= 0:
        return {"declared_pti": None, "declared_pti_exceeds_max": False, "pti_unavailable_reason": "no_income", **installment}
    if installment_payment is None:
        return {
            "declared_pti": None, "declared_pti_exceeds_max": False, "pti_unavailable_reason": "no_installment",
            **installment,
        }

    declared_pti = float((monthly_obligations + installment_payment) / monthly_income)
    return {"declared_pti": declared_pti, "declared_pti_exceeds_max": declared_pti > pti_max, **installment}


def compute_bki_features(
    report: BkiReport, *, monthly_income: Decimal, declared_monthly_obligations: Decimal,
    installment_payment: Decimal | None, today: date,
) -> dict:
    """После получения отчёта: фактический ПДН и сигналы под features.* (тот
    же неймспейс, что features.fssp_* из T09). score — bare top-level
    признак, задаётся отдельно в stage_b_pipeline (см. evaluate_financial).

    T21: ПДН = (платежи по действующим кредитам из КИ + платёж по нашей
    рассрочке) / доход. Без платежа по рассрочке — None, не заниженная цифра."""
    active_contracts = [c for c in report.contracts if c.status == _ACTIVE]
    factual_monthly_obligations = sum((_decimal(c.monthly_payment) for c in active_contracts), Decimal("0"))
    pti = (
        float((factual_monthly_obligations + installment_payment) / monthly_income)
        if monthly_income > 0 and installment_payment is not None
        else None
    )

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
