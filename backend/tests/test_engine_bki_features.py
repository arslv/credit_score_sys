"""T11: признаки БКИ — заявленный ПДН (до отчёта) и фактический ПДН + сигналы
(после отчёта). Чистые функции, синтетические отчёты — без БД и сети."""

from datetime import date, timedelta
from decimal import Decimal

from app.engine.bki_features import (
    compute_bki_features,
    compute_declared_pti_features,
    compute_installment_payment,
    parse_declared_income_and_obligations,
)
from app.sources.bki import (
    BkiContract,
    BkiInquiry,
    BkiReport,
    empty_history_report,
    good_history_report,
    overdue_history_report,
    overloaded_report,
)

# Чуть позже самой поздней даты, зашитой в синтетические отчёты
# app.sources.bki (инкассо/запросы там датированы 2022-2025) — иначе "сейчас"
# в тесте оказалось бы на годы позже отчёта и все окна recency обнулились бы.
_TODAY = date(2025, 9, 1)


# --- заявленный ПДН -----------------------------------------------------------


def test_parse_declared_income_and_obligations():
    income, obligations = parse_declared_income_and_obligations(
        {"declared_monthly": "100000", "additional_monthly": "20000"}, {"monthly_payment_total": "30000"}
    )
    assert income == Decimal("120000")
    assert obligations == Decimal("30000")


def test_parse_declared_income_and_obligations_missing_fields_default_to_zero():
    income, obligations = parse_declared_income_and_obligations({}, {})
    assert income == Decimal("0")
    assert obligations == Decimal("0")


def test_declared_pti_computed_from_income_and_obligations():
    features = compute_declared_pti_features(Decimal("100000"), Decimal("30000"), Decimal("0"), "partner", pti_max=0.60)
    assert features["declared_pti"] == 0.3
    assert features["declared_pti_exceeds_max"] is False


def test_declared_pti_exceeds_max_when_above_threshold():
    features = compute_declared_pti_features(Decimal("100000"), Decimal("70000"), Decimal("0"), "partner", pti_max=0.60)
    assert features["declared_pti"] == 0.7
    assert features["declared_pti_exceeds_max"] is True


def test_declared_pti_exactly_at_threshold_does_not_exceed():
    features = compute_declared_pti_features(Decimal("100000"), Decimal("60000"), Decimal("0"), "partner", pti_max=0.60)
    assert features["declared_pti_exceeds_max"] is False  # строго больше, не >=


def test_declared_pti_none_when_no_income():
    features = compute_declared_pti_features(Decimal("0"), Decimal("30000"), Decimal("0"), "partner", pti_max=0.60)
    assert features["declared_pti"] is None
    assert features["declared_pti_exceeds_max"] is False  # informational, не отказ


# --- признаки после получения отчёта -------------------------------------------


