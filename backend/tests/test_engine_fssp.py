from app.engine.fssp import classify_production, summarize_productions
from app.engine.schema import FsspCategory, FsspClassificationRule

_RULES = [
    FsspClassificationRule(category=FsspCategory.NOISE, match=["гибдд", "штраф"]),
    FsspClassificationRule(category=FsspCategory.CREDIT_DEBT, match=["кредит", "займ"]),
]


def test_classify_matches_case_insensitively():
    assert classify_production("Штраф ГИБДД за превышение", _RULES) is FsspCategory.NOISE


def test_classify_credit_debt():
    assert classify_production("Взыскание по кредитному договору", _RULES) is FsspCategory.CREDIT_DEBT


def test_classify_unmatched_is_other():
    assert classify_production("Алименты на содержание ребёнка", _RULES) is FsspCategory.OTHER


def test_classify_first_matching_rule_wins():
    rules = [
        FsspClassificationRule(category=FsspCategory.NOISE, match=["штраф"]),
        FsspClassificationRule(category=FsspCategory.CREDIT_DEBT, match=["штраф"]),
    ]
    assert classify_production("Штраф", rules) is FsspCategory.NOISE


def test_summarize_excludes_noise_from_total_and_count():
    productions = [
        {"subject": "Штраф ГИБДД", "amount": "3000"},
        {"subject": "Взыскание по кредитному договору", "amount": "150000.50"},
        {"subject": "Алименты", "amount": "20000"},
    ]
    result = summarize_productions(productions, _RULES)

    assert result["fssp_credit_debt_amount"] == 150000.50
    assert result["fssp_total_amount"] == 170000.50  # без штрафа ГИБДД
    assert result["fssp_count"] == 2  # штраф не считается


def test_summarize_empty_productions():
    result = summarize_productions([], _RULES)
    assert result == {
        "fssp_credit_debt_amount": 0.0,
        "fssp_total_amount": 0.0,
        "fssp_count": 0,
    }


def test_summarize_no_classification_rules_treats_everything_as_other():
    productions = [{"subject": "Что угодно", "amount": "1000"}]
    result = summarize_productions(productions, [])
    assert result["fssp_credit_debt_amount"] == 0.0
    assert result["fssp_total_amount"] == 1000.0
    assert result["fssp_count"] == 1
