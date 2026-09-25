"""Соответствие наших исходов статусам в системе заказчика — в конфиге, не в коде
(ТЗ, «Интеграция» -> «Соответствие статусов»; задача T04). Комментарий оператору
(build_operator_comment, T11) тоже собирается здесь: человеческим языком, по
шаблонам reason_notes, без служебных кодов — «оператор не программист»."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from app.config import get_settings


class StatusMappingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_status: str
    comment_template: str


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _load_raw() -> dict[str, Any]:
    path = Path(get_settings().status_mapping_path)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def get_status_mapping(our_status: str) -> StatusMappingEntry:
    raw = _load_raw()
    try:
        entry = raw[our_status]
    except KeyError as exc:
        raise ValueError(
            f"нет соответствия для статуса {our_status!r} в {get_settings().status_mapping_path}"
        ) from exc
    return StatusMappingEntry.model_validate(entry)


def render_comment(mapping: StatusMappingEntry, **context: Any) -> str:
    return mapping.comment_template.format_map(_SafeDict(context))


def get_all_reason_notes() -> dict[str, str]:
    raw = _load_raw()
    return dict(raw.get("reason_notes") or {})


def _display_value(value: Any) -> Any:
    """Значения из TriggeredRule/контекста — для текста, не для решения:
    62000.0 -> 62000, 0.723 -> 0.72. Булевы — как есть (не число)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 2)
    return value


def _rule_context(rule: dict[str, Any]) -> dict[str, Any]:
    """actual/expected — уже готовые к показу (см. _display_value);
    actual_pct/expected_pct — те же значения в процентах, для правил вида
    ПДН (доля 0..1), доступны только когда исходное значение числовое."""
    context: dict[str, Any] = {
        "actual": _display_value(rule.get("actual")),
        "expected": _display_value(rule.get("expected")),
        "field": rule.get("field"),
        "id": rule.get("id"),
    }
    for key in ("actual", "expected"):
        raw = rule.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            context[f"{key}_pct"] = round(raw * 100)
    return context


def _render_rule_text(rule: dict[str, Any], notes: dict[str, str]) -> str:
    code = rule.get("reason") or rule.get("id")
    template = notes.get(code)
    if template:
        try:
            return template.format_map(_SafeDict(_rule_context(rule)))
        except (ValueError, IndexError):
            pass  # шаблон с опечаткой в спецификаторе формата — не роняем комментарий
    # Запасной вариант — название правила и значения, БЕЗ служебного кода
    # (оператор не программист: SANCTIONS_HIT ему ни о чём не говорит).
    return f"Сработало правило «{rule.get('id')}»: {rule.get('field')} = {_display_value(rule.get('actual'))} (порог {_display_value(rule.get('expected'))})."


def build_operator_comment(
    *,
    reason_code: str | None = None,
    triggered_rules: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Комментарий оператору по исходу — человеческим языком, никогда просто
    служебный код (T11, ТЗ: «сработавшие правила с фактическими значениями»).

    Сработавшие правила (triggered_rules — сериализованные TriggeredRule) —
    перечисляются ВСЕ, каждое по своему шаблону из reason_notes с подстановкой
    {actual}/{expected}/{actual_pct}/{expected_pct}. Нет правил, но есть
    reason_code (операционный исход вроде BKI_NOT_CONFIGURED/SCORE_BELOW_THRESHOLD)
    — берём его текст из reason_notes с подстановкой произвольного context,
    который передаёт вызывающий код (score, пороги и т.п.). Нет текста для
    кода — запасной вариант с названием правила/значениями (для правил) или
    нейтральной фразой без кода (для операционных исходов) — никогда пустая
    строка и никогда голый код.
    """
    notes = get_all_reason_notes()

    if triggered_rules:
        return " ".join(_render_rule_text(rule, notes) for rule in triggered_rules)

    if reason_code:
        template = notes.get(reason_code)
        if template:
            clean_context = {k: _display_value(v) for k, v in (context or {}).items()}
            return template.format_map(_SafeDict(clean_context))
        return "Требуется решение оператора — для этой причины пока нет готового текста, посмотрите детали заявки."

    return "Решение принято без сработавших правил — посмотрите детали заявки."
