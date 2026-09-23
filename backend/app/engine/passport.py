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

# ПРИМЕР структуры для флага «код подразделения против региона выдачи» — НЕ настоящий
# справочник МВД. Ключ — первые 2 цифры серии (регион выдачи), значение — известные
# префиксы кода подразделения для этого региона. Для серий вне таблицы флаг не
# ставится (нет данных — не гадаем), таблицу нужно заменить реальной перед продакшеном.
_KNOWN_DEPARTMENT_PREFIXES: dict[str, set[str]] = {
    "77": {"770", "771", "772"},
    "78": {"780", "781", "782"},
}


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


def check_department_code_region(passport_series: str, department_code: str) -> bool | None:
    """True — код подразделения похож на код для региона выдачи серии;
    False — не похож (флаг оператору); None — по региону нет данных, не флагуем."""
    region_prefix = passport_series[:2]
    known_prefixes = _KNOWN_DEPARTMENT_PREFIXES.get(region_prefix)
    if known_prefixes is None:
        return None
    dept_prefix = department_code.split("-")[0]
    return dept_prefix in known_prefixes