def test_empty_history_report_has_no_signal():
    features = compute_bki_features(
        empty_history_report(), monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"),
        installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["pti"] == 0.0
    assert features["bki_active_contracts_count"] == 0
    assert features["bki_credit_history_depth_months"] == 0
    assert features["bki_max_overdue_6mo"] == 0
    assert features["bki_overdue_30_plus_count"] == 0
    assert features["bki_inquiries_1mo"] == 0


def test_good_history_report_pti_and_no_overdue():
    features = compute_bki_features(
        good_history_report(), monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"),
        installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    # Единственный договор в good_history_report закрыт -> не активен -> не
    # учитывается в факт. ПДН.
    assert features["pti"] == 0.0
    assert features["bki_active_contracts_count"] == 0
    assert features["bki_max_overdue_6mo"] == 0
    assert features["bki_overdue_30_plus_count"] == 0


def test_overdue_report_flags_high_buckets():
    features = compute_bki_features(
        overdue_history_report(), monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"),
        installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["bki_overdue_30_plus_count"] == 1
    assert features["bki_overdue_60_plus_count"] == 1
    assert features["bki_overdue_90_plus_count"] == 1
    assert features["bki_active_contracts_count"] == 1
    assert features["pti"] == 0.15  # 15000 / 100000


def test_overloaded_report_pti_sums_all_active_contracts():
    features = compute_bki_features(
        overloaded_report(), monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"),
        installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["bki_active_contracts_count"] == 3
    assert features["pti"] == 0.34  # (18000+9000+7000) / 100000
    assert features["bki_inquiries_3mo"] >= 1


def test_pti_none_when_no_income():
    features = compute_bki_features(
        overloaded_report(), monthly_income=Decimal("0"), declared_monthly_obligations=Decimal("0"), installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["pti"] is None


def test_obligations_discrepancy_declared_vs_factual():
    report = BkiReport(
        contracts=[
            BkiContract(
                opened_at=_TODAY - timedelta(days=400), closed_at=None, status="active",
                monthly_payment="10000", balance="50000", overdue_days_max=0, subject="Кредит",
            )
        ],
        inquiries=[], score=700,
    )
    features = compute_bki_features(
        report, monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("15000"), installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["bki_obligations_discrepancy"] == 5000.0  # 15000 заявлено - 10000 по факту


def test_inquiries_counted_within_each_window():
    report = BkiReport(
        contracts=[],
        inquiries=[
            BkiInquiry(requested_at=_TODAY - timedelta(days=10), requester="Банк А"),  # 1/3/6 мес
            BkiInquiry(requested_at=_TODAY - timedelta(days=80), requester="Банк Б"),  # 3/6 мес, не 1
            BkiInquiry(requested_at=_TODAY - timedelta(days=170), requester="Банк В"),  # 6 мес, не 1/3
            BkiInquiry(requested_at=_TODAY - timedelta(days=400), requester="Банк Г"),  # ни один
        ],
        score=None,
    )
    features = compute_bki_features(
        report, monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"), installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["bki_inquiries_1mo"] == 1
    assert features["bki_inquiries_3mo"] == 2
    assert features["bki_inquiries_6mo"] == 3


def test_max_overdue_windows_only_count_contracts_touching_window():
    old_closed = BkiContract(
        # Закрыт ~600 дней назад: вне окон 6/12 мес (180/360 дней), но внутри
        # 24-месячного (720 дней) — см. _contract_touches_window.
        opened_at=_TODAY - timedelta(days=1000), closed_at=_TODAY - timedelta(days=600), status="closed",
        monthly_payment="5000", balance="0", overdue_days_max=45, subject="Займ",
    )
    recent_active = BkiContract(
        opened_at=_TODAY - timedelta(days=100), closed_at=None, status="active",
        monthly_payment="8000", balance="40000", overdue_days_max=10, subject="Кредит",
    )
    report = BkiReport(contracts=[old_closed, recent_active], inquiries=[], score=600)

    features = compute_bki_features(
        report, monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"), installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["bki_max_overdue_6mo"] == 10  # только недавний договор попадает в окно
    assert features["bki_max_overdue_12mo"] == 10
    assert features["bki_max_overdue_24mo"] == 45  # оба договора попадают, старый просрочен сильнее


def test_credit_history_depth_months_from_earliest_contract():
    report = BkiReport(
        contracts=[
            BkiContract(
                opened_at=_TODAY - timedelta(days=730), closed_at=None, status="active",
                monthly_payment="1000", balance="1000", overdue_days_max=0, subject="Кредит",
            )
        ],
        inquiries=[], score=700,
    )
    features = compute_bki_features(
        report, monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"), installment_payment=Decimal("0"), today=_TODAY,
    )["features"]
    assert features["bki_credit_history_depth_months"] == 24


# --- T21: платёж по нашей рассрочке в ПДН ---------------------------------------


class _Tariff:
    def __init__(self, markup_pct):
        self.markup_pct = markup_pct


def test_installment_from_partner_takes_priority_over_tariff():
    payment, source = compute_installment_payment(
        partner_monthly_payment="6500", goods_price=Decimal("60000"), down_payment=Decimal("6000"),
        term_months=12, tariff="standard", tariffs={"standard": _Tariff(0.2)},
    )
    assert (payment, source) == (Decimal("6500.00"), "partner")


def test_installment_from_tariff_markup_when_partner_did_not_send():
    # (60000 − 6000) × 1.2 / 12 = 5400
    payment, source = compute_installment_payment(
        partner_monthly_payment=None, goods_price=Decimal("60000"), down_payment=Decimal("6000"),
        term_months=12, tariff="standard", tariffs={"standard": _Tariff(0.2)},
    )
    assert (payment, source) == (Decimal("5400.00"), "tariff")


def test_installment_with_zero_markup_is_principal_over_term():
    payment, source = compute_installment_payment(
        partner_monthly_payment="", goods_price=Decimal("60000"), down_payment=Decimal("6000"),
        term_months=12, tariff="standard", tariffs={"standard": _Tariff(0.0)},
    )
    assert (payment, source) == (Decimal("4500.00"), "tariff")


def test_installment_not_computable_without_partner_value_and_tariff_markup():
    for tariffs, tariff in (({"standard": _Tariff(None)}, "standard"), ({}, "unknown")):
        assert compute_installment_payment(
            partner_monthly_payment=None, goods_price=Decimal("60000"), down_payment=Decimal("6000"),
            term_months=12, tariff=tariff, tariffs=tariffs,
        ) == (None, None)


def test_factual_pti_includes_installment_payment():
    # (34000 по трём действующим кредитам + 6500 по рассрочке) / 100000
    features = compute_bki_features(
        overloaded_report(), monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"),
        installment_payment=Decimal("6500"), today=_TODAY,
    )["features"]
    assert features["pti"] == 0.405


def test_factual_pti_none_without_installment_payment():
    features = compute_bki_features(
        overloaded_report(), monthly_income=Decimal("100000"), declared_monthly_obligations=Decimal("0"),
        installment_payment=None, today=_TODAY,
    )["features"]
    assert features["pti"] is None


def test_declared_pti_includes_installment_and_records_source():
    # (30000 заявлено + 6500 рассрочка) / 100000 = 0.365
    features = compute_declared_pti_features(
        Decimal("100000"), Decimal("30000"), Decimal("6500"), "partner", pti_max=0.60
    )
    assert features["declared_pti"] == 0.365
    assert features["installment_monthly_payment"] == 6500.0
    assert features["installment_payment_source"] == "partner"
    # Платёж по рассрочке сам может перевести заявленный ПДН за порог.
    assert compute_declared_pti_features(
        Decimal("100000"), Decimal("55000"), Decimal("6500"), "partner", pti_max=0.60
    )["declared_pti_exceeds_max"] is True


def test_declared_pti_none_without_installment():
    features = compute_declared_pti_features(Decimal("100000"), Decimal("30000"), None, None, pti_max=0.60)
    assert features["declared_pti"] is None
    assert features["declared_pti_exceeds_max"] is False
    assert features["installment_payment_source"] is None
