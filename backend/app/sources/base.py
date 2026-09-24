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
    используют разные подмножества полей."""

    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    birth_date: date | None = None
    passport_series: str | None = None
    passport_number: str | None = None
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


class SourceBase(Protocol):
    """Реализуют mvd.py, rosfinmon.py, blacklist.py (T08), fedresurs.py, fns.py
    (T09), позже bki.py (T11) — однократный запрос/ответ. Исключение — fssp.py
    (T09): официальное API асинхронное (запрос -> ID -> опрос), не укладывается
    в один check(), поэтому там отдельный протокол FsspSource (submit/poll) с
    тем же принципом "никогда не бросает исключение наружу" для poll(), но не
    для submit() — см. app/sources/fssp.py."""

    source_name: str  # mvd | rosfinmon | fssp | fedresurs | fns | bki | blacklist

    async def check(self, subject: Subject) -> CheckResult:
        """Никогда не бросает исключение наружу — при сбое возвращает
        CheckResult(status=SOURCE_UNAVAILABLE или ERROR)."""
        ...
