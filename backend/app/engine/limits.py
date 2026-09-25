"""Лимиты сделки и альтернативный тариф (T10, ТЗ «Тарифы» + «Лимиты сделки»).
Чистая функция: не отказ, если выбранный тариф не проходит, а проходит
другой — предложение alternative-тарифа с нужным первым взносом. Только по
лимитам (сумма/срок), без учёта риска — риск-логика добавится в T11."""

from dataclasses import dataclass
from decimal import Decimal

from app.engine.schema import LimitsConfig, TariffConfig


@dataclass(frozen=True)
class DealLimitsOutcome:
    passed: bool
    tariff: str | None = None
    required_down_pay: Decimal | None = None
    is_alternative: bool = False
    reason_code: str | None = None


def _meets_down_payment(down_payment: Decimal, goods_price: Decimal, min_pct: float) -> bool:
    if goods_price <= 0:
        return False
    return down_payment >= goods_price * Decimal(str(min_pct))


def _tariff_fits(
    *,
    tariff: str,
    down_payment: Decimal,
    goods_price: Decimal,
    is_repeat: bool,
    limits: LimitsConfig,
    tariffs: dict[str, TariffConfig],
) -> bool:
    config = tariffs[tariff]
    if config.requires_repeat_client and not is_repeat:
        return False
    if not _meets_down_payment(down_payment, goods_price, config.down_payment_min_pct):
        return False
    financed_amount = goods_price - down_payment
    return limits.amount_min <= financed_amount <= limits.amount_max


def evaluate_deal_limits(
    *,
    requested_tariff: str,
    goods_price: Decimal,
    down_payment: Decimal,
    term_months: int,
    is_repeat: bool,
    limits: LimitsConfig,
    tariffs: dict[str, TariffConfig],
) -> DealLimitsOutcome:
    """financed_amount = goods_price - down_payment — первый взнос уменьшает
    финансируемую сумму, поэтому больший взнос может провести сделку под
    лимит суммы даже там, где по запрошенному тарифу сумма его превышала."""
    if term_months < limits.term_min or term_months > limits.term_max:
        return DealLimitsOutcome(passed=False, reason_code="TERM_OUT_OF_RANGE")

    if requested_tariff not in tariffs:
        return DealLimitsOutcome(passed=False, reason_code="UNKNOWN_TARIFF")

    if _tariff_fits(
        tariff=requested_tariff, down_payment=down_payment, goods_price=goods_price,
        is_repeat=is_repeat, limits=limits, tariffs=tariffs,
    ):
        return DealLimitsOutcome(passed=True, tariff=requested_tariff, required_down_pay=down_payment)

    best: tuple[str, Decimal] | None = None
    for name, config in tariffs.items():
        candidate_down_pay = max(down_payment, goods_price * Decimal(str(config.down_payment_min_pct)))
        if _tariff_fits(
            tariff=name, down_payment=candidate_down_pay, goods_price=goods_price,
            is_repeat=is_repeat, limits=limits, tariffs=tariffs,
        ):
            if best is None or candidate_down_pay < best[1]:
                best = (name, candidate_down_pay)

    if best is not None:
        name, down_pay = best
        return DealLimitsOutcome(passed=True, tariff=name, required_down_pay=down_pay, is_alternative=True)

    return DealLimitsOutcome(passed=False, reason_code="LIMITS_NOT_MET")
