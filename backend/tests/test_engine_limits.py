from decimal import Decimal

from app.engine.limits import evaluate_deal_limits
from app.engine.schema import LimitsConfig, TariffConfig

_LIMITS = LimitsConfig(
    amount_min=Decimal("15000"), amount_max=Decimal("500000"),
    term_min=3, term_max=24, pti_max=0.6, declared_income_cap=Decimal("80000"),
)
_TARIFFS = {
    "standard": TariffConfig(down_payment_min_pct=0.0),
    "advantageous": TariffConfig(down_payment_min_pct=0.20),
    "repeat_client": TariffConfig(down_payment_min_pct=0.0, requires_repeat_client=True),
}


def _evaluate(**overrides):
    params = dict(
        requested_tariff="standard", goods_price=Decimal("100000"), down_payment=Decimal("0"),
        term_months=12, is_repeat=False, limits=_LIMITS, tariffs=_TARIFFS,
    )
    params.update(overrides)
    return evaluate_deal_limits(**params)


def test_standard_tariff_within_limits_passes_as_requested():
    outcome = _evaluate()
    assert outcome.passed is True
    assert outcome.tariff == "standard"
    assert outcome.is_alternative is False


def test_term_out_of_range_rejects_before_tariff_logic():
    outcome = _evaluate(term_months=36)
    assert outcome.passed is False
    assert outcome.reason_code == "TERM_OUT_OF_RANGE"


def test_unknown_tariff_rejects():
    outcome = _evaluate(requested_tariff="does_not_exist")
    assert outcome.passed is False
    assert outcome.reason_code == "UNKNOWN_TARIFF"


def test_amount_too_high_for_standard_offers_advantageous_with_required_down_payment():
    # financed = 550000 при standard (0% взноса) -> выше amount_max (500000).
    # advantageous требует >=20% взноса: 550000*0.20=110000 -> financed=440000, в лимите.
    outcome = _evaluate(requested_tariff="standard", goods_price=Decimal("550000"), down_payment=Decimal("0"))
    assert outcome.passed is True
    assert outcome.is_alternative is True
    assert outcome.tariff == "advantageous"
    assert outcome.required_down_pay == Decimal("110000.00000")


def test_amount_too_high_even_for_best_tariff_rejects_with_limits_not_met():
    outcome = _evaluate(goods_price=Decimal("10000000"), down_payment=Decimal("0"))
    assert outcome.passed is False
    assert outcome.reason_code == "LIMITS_NOT_MET"


def test_repeat_client_tariff_unavailable_without_is_repeat():
    # advantageous ещё проходит (20% взноса хватает), repeat_client недоступен
    # без is_repeat, но это не мешает найти advantageous как альтернативу.
    outcome = _evaluate(
        requested_tariff="repeat_client", goods_price=Decimal("550000"), down_payment=Decimal("0"), is_repeat=False,
    )
    assert outcome.passed is True
    assert outcome.tariff != "repeat_client"


def test_repeat_client_tariff_available_with_is_repeat():
    outcome = _evaluate(requested_tariff="repeat_client", is_repeat=True)
    assert outcome.passed is True
    assert outcome.tariff == "repeat_client"
    assert outcome.is_alternative is False


def test_already_sufficient_down_payment_on_requested_tariff_does_not_trigger_offer():
    outcome = _evaluate(requested_tariff="advantageous", goods_price=Decimal("100000"), down_payment=Decimal("20000"))
    assert outcome.passed is True
    assert outcome.is_alternative is False
    assert outcome.tariff == "advantageous"
