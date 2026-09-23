import pytest

from app.ocr.normalize import (
    normalize_date,
    normalize_department_code,
    normalize_passport_number,
    normalize_passport_series,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1990-01-15", "1990-01-15"),
        ("15.01.1990", "1990-01-15"),
        ("15/01/1990", "1990-01-15"),
        ("15-01-1990", "1990-01-15"),
        (" 15.01.1990 ", "1990-01-15"),
        (None, None),
        ("", None),
        ("не дата", None),
        ("32.13.1990", None),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234", "1234"),
        ("12 34", "1234"),
        ("12-34", "1234"),
        (None, None),
        ("", None),
        ("123", None),
        ("12345", None),
    ],
)
def test_normalize_passport_series(raw, expected):
    assert normalize_passport_series(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("567890", "567890"),
        ("567 890", "567890"),
        ("567-890", "567890"),
        (None, None),
        ("", None),
        ("12345", None),
        ("1234567", None),
    ],
)
def test_normalize_passport_number(raw, expected):
    assert normalize_passport_number(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("770001", "770-001"),
        ("770-001", "770-001"),
        ("770 001", "770-001"),
        (None, None),
        ("", None),
        ("77001", None),
    ],
)
def test_normalize_department_code(raw, expected):
    assert normalize_department_code(raw) == expected
