"""Расчёт признаков. Версия набора признаков фиксируется рядом со снапшотом —
добавление признака не ломает старые снапшоты (ТЗ, «Ключевой принцип расширяемости»)."""

from datetime import date, datetime, timezone
from typing import Any

from app.engine.dates import compute_age
from app.engine.passport import (
    check_department_code_region,
    compute_blank_year,
    is_blank_year_future,
    is_issue_date_blank_year_mismatch,
    is_issue_date_implausible,
    is_passport_expired,
    validate_passport_format,
)

STAGE_A_FEATURE_SET_VERSION = "stage_a.v4"

__all__ = ["STAGE_A_FEATURE_SET_VERSION", "compute_age", "compute_stage_a_features"]


def compute_stage_a_features(
    ocr_fields: dict[str, Any],
    *,
    as_of: date | None = None,
    department_code_check_enabled: bool = False,
    known_department_codes: frozenset[str] = frozenset(),
    blank_year_max_issue_offset_years: int = 3,
) -> dict[str, Any]:
    """Признаки стадии А из распознанных полей паспорта (T07 + T08): возраст,
    валидность формата и срока действия паспорта, правдоподобность даты выдачи,
    год бланка и его согласованность с датой выдачи, флаг по коду подразделения
    (только если включён в конфиге; known_department_codes — справочник
    hflabs/fms-unit, см. app.engine.department_codes). Не распознанное/не
    нормализованное поле — признак просто отсутствует, ссылающееся на него
    правило не сработает (см. missing_fields)."""
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
    format_valid = validate_passport_format(series, number)
    features["passport_format_valid"] = format_valid

    issue_date_raw = value("issue_date")
    issue_date = date.fromisoformat(issue_date_raw) if issue_date_raw else None
    if birth_date is not None and issue_date is not None:
        features["passport_expired"] = is_passport_expired(birth_date, issue_date, as_of)
        features["passport_issue_date_invalid"] = is_issue_date_implausible(birth_date, issue_date, as_of)

    if format_valid:
        blank_year = compute_blank_year(series, current_year=as_of.year)
        features["passport_blank_year"] = blank_year
        features["passport_blank_year_future"] = is_blank_year_future(blank_year)
        if blank_year is not None and issue_date is not None:
            features["passport_blank_year_mismatch"] = is_issue_date_blank_year_mismatch(
                issue_date, blank_year, blank_year_max_issue_offset_years
            )

    if department_code_check_enabled:
        department_code = value("department_code")
        if series and department_code:
            region_match = check_department_code_region(series, department_code, known_department_codes)
            if region_match is None:
                features["passport_department_code_not_found"] = True
            else:
                features["passport_department_code_flag"] = not region_match

    return features
