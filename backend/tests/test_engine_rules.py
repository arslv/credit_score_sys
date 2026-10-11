import pytest

from app.engine.rules import (
    RuleEvaluationError,
    evaluate_financial,
    evaluate_hard_stops,
    evaluate_stage_b_pre_bki,
    get_field,
    parse_matrix_key,
    resolve_matrix_overrides,
)
from app.engine.schema import RuleDef, RulesetConfig


def _config(**overrides) -> RulesetConfig:
    base = {
        "version": 1,
        "effective_from": "2026-01-01",
        "ocr": {"min_confidence_key_fields": 0.85},
        "sources": {
            "rosfinmon": {"name_match_min_similarity": 0.7},
            "registries": {"max_staleness_days": 2},
            "passport": {
                "department_code_check_enabled": False,
                "blank_year_max_issue_offset_years": 3,
            },
            "fssp": {"classification": []},
        },
        "hard_stops": [],
        "stage_b_pre_bki": [],
        "soft_rules": [],
        "limits": {
            "amount_min": 15000,
            "amount_max": 500000,
            "term_min": 3,
            "term_max": 24,
            "pti_max": 0.60,
            "declared_income_cap": 80000,
        },
        "tariffs": {"standard": {"down_payment_min_pct": 0.0}},
        "stage_b_completeness": {"required_sections": ["deal"]},
        "bki": {"cache_days": 30, "on_unavailable": "manual_review", "consent_max_age_days": 30, "in_flight_timeout_minutes": 10},
        "score_thresholds": {"enabled": True, "auto_approve_above": 700, "auto_reject_below": 450},
        "matrix": [],
        "antifraud": {"max_applications_per_phone_30d": 3, "duplicate_window_days": 30},
    }
    base.update(overrides)
    return RulesetConfig.model_validate(base)


def _rule(**overrides) -> dict:
    base = {"id": "x", "field": "f", "op": "eq", "value": 1, "reason": "R"}
    base.update(overrides)
    return base


# --- get_field -------------------------------------------------------------


def test_get_field_resolves_nested_path():
    data = {"checks": {"mvd": {"status": "hit"}}}
    assert get_field(data, "checks.mvd.status") == "hit"


def test_get_field_missing_path():
    from app.engine.rules import _MISSING

    assert get_field({"a": 1}, "a.b.c") is _MISSING
    assert get_field({}, "a") is _MISSING


# --- operators, including boundaries ---------------------------------------


@pytest.mark.parametrize(
    "op,value,actual,expected",
    [
        ("eq", 10, 10, True),
        ("eq", 10, 11, False),
        ("ne", 10, 11, True),
        ("ne", 10, 10, False),
        ("lt", 10, 9, True),
        ("lt", 10, 10, False),  # граница
        ("lte", 10, 10, True),  # граница
        ("lte", 10, 11, False),
        ("gt", 10, 11, True),
        ("gt", 10, 10, False),  # граница
        ("gte", 10, 10, True),  # граница
        ("gte", 10, 9, False),
        ("between", [5, 10], 5, True),  # нижняя граница
        ("between", [5, 10], 10, True),  # верхняя граница
        ("between", [5, 10], 4, False),
        ("between", [5, 10], 11, False),
        ("in", [1, 2, 3], 2, True),
        ("in", [1, 2, 3], 4, False),
        ("not_in", [1, 2, 3], 4, True),
        ("not_in", [1, 2, 3], 2, False),
        ("is_null", None, None, True),
        ("is_null", None, 0, False),
    ],
)
def test_operator_boundaries(op, value, actual, expected):
    config = _config(hard_stops=[_rule(op=op, value=value)])
    outcome = evaluate_hard_stops(config, {"f": actual})
    assert outcome.matched is expected


def test_operator_type_mismatch_raises_clear_error():
    config = _config(hard_stops=[_rule(op="lt", value=10)])
    with pytest.raises(RuleEvaluationError):
        evaluate_hard_stops(config, {"f": "not-a-number"})


# --- missing fields ----------------------------------------------------------


def test_missing_field_does_not_trigger_and_is_reported():
    config = _config(hard_stops=[_rule(field="checks.mvd.status", op="eq", value="hit")])
    outcome = evaluate_hard_stops(config, {})
    assert outcome.matched is False
    assert outcome.missing_fields == ["checks.mvd.status"]


def test_null_value_treated_as_missing_for_non_is_null_ops():
    config = _config(hard_stops=[_rule(field="f", op="eq", value=1)])
    outcome = evaluate_hard_stops(config, {"f": None})
    assert outcome.matched is False
    assert outcome.missing_fields == ["f"]


