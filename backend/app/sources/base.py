"""Общий интерфейс для всех внешних источников (ТЗ, «Источники данных»; правило 4
CLAUDE.md). Источник никогда не бросает исключение наружу — только статус."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Protocol


class CheckStatus(StrEnum):
    OK = "ok"
    HIT = "hit"
    NO_DATA = "no_data"
    SOURCE_UNAVAILABLE = "source_unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class Subject:
    """Данные субъекта проверки (заявитель или поручитель). Разные источники
    используют разные подмножества полей.

    passport_issue_date/registration_address — добавлены в T16 для fns_inn
    (дата выдачи паспорта, обязательное поле формы) и для сопоставления
    региона ФССП (app/engine/fssp_regions.py — только по адресу регистрации,
    НЕ по серии паспорта, ваше явное указание)."""

    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    birth_date: date | None = None
    passport_series: str | None = None
    passport_number: str | None = None
    passport_issue_date: date | None = None
    registration_address: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    request: dict[str, Any] | None = None
    response_raw: dict[str, Any] | None = None
    """Ответ источника как пришёл — до разбора (правило 3 CLAUDE.md)."""
    parsed: dict[str, Any] | None = None
    """Разобранный результат: детали найденного совпадения и т.п."""
    error_text: str | None = None


_SUBJECT_FIELD_LABELS = {
    "last_name": "фамилия",
    "first_name": "имя",
    "birth_date": "дата рождения",
    "passport_series": "серия паспорта",
    "passport_number": "номер паспорта",
    "passport_issue_date": "дата выдачи паспорта",
}


def missing_subject_field_error(subject: Subject, fields: tuple[str, ...]) -> str | None:
    """T16, живой прогон: источник не должен стучаться на внешний сайт с
    заведомо пустым обязательным полем — ни ради экономии реального лимита
    запросов сайта, ни чтобы не путать настоящую недоступность сайта с нашей
    же недостачей данных (найденный баг: пустой Subject из-за нераспакованной
    обёртки OcrResult.fields ушёл на is-go.fssp.gov.ru как валидный запрос и
    дал обманчивую ошибку «не нашли капчу в ответе сайта»).

    Один общий хелпер для fns.py/fssp.py (и Fake-, и Http-реализаций — ваше
    явное указание, п.3: фейки обязаны падать на пустом Subject так же, как
    настоящие источники, иначе этот класс багов тесты не поймают). Возвращает
    готовый текст на первое отсутствующее поле (по порядку fields), иначе None."""
    for field in fields:
        if not getattr(subject, field, None):
            label = _SUBJECT_FIELD_LABELS.get(field, field)
            return f"Нет данных для проверки: не заполнено «{label}»"
    return None


class SourceBase(Protocol):
    """Реализуют mvd.py, rosfinmon.py, blacklist.py (T08), bki.py (T11),
    fns.py (T09/T16: fns_inn/fns_npd/fns_egrip, три источника в одном файле)
    — однократный запрос/ответ. Федресурс убран из конвейера целиком (T16,
    никогда не был настоящей интеграцией — всегда source_unavailable).

    ФССП (T16) — больше не этот протокол: капчу вводит человек, поэтому
    app/sources/fssp.py не реализует SourceBase (нет единого check()) —
    отдельные search()/submit_code(), вызываемые только из app/api/fssp.py по
    действию оператора, никогда из автоматического конвейера."""

    source_name: str  # mvd | rosfinmon | fns_inn | fns_npd | fns_egrip | fssp | bki | blacklist

    async def check(self, subject: Subject) -> CheckResult:
        """Никогда не бросает исключение наружу — при сбое возвращает
        CheckResult(status=SOURCE_UNAVAILABLE или ERROR)."""
        ...
