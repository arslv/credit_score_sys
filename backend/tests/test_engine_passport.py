from datetime import date, timedelta

import pytest

from app.engine.passport import (
    check_department_code_region,
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


# --- код подразделения против региона (иллюстративный справочник) ---------------


def test_department_code_region_match():
    assert check_department_code_region("7712", "770-001") is True


def test_department_code_region_mismatch():
    assert check_department_code_region("7712", "999-001") is False


def test_department_code_region_unknown_series_skips_flag():
    assert check_department_code_region("9999", "999-001") is None
