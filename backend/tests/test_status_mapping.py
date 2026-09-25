"""T11 ("три вещи" -> операторские комментарии): комментарий оператору —
человеческим языком, с фактическим значением и порогом, без служебных кодов
(«оператор не программист»). Источник текстов — reason_notes в
config/status_mapping.yaml."""

from pathlib import Path

from app.config import get_settings
from app.engine.schema import load_ruleset_from_yaml
from app.integrations.status_mapping import build_operator_comment, get_all_reason_notes


def _rule(**overrides) -> dict:
    base = {
        "id": "pti_high", "field": "features.pti", "op": "gt", "expected": 0.55,
        "actual": 0.72, "reason": "PTI_HIGH", "action": "manual_review", "freeze": False,
    }
    return {**base, **overrides}


# --- покрытие: каждый код причины из dev.yaml имеет текст -----------------------


def test_all_dev_ruleset_reason_codes_have_operator_text():
    """Аналог test_dev_ruleset_rule_field_paths_all_resolve_against_real_snapshot
    (T09) — но для текстов оператору: у каждого правила из hard_stops/
    stage_b_pre_bki/soft_rules (по reason, а если reason не задан — по id, см.
    app.engine.rules.evaluate_financial/build_operator_comment) должен быть
    шаблон в reason_notes, иначе оператор увидит запасной вариант вместо
    готового текста."""
    config = load_ruleset_from_yaml(Path(get_settings().ruleset_path))
    notes = get_all_reason_notes()

    all_rules = [*config.hard_stops, *config.stage_b_pre_bki, *config.soft_rules]
    codes = {rule.reason or rule.id for rule in all_rules}
    missing = sorted(codes - notes.keys())
    assert missing == [], f"нет текста в reason_notes для: {missing}"


# --- build_operator_comment: правила движка --------------------------------------


def test_renders_rule_with_actual_and_expected_as_percentage():
    comment = build_operator_comment(triggered_rules=[_rule()])
    assert comment == "Долговая нагрузка 72% при допустимых 55%."


def test_lists_all_triggered_rules_not_just_first():
    rules = [
        _rule(id="pti_high", reason="PTI_HIGH", actual=0.72, expected=0.55),
        _rule(
            id="fssp_minor", field="features.fssp_total_amount", op="between", reason=None,
            actual=12000.0, expected=[5000, 50000],
        ),
    ]
    comment = build_operator_comment(triggered_rules=rules)
    assert "Долговая нагрузка 72%" in comment
    assert "12000" in comment and "мелким исполнительным" in comment


def test_never_shows_raw_reason_code_in_rule_text():
    comment = build_operator_comment(triggered_rules=[_rule()])
    assert "PTI_HIGH" not in comment


def test_rule_without_configured_text_falls_back_to_name_and_values_not_empty():
    rule = _rule(id="some_new_rule", reason="SOME_NEW_REASON_CODE_NOT_IN_CONFIG", actual=42, expected=10)
    comment = build_operator_comment(triggered_rules=[rule])
    assert comment != ""
    assert "SOME_NEW_REASON_CODE_NOT_IN_CONFIG" not in comment  # без служебного кода
    assert "some_new_rule" in comment
    assert "42" in comment and "10" in comment


def test_rule_actual_float_displayed_cleanly_not_as_62000_0():
    rule = _rule(
        id="fssp_credit_debt", field="features.fssp_credit_debt_amount", op="gt",
        reason="FSSP_CREDIT_DEBT", actual=62000.0, expected=50000,
    )
    comment = build_operator_comment(triggered_rules=[rule])
    assert "62000" in comment
    assert "62000.0" not in comment


# --- build_operator_comment: операционные исходы (без правил движка) ------------


def test_reason_code_without_rules_uses_context_substitution():
    comment = build_operator_comment(
        reason_code="SCORE_BELOW_THRESHOLD", context={"score": 380, "auto_reject_below": 450},
    )
    assert comment == "Скоринговый балл 380 ниже порога автоотказа 450."


def test_reason_code_without_rules_and_without_text_is_not_empty_and_has_no_code():
    comment = build_operator_comment(reason_code="SOME_UNCONFIGURED_OPERATIONAL_CODE")
    assert comment != ""
    assert "SOME_UNCONFIGURED_OPERATIONAL_CODE" not in comment


def test_no_reason_code_and_no_rules_is_not_empty():
    comment = build_operator_comment()
    assert comment != ""


def test_bki_not_configured_note_has_no_double_space_around_it():
    """Регрессия: раньше note дописывался отдельной строкой поверх уже
    отрендеренного шаблона ("Требуется решение оператора. " + " БКИ..."),
    получался двойной пробел."""
    comment = build_operator_comment(reason_code="BKI_NOT_CONFIGURED")
    assert "  " not in comment
    assert comment == "БКИ не подключено, решение за оператором."