# --- hard_stops / stage_b_pre_bki -------------------------------------------


def test_hard_stops_reason_code_and_freeze():
    config = _config(
        hard_stops=[
            _rule(id="age_min", field="age", op="lt", value=21, reason="AGE_BELOW_MIN"),
            _rule(id="sanctions", field="checks.rosfinmon.status", op="eq", value="hit",
                  reason="SANCTIONS_HIT", freeze=True),
        ]
    )
    outcome = evaluate_hard_stops(config, {"age": 19, "checks": {"rosfinmon": {"status": "hit"}}})
    assert outcome.matched is True
    assert {r.id for r in outcome.triggered_rules} == {"age_min", "sanctions"}
    assert outcome.freeze is True
    assert outcome.reason_code == "AGE_BELOW_MIN"  # первое сработавшее по порядку конфига


def test_hard_stops_no_match():
    config = _config(hard_stops=[_rule(field="age", op="lt", value=21, reason="AGE_BELOW_MIN")])
    outcome = evaluate_hard_stops(config, {"age": 30})
    assert outcome.matched is False
    assert outcome.reason_code is None
    assert outcome.freeze is False


def test_stage_b_pre_bki_sharia_filter():
    config = _config(
        stage_b_pre_bki=[
            _rule(id="sharia_goods", field="goods.sharia_allowed", op="eq", value=False,
                  reason="GOODS_NOT_ALLOWED")
        ]
    )
    outcome = evaluate_stage_b_pre_bki(config, {"goods": {"sharia_allowed": False}})
    assert outcome.matched is True
    assert outcome.reason_code == "GOODS_NOT_ALLOWED"


# --- matrix ------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected_field,expected_op",
    [
        ("amount_gt", "amount", "gt"),
        ("amount_gte", "amount", "gte"),
        ("amount_lt", "amount", "lt"),
        ("amount_lte", "amount", "lte"),
        ("prior_defaults_ne", "prior_defaults", "ne"),
        ("goods_liquidity", "goods_liquidity", "eq"),
        ("is_repeat", "is_repeat", "eq"),
    ],
)
def test_parse_matrix_key(key, expected_field, expected_op):
    field_path, op = parse_matrix_key(key)
    assert field_path == expected_field
    assert op == expected_op


def test_matrix_overrides_threshold_when_condition_matches():
    config = _config(
        score_thresholds={"enabled": True, "auto_approve_above": 700, "auto_reject_below": 450},
        matrix=[{"when": {"amount_gt": 200000}, "override": {"auto_approve_above": 780}}],
    )
    resolved, missing = resolve_matrix_overrides(config, {"amount": 250000})
    assert resolved["auto_approve_above"] == 780
    assert missing == set()


def test_matrix_does_not_override_when_condition_does_not_match():
    config = _config(
        score_thresholds={"enabled": True, "auto_approve_above": 700, "auto_reject_below": 450},
        matrix=[{"when": {"amount_gt": 200000}, "override": {"auto_approve_above": 780}}],
    )
    resolved, _ = resolve_matrix_overrides(config, {"amount": 100000})
    assert resolved["auto_approve_above"] == 700


def test_matrix_later_entry_wins_on_conflict():
    config = _config(
        matrix=[
            {"when": {"is_repeat": True}, "override": {"auto_approve_above": 100}},
            {"when": {"is_repeat": True}, "override": {"auto_approve_above": 200}},
        ]
    )
    resolved, _ = resolve_matrix_overrides(config, {"is_repeat": True})
    assert resolved["auto_approve_above"] == 200


def test_matrix_condition_on_missing_field_does_not_match():
    config = _config(matrix=[{"when": {"amount_gt": 200000}, "override": {"auto_approve_above": 780}}])
    resolved, missing = resolve_matrix_overrides(config, {})
    assert resolved["auto_approve_above"] == 700  # базовый порог, не переопределён
    assert "amount" in missing


def test_matrix_changes_final_outcome_via_evaluate_financial():
    config = _config(
        matrix=[{"when": {"amount_gt": 200000}, "override": {"auto_approve_above": 780}}],
    )
    # без матрицы 750 >= 700 -> approve; с матрицей порог поднят до 780 -> серая зона
    outcome = evaluate_financial(config, {"amount": 250000, "score": 750})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "SCORE_GRAY_ZONE"
    assert outcome.thresholds_used["auto_approve_above"] == 780


# --- evaluate_financial --------------------------------------------------------


