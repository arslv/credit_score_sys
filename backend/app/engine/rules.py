"""Движок правил. Чистая функция: признаки (словарь) + конфиг -> решение.
Без БД и сети (T06, ТЗ «Конфиг правил и пороги»)."""

import re
from dataclasses import dataclass, field
from typing import Any

from app.engine.schema import MatrixEntry, Operator, RuleDef, RulesetConfig

_MISSING = object()

_OUTCOME_REJECT = "reject"
_OUTCOME_MANUAL_REVIEW = "manual_review"
_OUTCOME_APPROVE = "approve"


class RuleEvaluationError(Exception):
    """Правило нашло значение поля, но не смогло применить к нему оператор
    (несовместимый тип) — ошибка, а не тихий пропуск."""


def get_field(data: dict[str, Any], path: str) -> Any:
    """Доступ к полю по пути через точку (checks.mvd.status, features.pti).
    Возвращает _MISSING, если путь не резолвится."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _apply_operator(op: Operator, actual: Any, expected: Any, *, rule_id: str, field_path: str) -> bool:
    try:
        if op is Operator.EQ:
            return actual == expected
        if op is Operator.NE:
            return actual != expected
        if op is Operator.LT:
            return actual < expected
        if op is Operator.LTE:
            return actual <= expected
        if op is Operator.GT:
            return actual > expected
        if op is Operator.GTE:
            return actual >= expected
        if op is Operator.BETWEEN:
            lo, hi = expected
            return lo <= actual <= hi
        if op is Operator.IN:
            return actual in expected
        if op is Operator.NOT_IN:
            return actual not in expected
        if op is Operator.IS_NULL:
            return actual is None
    except TypeError as exc:
        raise RuleEvaluationError(
            f"rule {rule_id!r}: не удалось сравнить {field_path!r}={actual!r} с {expected!r} ({op}): {exc}"
        ) from exc
    raise AssertionError(f"unknown operator {op!r}")  # pragma: no cover


@dataclass(frozen=True)
class TriggeredRule:
    id: str
    field: str
    op: Operator
    expected: Any
    actual: Any
    reason: str | None
    action: str | None
    freeze: bool


def evaluate_rule_list(rules: list[RuleDef], features: dict[str, Any]) -> tuple[list[TriggeredRule], set[str]]:
    """Общий примитив для hard_stops / stage_b_pre_bki / soft_rules.

    Отсутствующее поле (или явный null вне op=is_null) — правило не срабатывает
    и попадает в missing_fields, а не роняет оценку.
    """
    triggered: list[TriggeredRule] = []
    missing: set[str] = set()

    for rule in rules:
        actual = get_field(features, rule.field)
        if actual is _MISSING or (actual is None and rule.op is not Operator.IS_NULL):
            missing.add(rule.field)
            continue

        if _apply_operator(rule.op, actual, rule.value, rule_id=rule.id, field_path=rule.field):
            triggered.append(
                TriggeredRule(
                    id=rule.id,
                    field=rule.field,
                    op=rule.op,
                    expected=rule.value,
                    actual=actual,
                    reason=rule.reason,
                    action=rule.action,
                    freeze=rule.freeze,
                )
            )

    return triggered, missing


@dataclass(frozen=True)
class StopOutcome:
    """Результат hard_stops / stage_b_pre_bki: список условий вида «стоп-фактор»."""

    triggered_rules: list[TriggeredRule]
    missing_fields: list[str]

    @property
    def matched(self) -> bool:
        return bool(self.triggered_rules)

    @property
    def reason_code(self) -> str | None:
        if not self.triggered_rules:
            return None
        first = self.triggered_rules[0]
        return first.reason or first.id

    @property
    def freeze(self) -> bool:
        return any(r.freeze for r in self.triggered_rules)


def evaluate_hard_stops(config: RulesetConfig, features: dict[str, Any]) -> StopOutcome:
    triggered, missing = evaluate_rule_list(config.hard_stops, features)
    return StopOutcome(triggered_rules=triggered, missing_fields=sorted(missing))


def evaluate_stage_b_pre_bki(config: RulesetConfig, features: dict[str, Any]) -> StopOutcome:
    triggered, missing = evaluate_rule_list(config.stage_b_pre_bki, features)
    return StopOutcome(triggered_rules=triggered, missing_fields=sorted(missing))


_MATRIX_KEY_SUFFIXES: tuple[tuple[str, Operator], ...] = (
    ("_gte", Operator.GTE),
    ("_lte", Operator.LTE),
    ("_gt", Operator.GT),
    ("_lt", Operator.LT),
    ("_ne", Operator.NE),
)


def parse_matrix_key(key: str) -> tuple[str, Operator]:
    """`amount_gt` -> (field="amount", op=gt). Без известного суффикса -> op=eq."""
    for suffix, op in _MATRIX_KEY_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)], op
    return key, Operator.EQ


def _matrix_entry_matches(entry: MatrixEntry, features: dict[str, Any], missing: set[str]) -> bool:
    for key, expected in entry.when.items():
        field_path, op = parse_matrix_key(key)
        actual = get_field(features, field_path)
        if actual is _MISSING or (actual is None and op is not Operator.IS_NULL):
            missing.add(field_path)
            return False
        if not _apply_operator(op, actual, expected, rule_id=f"matrix:{key}", field_path=field_path):
            return False
    return True


def resolve_matrix_overrides(
    config: RulesetConfig, features: dict[str, Any]
) -> tuple[dict[str, Any], set[str]]:
    """Базовые score_thresholds + переопределения подошедших строк матрицы,
    применённые по порядку (поздние строки переопределяют более ранние)."""
    resolved: dict[str, Any] = config.score_thresholds.model_dump()
    missing: set[str] = set()
    for entry in config.matrix:
        if _matrix_entry_matches(entry, features, missing):
            resolved.update(entry.override)
    return resolved, missing


@dataclass(frozen=True)
class FinancialOutcome:
    outcome: str  # approve | reject | manual_review
    reason_code: str | None
    triggered_rules: list[TriggeredRule]
    missing_fields: list[str]
    thresholds_used: dict[str, Any] = field(default_factory=dict)
    score: Any = None


def evaluate_financial(config: RulesetConfig, features: dict[str, Any]) -> FinancialOutcome:
    """Стадия Б после БКИ: soft_rules -> порог скора (с учётом матрицы)."""
    triggered, missing = evaluate_rule_list(config.soft_rules, features)
    if triggered:
        first = triggered[0]
        return FinancialOutcome(
            outcome=_OUTCOME_MANUAL_REVIEW,
            reason_code=first.reason or first.id,
            triggered_rules=triggered,
            missing_fields=sorted(missing),
        )

    thresholds, matrix_missing = resolve_matrix_overrides(config, features)
    missing |= matrix_missing

    score = get_field(features, "score")
    if score is _MISSING or score is None:
        missing.add("score")
        return FinancialOutcome(
            outcome=_OUTCOME_MANUAL_REVIEW,
            reason_code="SCORE_MISSING",
            triggered_rules=[],
            missing_fields=sorted(missing),
            thresholds_used=thresholds,
        )

    if not thresholds.get("enabled", False):
        return FinancialOutcome(
            outcome=_OUTCOME_MANUAL_REVIEW,
            reason_code="SCORE_THRESHOLDS_DISABLED",
            triggered_rules=[],
            missing_fields=sorted(missing),
            thresholds_used=thresholds,
            score=score,
        )

    if score >= thresholds["auto_approve_above"]:
        outcome = _OUTCOME_APPROVE
        reason_code = None
    elif score < thresholds["auto_reject_below"]:
        outcome = _OUTCOME_REJECT
        reason_code = "SCORE_BELOW_THRESHOLD"
    else:
        outcome = _OUTCOME_MANUAL_REVIEW
        reason_code = "SCORE_GRAY_ZONE"

    return FinancialOutcome(
        outcome=outcome,
        reason_code=reason_code,
        triggered_rules=[],
        missing_fields=sorted(missing),
        thresholds_used=thresholds,
        score=score,
    )
