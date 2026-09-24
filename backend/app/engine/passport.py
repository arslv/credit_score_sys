"""Валидация паспорта — чистые функции, без БД и сети (ТЗ, «Стадия А, шаг 2»)."""

import re
from datetime import date

from app.engine.dates import compute_age

_SERIES_RE = re.compile(r"^\d{4}$")
_NUMBER_RE = re.compile(r"^\d{6}$")

# Возраст плановой замены паспорта РФ: выдан в 14, меняется в 20 и в 45 лет,
# дальше действует бессрочно.
_REPLACEMENT_AGES = (20, 45)
_MIN_ISSUE_AGE = 14


def validate_passport_format(series: str | None, number: str | None) -> bool:
    """Формат: серия — 4 цифры, номер — 6 цифр. Официального контрольного разряда
    у паспорта РФ нет — только структурная проверка."""
    if series is None or number is None:
        return False
    return bool(_SERIES_RE.match(series)) and bool(_NUMBER_RE.match(number))


def is_passport_expired(birth_date: date, issue_date: date, as_of: date) -> bool:
    """Срок действия паспорта (20 и 45 лет): если текущий возраст уже перешёл
    очередной порог замены, а паспорт выдан раньше этого порога — просрочен."""
    current_age = compute_age(birth_date, as_of)
    age_at_issue = compute_age(birth_date, issue_date)

    for threshold in sorted(_REPLACEMENT_AGES, reverse=True):
        if current_age >= threshold:
            return age_at_issue < threshold
    return False


def is_issue_date_implausible(birth_date: date, issue_date: date, as_of: date) -> bool:
    """Дата выдачи не может быть раньше 14-летия владельца (паспорт РФ выдаётся
    с 14 лет) и не может быть в будущем."""
    if issue_date > as_of:
        return True
    age_at_issue = compute_age(birth_date, issue_date)
    return age_at_issue < _MIN_ISSUE_AGE


def compute_blank_year(series: str, *, current_year: int) -> int | None:
    """Год бланка — цифры 3-4 серии. Формат в ходу с 1997 года: 97-99 -> 19xx
    (1997-1999), 00 до последних двух цифр текущего года -> 20xx (бланк уже мог
    быть выпущен). Всё, что осталось бы между ними, дало бы год позже текущего —
    бланк из будущего, структурно невалидная серия: возвращаем None, см.
    is_blank_year_future."""
    yy = int(series[2:4])
    if yy >= 97:
        return 1900 + yy
    if yy <= current_year % 100:
        return 2000 + yy
    return None


def is_blank_year_future(blank_year: int | None) -> bool:
    return blank_year is None


def is_issue_date_blank_year_mismatch(issue_date: date, blank_year: int, max_years_after: int) -> bool:
    """Дата выдачи не может быть раньше года бланка (бланк не мог использоваться
    до печати) и не должна отставать от него больше чем на N лет — бланки не
    лежат на складе десятилетиями."""
    return issue_date.year < blank_year or issue_date.year > blank_year + max_years_after


def check_department_code_region(
    passport_series: str, department_code: str, known_codes: frozenset[str] | set[str]
) -> bool | None:
    """Код подразделения — формат RRR-NNN, где RRR — регион (справочник
    hflabs/fms-unit, см. app.engine.department_codes и docs/registries.md).
    True — первые 2 цифры кода совпадают с регионом выдачи серии; False — не
    совпадают (флаг оператору, не автоотказ); None — код отсутствует в
    справочнике (features.passport_department_code_not_found, а не флаг
    несовпадения — с несуществующим кодом не с чем сравнивать)."""
    if department_code not in known_codes:
        return None
    return department_code[:2] == passport_series[:2]