def test_financial_soft_rule_triggers_manual_review():
    config = _config(
        soft_rules=[_rule(id="pti_high", field="features.pti", op="gt", value=0.55,
                           action="manual_review", reason="PTI_HIGH")]
    )
    outcome = evaluate_financial(config, {"features": {"pti": 0.6}, "score": 900})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "PTI_HIGH"


def _pti_rules_config(*, applies_to_credit_history: bool = True):
    config = _config(
        soft_rules=[_rule(id="pti_high", field="features.pti", op="gt", value=0.55,
                           action="manual_review", reason="PTI_HIGH")]
    )
    config.limits.pti_max_applies_to_credit_history = applies_to_credit_history
    return config


def test_financial_pti_reject_off_in_versions_before_t22():
    """Старые версии правил (ключа нет -> False) решают, как решали: 83% по
    КИ — ручная проверка. Иначе симуляция и история версий не видели бы, с
    какой версии 83% стало отказом."""
    outcome = evaluate_financial(
        _pti_rules_config(applies_to_credit_history=False), {"features": {"pti": 0.83}, "score": 900}
    )
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "PTI_HIGH"


def test_financial_pti_by_credit_history_above_pti_max_rejects():
    """T22 (ваше решение, ТЗ: «ПДН, отказ — выше 60%»): ПДН по КИ выше
    limits.pti_max — отказ, а не ручная проверка по pti_high. Раньше 83% давало
    серую зону: pti_max действовал только на заявленный ПДН до БКИ."""
    outcome = evaluate_financial(_pti_rules_config(), {"features": {"pti": 0.83}, "score": 900})
    assert outcome.outcome == "reject"
    assert outcome.reason_code == "PTI_TOO_HIGH"
    [rule] = outcome.triggered_rules
    assert (rule.id, rule.field, rule.actual, rule.expected, rule.action) == (
        "pti_max", "features.pti", 0.83, 0.60, "reject",
    )


def test_financial_pti_exactly_at_pti_max_is_not_reject():
    # Строго больше, как у заявленного ПДН (declared_pti_exceeds_max).
    outcome = evaluate_financial(_pti_rules_config(), {"features": {"pti": 0.60}, "score": 900})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "PTI_HIGH"


def test_financial_pti_between_manual_and_reject_thresholds_is_manual_review():
    outcome = evaluate_financial(_pti_rules_config(), {"features": {"pti": 0.58}, "score": 900})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "PTI_HIGH"


def test_financial_pti_reject_uses_pti_max_from_config_not_a_literal():
    config = _pti_rules_config()
    config.limits.pti_max = 0.90
    outcome = evaluate_financial(config, {"features": {"pti": 0.83}, "score": 900})
    assert outcome.outcome == "manual_review"  # 83% < 90% — только ручная проверка


def test_financial_missing_pti_does_not_reject():
    outcome = evaluate_financial(_pti_rules_config(), {"features": {"pti": None}, "score": 900})
    assert outcome.outcome == "approve"


def test_financial_score_above_approve_threshold():
    config = _config()
    outcome = evaluate_financial(config, {"score": 700})
    assert outcome.outcome == "approve"


def test_financial_score_below_reject_threshold():
    config = _config()
    outcome = evaluate_financial(config, {"score": 449})
    assert outcome.outcome == "reject"
    assert outcome.reason_code == "SCORE_BELOW_THRESHOLD"


def test_financial_score_in_gray_zone():
    config = _config()
    outcome = evaluate_financial(config, {"score": 500})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "SCORE_GRAY_ZONE"


def test_financial_missing_score():
    config = _config()
    outcome = evaluate_financial(config, {})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "SCORE_MISSING"
    assert "score" in outcome.missing_fields


def test_financial_score_thresholds_disabled():
    config = _config(score_thresholds={"enabled": False, "auto_approve_above": 700, "auto_reject_below": 450})
    outcome = evaluate_financial(config, {"score": 900})
    assert outcome.outcome == "manual_review"
    assert outcome.reason_code == "SCORE_THRESHOLDS_DISABLED"


# --- determinism ---------------------------------------------------------------


def test_same_input_gives_same_output():
    config = _config(
        hard_stops=[_rule(id="age_min", field="age", op="lt", value=21, reason="AGE_BELOW_MIN")],
        matrix=[{"when": {"amount_gt": 200000}, "override": {"auto_approve_above": 780}}],
    )
    features = {"age": 19, "amount": 250000, "score": 750}

    result_1 = evaluate_hard_stops(config, features)
    result_2 = evaluate_hard_stops(config, features)
    assert result_1 == result_2

    financial_1 = evaluate_financial(config, features)
    financial_2 = evaluate_financial(config, features)
    assert financial_1 == financial_2
