"""БКИ через Спектрум Дату (B2B-API, кабинет «Проверить»). Два пакета —
две кнопки в карточке (bki.report_types в правилах):

- «Обычный» — пакет «Кредитный рейтинг»: блок P.0191.CRR.HIS «Кредитный
  рейтинг с детализацией» — балл 0–999, просрочки, число кредитов, средний
  платёж, обращения в бюро. Суммы и количества — диапазонами («3-4»,
  «100000.0-149999.99»).
- «Подробный» — пакет «Проверка соискателя — Премиум»: тот же кредитный
  рейтинг и ~30 проверок (ФССП, банкротство, паспорт, ИНН, перечни…). Их
  результаты показываются в карточке рядом с нашими проверками, с пометкой
  «от БКИ», и в решение не входят (решение владельца: наши проверки — как
  обычно).

Ответ отчёта: data[].content.check_person.<блок> (структура — из описания
блока в кабинете). Транспорт (адрес API, авторизация, заказ и получение
отчёта) — SpectrumTransport; HTTP-реализация — когда будет описание API из
кабинета. До того провайдер не собирается фабрикой (app/sources/factory.py).

Правило 4: провайдер никогда не бросает исключение наружу."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.sources.base import CheckStatus, Subject
from app.sources.bki import BkiProviderResult, BkiReport

# Справочник Overdue (описание блока P.0191): код и текст → нижняя граница
# просрочки в днях. None — «нет информации».
_OVERDUE_DAYS: dict[str, int | None] = {
    "0": None, "нет информации/неизвестные результаты": None,
    "1": 0, "оплата без просрочек": 0,
    "2": 1, "просрочка от 1 до 7 дней": 1,
    "3": 8, "просрочка от 8 до 29 дней": 8,
    "4": 1, "просрочка от 1 до 29 дней": 1,
    "5": 30, "просрочка от 30 до 59 дней": 30,
    "6": 60, "просрочка от 60 до 89 дней": 60,
    "7": 90, "просрочка от 90 до 119 дней": 90,
    "8": 120, "просрочка от 120 до 179 дней": 120,
    "9": 180, "просрочка более 180 дней": 180,
}

_RANGE_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(?:-\s*(-?\d+(?:[.,]\d+)?))?\s*$")


def parse_range(value: Any) -> tuple[Decimal, Decimal] | None:
    """«100000.0-149999.99» → (100000.0, 149999.99); «5» → (5, 5); пусто или
    непонятно → None."""
    if value is None:
        return None
    match = _RANGE_RE.match(str(value))
    if not match:
        return None
    try:
        low = Decimal(match.group(1).replace(",", "."))
        high = Decimal((match.group(2) or match.group(1)).replace(",", "."))
    except InvalidOperation:
        return None
    return (low, high) if low <= high else (high, low)


def pick(bounds: tuple[Decimal, Decimal] | None, bound: str) -> Decimal | None:
    """Какую границу диапазона брать — bki.range_bound в правилах."""
    if bounds is None:
        return None
    low, high = bounds
    if bound == "lower":
        return low
    if bound == "middle":
        return (low + high) / 2
    return high


def overdue_days(value: Any) -> int | None:
    if value is None:
        return None
    return _OVERDUE_DAYS.get(str(value).strip().lower())


def _credit_history(response: dict) -> tuple[dict | None, dict]:
    """(блок credit_history или None, content целиком) из ответа отчёта."""
    for item in response.get("data") or []:
        content = item.get("content") or {}
        person = content.get("check_person") or {}
        if "credit_history" in person:
            return person["credit_history"], content
    return None, {}


def parse_credit_rating(response: dict) -> tuple[CheckStatus, BkiReport]:
    """Ответ отчёта → (статус, BkiReport с summary). Сведений нет (у человека
    нет кредитной истории) — no_data с пустым отчётом, как у прочих бюро."""
    history, _content = _credit_history(response)
    if history is None:
        return CheckStatus.ERROR, BkiReport()
    meta = history.get("src_meta") or {}
    if str(meta.get("state", "OK")).upper() != "OK":
        return CheckStatus.SOURCE_UNAVAILABLE, BkiReport()

    score_raw = (history.get("score") or {}).get("value")
    try:
        score = int(score_raw) if score_raw not in (None, "") else None
    except ValueError:
        score = None
    if score == 0:  # 0 — «нет информации по кредитному рейтингу»
        score = None

    if not any(key in history for key in ("bills", "credits", "activeCreditsDelays", "treatmentsInBanks", "score")):
        return CheckStatus.NO_DATA, BkiReport()

    bills = history.get("bills") or {}
    credits = history.get("credits") or {}
    delays = history.get("activeCreditsDelays") or {}
    inquiries = history.get("treatmentsInBanks") or {}
    active = bills.get("activeAccounts") or (history.get("summary") or {}).get("activeAccounts") or {}

    def as_range(value: Any) -> list[str] | None:
        bounds = parse_range(value)
        return [str(bounds[0]), str(bounds[1])] if bounds else None

    summary = {
        "source": "spectrum_credit_rating",
        "active_accounts": as_range(active.get("borrowerCount")),
        "guarantor_accounts": as_range(active.get("guarantorCount")),
        "average_payment": as_range(bills.get("averagePayment")),
        "active_credits_limit": as_range(bills.get("activeCreditsSumLimit")),
        "overdue_sum": as_range(delays.get("sumDelay")),
        "current_overdue_days": overdue_days(delays.get("maxCurrentDelay")),
        "max_overdue_days": overdue_days(delays.get("maxHistoryDelay")),
        "bad_debt": bool(credits.get("hasBadDebtOrPassedToCollection")),
        "open_credit_types": [t.get("type") for t in credits.get("creditTypeInfosOpen") or []],
        "inquiries_7d": inquiries.get("forLast7Days"),
        "inquiries_14d": inquiries.get("forLast14Days"),
        "inquiries_1mo": inquiries.get("forLastMonth"),
        "inquiries_all": inquiries.get("all"),
        "last_repayment_date": bills.get("lastRepaymentDate"),
        "score_description": (history.get("score") or {}).get("description"),
    }
    return CheckStatus.OK, BkiReport(score=score, summary=summary)


@dataclass(frozen=True)
class BureauCheck:
    """Проверка из подробного отчёта бюро (ФССП, банкротство, паспорт…) —
    показывается рядом с нашей, в решение не входит."""

    source: str  # как у наших проверок: fssp | fedresurs | mvd | fns_inn | fns_npd | rosfinmon
    block: str  # ключ блока в отчёте
    status: str  # ok | hit | no_data | source_unavailable | error
    parsed: dict = field(default_factory=dict)


# Ключ блока в content.check_person → наш источник. Разбор статуса каждого
# блока — по его описанию в кабинете (структура отчёта); пока описаний нет,
# статус — только по src_meta.state («проверка прошла / не прошла»), без
# «найдено / не найдено».
BUREAU_BLOCKS: dict[str, str] = {}


def extract_bureau_checks(response: dict) -> list[BureauCheck]:
    _history, content = _credit_history(response)
    person = content.get("check_person") or {}
    checks: list[BureauCheck] = []
    for key, source in BUREAU_BLOCKS.items():
        block = person.get(key)
        if not isinstance(block, dict):
            continue
        state = str((block.get("src_meta") or {}).get("state", "OK")).upper()
        checks.append(BureauCheck(
            source=source, block=key, status="ok" if state == "OK" else "source_unavailable",
            parsed={k: v for k, v in block.items() if k != "src_meta"},
        ))
    return checks


def request_data(subject: Subject) -> dict[str, str]:
    """Идентификаторы субъекта для запроса (data в теле запроса отчёта)."""

    def ru_date(value: date | None) -> str | None:
        return value.strftime("%d.%m.%Y") if value else None

    data = {
        "last_name": subject.last_name or "",
        "first_name": subject.first_name or "",
        "patronymic": subject.middle_name,
        "birth": ru_date(subject.birth_date),
        "passport": f"{subject.passport_series or ''} {subject.passport_number or ''}".strip(),
        "passport_date": ru_date(subject.passport_issue_date),
    }
    return {k: v for k, v in data.items() if v}


class SpectrumTransport(Protocol):
    async def fetch_report(self, report_type_uid: str, data: dict[str, str]) -> dict:
        """Заказать отчёт пакета report_type_uid по data и дождаться готового
        ответа (data[].content…). Сбой — исключение; провайдер его ловит."""
        ...


class SpectrumDataBkiProvider:
    is_configured = True

    def __init__(self, transport: SpectrumTransport) -> None:
        self._transport = transport

    async def request_report(self, subject: Subject, *, report_code: str | None = None) -> BkiProviderResult:
        if not report_code:
            return BkiProviderResult(
                status=CheckStatus.ERROR,
                error_text="не задан пакет Спектрум Даты (bki.report_types.*.provider_code в правилах)",
            )
        if not (subject.last_name and subject.first_name and subject.passport_series and subject.passport_number):
            return BkiProviderResult(status=CheckStatus.ERROR, error_text="не хватает ФИО или паспорта для запроса")
        try:
            response = await self._transport.fetch_report(report_code, request_data(subject))
        except Exception as exc:  # правило 4 — наружу не бросаем
            return BkiProviderResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text=f"Спектрум Дата: {exc}")
        status, report = parse_credit_rating(response)
        return BkiProviderResult(
            status=status, report=report, raw=response, extra_checks=extract_bureau_checks(response),
            error_text=None if status in (CheckStatus.OK, CheckStatus.NO_DATA) else "отчёт без кредитного рейтинга",
        )
