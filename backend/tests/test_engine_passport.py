from datetime import date, timedelta

import pytest

from app.engine.passport import (
    check_department_code_region,
    compute_blank_year,
    is_blank_year_future,
    is_issue_date_blank_year_mismatch,
    is_issue_date_implausible,
    is_passport_expired,
    validate_passport_format,
)

_BIRTH_DATE = date(2000, 6, 15)


def _nth_birthday(age: int) -> date:
    return _BIRTH_DATE.replace(year=_BIRTH_DATE.year + age)


# --- срок действия: ровно на 20/45-летие, за день до и через день после ----------


@pytest.mark.parametrize("threshold_age", [20, 45])
def test_passport_expired_exactly_on_threshold_birthday(threshold_age):
    as_of = _nth_birthday(threshold_age)
    issue_date = _nth_birthday(threshold_age - 5)  # выдан задолго до порога, не переоформлен
    assert is_passport_expired(_BIRTH_DATE, issue_date, as_of) is True


@pytest.mark.parametrize("threshold_age", [20, 45])
def test_passport_not_expired_day_before_threshold_birthday(threshold_age):
    as_of = _nth_birthday(threshold_age) - timedelta(days=1)
    issue_date = _nth_birthday(threshold_age - 5)
    assert is_passport_expired(_BIRTH_DATE, issue_date, as_of) is False


@pytest.mark.parametrize("threshold_age", [20, 45])
def test_passport_still_expired_day_after_threshold_birthday(threshold_age):
    as_of = _nth_birthday(threshold_age) + timedelta(days=1)
    issue_date = _nth_birthday(threshold_age - 5)
    assert is_passport_expired(_BIRTH_DATE, issue_date, as_of) is True


@pytest.mark.parametrize("threshold_age", [20, 45])
def test_passport_not_expired_if_reissued_exactly_on_threshold_birthday(threshold_age):
    as_of = _nth_birthday(threshold_age)
    issue_date = as_of  # переоформлен точно в день наступления порога
    assert is_passport_expired(_BIRTH_DATE, issue_date, as_of) is False


# --- правдоподобность даты выдачи: раньше 14-летия или в будущем -----------------


def test_issue_date_implausible_before_14th_birthday():
    issue_date = _nth_birthday(14) - timedelta(days=1)
    assert is_issue_date_implausible(_BIRTH_DATE, issue_date, date(2026, 1, 1)) is True


def test_issue_date_plausible_exactly_on_14th_birthday():
    issue_date = _nth_birthday(14)
    assert is_issue_date_implausible(_BIRTH_DATE, issue_date, date(2026, 1, 1)) is False


def test_issue_date_implausible_in_future():
    as_of = date(2026, 1, 1)
    issue_date = as_of + timedelta(days=1)
    assert is_issue_date_implausible(_BIRTH_DATE, issue_date, as_of) is True


def test_issue_date_plausible_normal_case():
    issue_date = _nth_birthday(20)
    assert is_issue_date_implausible(_BIRTH_DATE, issue_date, date(2026, 1, 1)) is False


# --- формат -------------------------------------------------------------------


@pytest.mark.parametrize(
    "series,number,expected",
    [
        ("1234", "567890", True),
        (None, "567890", False),
        ("1234", None, False),
        ("123", "567890", False),  # серия короче
        ("1234", "56789", False),  # номер короче
        ("12a4", "567890", False),  # не цифры
    ],
)
def test_validate_passport_format(series, number, expected):
    assert validate_passport_format(series, number) is expected


# --- код подразделения против региона (синтетический справочник, T08 п.3) -------

_KNOWN_CODES = frozenset({"770-001", "010-001"})  # Москва, Адыгея — см. config/reference/fms_unit.csv


def test_department_code_region_match():
    assert check_department_code_region("7712", "770-001", _KNOWN_CODES) is True


def test_department_code_region_mismatch():
    assert check_department_code_region("7712", "010-001", _KNOWN_CODES) is False


def test_department_code_not_in_reference_returns_none():
    """Код отсутствует в справочнике — не флаг несовпадения, отдельная отметка
    "код не найден" (features.passport_department_code_not_found)."""
    assert check_department_code_region("7712", "999-999", _KNOWN_CODES) is None


# --- год бланка: цифры 3-4 серии --------------------------------------------

_CURRENT_YEAR = 2026  # фиксируем "текущий год" в тестах вместо часов


@pytest.mark.parametrize(
    "series,expected_year",
    [
        ("4514", 2014),  # 00..current_yy -> 20xx
        ("4597", 1997),  # 97-99 -> 19xx
        ("4599", 1999),
        ("4500", 2000),
        ("4526", 2026),  # ровно текущий год — ещё валиден
    ],
)
def test_compute_blank_year(series, expected_year):
    assert compute_blank_year(series, current_year=_CURRENT_YEAR) == expected_year


def test_compute_blank_year_one_past_current_is_future():
    # yy=27 при текущем годе 2026 не попадает ни в 97-99, ни в 00-26 -> бланк из будущего
    assert compute_blank_year("4527", current_year=_CURRENT_YEAR) is None


def test_compute_blank_year_96_is_future_when_current_year_is_2026():
    assert compute_blank_year("4596", current_year=_CURRENT_YEAR) is None


def test_is_blank_year_future_true_for_none():
    assert is_blank_year_future(None) is True


def test_is_blank_year_future_false_for_computed_year():
    assert is_blank_year_future(2014) is False


# --- согласованность даты выдачи с годом бланка (флаг, не автоотказ) ------------


def test_issue_before_blank_year_is_mismatch():
    assert is_issue_date_blank_year_mismatch(date(2013, 12, 31), blank_year=2014, max_years_after=3) is True


def test_issue_in_blank_year_is_not_mismatch():
    assert is_issue_date_blank_year_mismatch(date(2014, 1, 1), blank_year=2014, max_years_after=3) is False


def test_issue_exactly_n_years_after_blank_year_is_not_mismatch():
    assert is_issue_date_blank_year_mismatch(date(2017, 12, 31), blank_year=2014, max_years_after=3) is False


def test_issue_more_than_n_years_after_blank_year_is_mismatch():
    assert is_issue_date_blank_year_mismatch(date(2018, 1, 1), blank_year=2014, max_years_after=3) is True
