from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import get_settings
from app.engine.rules import evaluate_hard_stops
from app.engine.schema import load_ruleset_from_yaml, parse_ruleset

# Путь берём из настроек — тот же относительный путь ("config/rulesets/dev.yaml"),
# который приложение резолвит от cwd. На хосте cwd — backend/, в контейнере — /app,
# где config/ смонтирован volume'ом (см. docker-compose.yml).
DEV_RULESET_PATH = Path(get_settings().ruleset_path)


def _base_config(**overrides):
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
        "bki": {"cache_days": 30, "on_unavailable": "manual_review", "consent_max_age_days": 30},
        "score_thresholds": {"enabled": True, "auto_approve_above": 700, "auto_reject_below": 450},
        "matrix": [],
        "antifraud": {"max_applications_per_phone_30d": 3, "duplicate_window_days": 30},
    }
    base.update(overrides)
    return base


def _load_real_config_dict() -> dict:
    return yaml.safe_load(DEV_RULESET_PATH.read_text(encoding="utf-8"))


def test_dev_ruleset_file_is_valid():
    """Конфиг по тому же пути, что резолвит Settings.ruleset_path при запуске
    приложения — обязан проходить схему целиком. Это защита от того, что
    сломанный YAML попадёт в прод и о поломке узнают только там."""
    config = load_ruleset_from_yaml(DEV_RULESET_PATH)
    assert config.version == 1
    assert config.ocr.min_confidence_key_fields == 0.85
    assert any(r.id == "age_min" for r in config.hard_stops)
    assert len(config.matrix) == 3
    assert config.sources.rosfinmon.name_match_min_similarity is not None
    assert config.limits.amount_min < config.limits.amount_max


def test_dev_ruleset_blank_year_hard_stop_wired_correctly():
    """Проверяет реальный hard_stop из dev.yaml напрямую движком (без БД/пайплайна
    — не оставляет висящих версий в ruleset_versions, влияющих на другие тесты):
    что правило в файле ссылается на верное имя признака и даёт верный
    reason_code. Саму арифметику года бланка (когда признак действительно
    становится True) проверяет test_engine_passport.py."""
    config = load_ruleset_from_yaml(DEV_RULESET_PATH)

    outcome = evaluate_hard_stops(config, {"age": 30, "passport_blank_year_future": True})

    assert outcome.matched is True
    assert outcome.reason_code == "PASSPORT_BLANK_YEAR_FUTURE"


def test_dev_ruleset_sanctions_reason_code_wins_and_freezes_regardless_of_other_matches():
    """Порядок hard_stops в dev.yaml — по тяжести (комментарий в самом файле):
    санкции первые. При одновременном совпадении санкций и любого другого
    правила reason_code обязан остаться SANCTIONS_HIT, а не смениться на то,
    что стоит ниже по списку. freeze не зависит от порядка (см. отдельный тест
    test_hard_stops_reason_code_and_freeze в test_engine_rules.py, где freeze
    стоит вторым правилом и всё равно срабатывает) — здесь дополнительно
    проверяем это на реальном dev.yaml с реальными сработавшими правилами."""
    config = load_ruleset_from_yaml(DEV_RULESET_PATH)

    outcome = evaluate_hard_stops(
        config,
        {
            "age": 30,
            "checks": {"rosfinmon": {"status": "hit"}},
            "passport_blank_year_future": True,
        },
    )

    assert outcome.reason_code == "SANCTIONS_HIT"
    assert outcome.freeze is True
    assert {r.id for r in outcome.triggered_rules} == {"sanctions", "passport_blank_year_future"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["hard_stops"].__setitem__(0, {**raw["hard_stops"][0], "typo_field": "oops"}),
        lambda raw: raw.pop("limits"),
        lambda raw: raw.pop("sources"),
        lambda raw: raw["sources"]["rosfinmon"].__setitem__("name_match_min_similarity", 1.5),
        lambda raw: raw["score_thresholds"].__setitem__(
            "auto_reject_below", raw["score_thresholds"]["auto_approve_above"] + 100
        ),
    ],
    ids=[
        "typo_key_in_hard_stop_rule",
        "missing_limits_section",
        "missing_sources_section",
        "similarity_out_of_range",
        "score_thresholds_order_violated",
    ],
)
def test_broken_variant_of_real_config_fails_validation(tmp_path, mutate):
    """Не синтетический минимальный конфиг, а мутации реального dev.yaml:
    опечатка в ключе правила, отсутствующая обязательная секция, значение вне
    допустимого диапазона, нарушенный порядок порогов скора. Каждая должна
    падать на валидации схемой, а не проезжать тихо."""
    raw = _load_real_config_dict()
    mutate(raw)
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_ruleset_from_yaml(path)


def test_unknown_top_level_key_rejected():
    with pytest.raises(ValidationError):
        parse_ruleset(_base_config(unknown_section={"x": 1}))


def test_unknown_key_inside_rule_rejected():
    with pytest.raises(ValidationError):
        parse_ruleset(
            _base_config(
                hard_stops=[
                    {"id": "x", "field": "age", "op": "lt", "value": 21, "reason": "R", "oops": True}
                ]
            )
        )


def test_missing_required_section_rejected():
    config = _base_config()
    del config["limits"]
    with pytest.raises(ValidationError):
        parse_ruleset(config)


@pytest.mark.parametrize(
    "op,value",
    [
        ("between", [1]),  # нужно два элемента
        ("between", 5),  # не список
        ("in", 5),  # не список
        ("in", []),  # пустой список
        ("not_in", "x"),
    ],
)
def test_invalid_operator_value_shape_rejected(op, value):
    with pytest.raises(ValidationError):
        parse_ruleset(
            _base_config(hard_stops=[{"id": "x", "field": "f", "op": op, "value": value, "reason": "R"}])
        )


def test_is_null_rejects_value():
    with pytest.raises(ValidationError):
        parse_ruleset(
            _base_config(
                hard_stops=[{"id": "x", "field": "f", "op": "is_null", "value": 1, "reason": "R"}]
            )
        )


def test_non_null_operator_requires_value():
    with pytest.raises(ValidationError):
        parse_ruleset(_base_config(hard_stops=[{"id": "x", "field": "f", "op": "eq", "reason": "R"}]))


def test_score_thresholds_order_validated():
    with pytest.raises(ValidationError):
        parse_ruleset(
            _base_config(
                score_thresholds={"enabled": True, "auto_approve_above": 400, "auto_reject_below": 500}
            )
        )


def test_limits_amount_range_validated():
    with pytest.raises(ValidationError):
        parse_ruleset(
            _base_config(
                limits={
                    "amount_min": 500000,
                    "amount_max": 15000,
                    "term_min": 3,
                    "term_max": 24,
                    "pti_max": 0.6,
                    "declared_income_cap": 80000,
                }
            )
        )


def test_malformed_yaml_syntax_raises_clear_error(tmp_path):
    bad_yaml = tmp_path / "broken.yaml"
    bad_yaml.write_text("version: 1\nhard_stops: [this is not: valid", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_ruleset_from_yaml(bad_yaml)


def test_yaml_with_typo_key_raises_validation_error(tmp_path):
    config = _base_config()
    config["hard_stops"] = [{"id": "x", "fielld": "age", "op": "lt", "value": 21, "reason": "R"}]
    path = tmp_path / "typo.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_ruleset_from_yaml(path)

    assert "fielld" in str(exc_info.value) or "field" in str(exc_info.value)
