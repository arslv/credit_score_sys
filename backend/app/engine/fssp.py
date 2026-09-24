"""Разбор производств ФССП по типу и сумме (T09, ТЗ «ФССП»): штрафы ГИБДД —
шум, взыскание по кредитному договору — сильный сигнал. Чистые функции, без
БД и сети — классификация полностью в конфиге (sources.fssp.classification),
правится по живым данным без изменения кода."""

from decimal import Decimal
from typing import Any

from app.engine.schema import FsspCategory, FsspClassificationRule


def classify_production(subject_text: str, rules: list[FsspClassificationRule]) -> FsspCategory:
    """Первое правило, чья подстрока (без учёта регистра) встречается в
    описании производства, побеждает. Ничего не подошло -> OTHER (не шум, но
    и не кредитный долг — по умолчанию не теряется, просто не усиливает сигнал)."""
    lowered = subject_text.lower()
    for rule in rules:
        if any(pattern.lower() in lowered for pattern in rule.match):
            return rule.category
    return FsspCategory.OTHER


def summarize_productions(
    productions: list[dict[str, Any]], rules: list[FsspClassificationRule]
) -> dict[str, Any]:
    """productions — сырой разбор ответа ФССП: [{"subject": str, "amount": ...}].
    credit_debt_amount/total_amount/count намеренно не учитывают NOISE — иначе
    накопленные мелкие штрафы ГИБДД шумели бы в признаке для ручного разбора
    (features.fssp_total_amount, soft_rule fssp_minor).

    Суммируем в Decimal (правило 5 CLAUDE.md), но признаки для движка правил —
    float: features.* сравниваются оператором gt/between с числами из YAML
    (см. app.engine.rules._apply_operator) и хранятся в JSONB feature_snapshots,
    для которого Decimal не сериализуется без потерь по умолчанию. Сырые суммы
    из ответа ФССП остаются Decimal-строками в checks.response_raw/parsed
    (правило 3) — это единственное место, где точность обязательна."""
    credit_debt_amount = Decimal("0")
    total_amount = Decimal("0")
    count = 0

    for production in productions:
        category = classify_production(str(production.get("subject", "")), rules)
        if category is FsspCategory.NOISE:
            continue
        amount = Decimal(str(production.get("amount", 0)))
        total_amount += amount
        count += 1
        if category is FsspCategory.CREDIT_DEBT:
            credit_debt_amount += amount

    return {
        "fssp_credit_debt_amount": float(credit_debt_amount),
        "fssp_total_amount": float(total_amount),
        "fssp_count": count,
    }
