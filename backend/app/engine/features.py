"""Расчёт признаков. Версия набора признаков фиксируется рядом со снапшотом —
добавление признака не ломает старые снапшоты (ТЗ, «Ключевой принцип расширяемости»)."""

from datetime import date, datetime, timezone
from typing import Any

from app.engine.dates import compute_age
from app.engine.passport import (
    check_department_code_region,
    is_issue_date_implausible,
    is_passport_expired,
    validate_passport_format,
)

STAGE_A_FEATURE_SET_VERSION = "stage_a.v2"

__all__ = ["STAGE_A_FEATURE_SET_VERSION", "compute_age", "compute_stage_a_features"]


def compute_stage_a_features(
    ocr_fields: dict[str, Any],
    *,
    as_of: date | None = None,
    department_code_check_enabled: bool = False,
) -> dict[str, Any]:
    """Признаки стадии А из распознанных полей паспорта (T07 + T08): возраст,
    валидность формата и срока действия паспорта, правдоподобность даты выдачи,
    флаг по коду подразделения (только если включён в конфиге — справочник пока
    иллюстративный, см. открытые вопросы T08). Не распознанное/не нормализованное
    поле — признак просто отсутствует, ссылающееся на него правило не сработает
    (см. missing_fields)."""
    as_of = as_of or datetime.now(timezone.utc).date()

    def value(name: str) -> str | None:
        return (ocr_fields.get(name) or {}).get("value")

    features: dict[str, Any] = {}

    birth_date_raw = value("birth_date")
    birth_date = date.fromisoformat(birth_date_raw) if birth_date_raw else None
    if birth_date is not None:
        features["age"] = compute_age(birth_date, as_of)

    series = value("passport_series")
    number = value("passport_number")
    features["passport_format_valid"] = validate_passport_format(series, number)

    issue_date_raw = value("issue_date")
    issue_date = date.fromisoformat(issue_date_raw) if issue_date_raw else None
    if birth_date is not None and issue_date is not None:
        features["passport_expired"] = is_passport_expired(birth_date, issue_date, as_of)
        features["passport_issue_date_invalid"] = is_issue_date_implausible(birth_date, issue_date, as_of)

    if department_code_check_enabled:
        department_code = value("department_code")
        if series and department_code:
            region_flag = check_department_code_region(series, department_code)
            if region_flag is not None:
                features["passport_department_code_flag"] = not region_flag

    return features
