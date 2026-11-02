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
отчёта) — SpectrumTransport, HTTP-реализация — app/sources/spectrum_http.py;
фабрика собирает провайдер при BKI_PROVIDER_MODE=spectrum и заданных
SPECTRUM_USER/SPECTRUM_PASSWORD (docs/bki_spectrum.md).

Проверки подробного пакета — по BUREAU_BLOCKS (по описаниям блоков): ФССП
(P.0035), банкротство (P.0124), недействительный паспорт (P.0308), ИНН
(P.0031), самозанятый (P.0247), перечень террористов (P.0043).

Правило 4: провайдер никогда не бросает исключение наружу."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Protocol

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


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value not in (None, "") else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def _parse_fssp(block: dict) -> tuple[str, dict]:
    """P.0035.SSP.HIS — открытые исполнительные производства (isOpen по
    описанию блока: нет даты окончания и запись не «C»). Поиск по ФИО и дате
    рождения — бывают и чужие ИП (сказано в описании), поэтому это подсказка
    оператору, в решение не входит."""
    items = [i for i in block.get("items") or [] if isinstance(i, dict)]
    open_items = [i for i in items if i.get("isOpen") is True]
    open_count = block.get("openCount")
    if not isinstance(open_count, int):
        open_count = len(open_items)
    debt = sum((_money(i.get("debtBalanceTotal")) for i in open_items), Decimal("0"))
    parsed = {
        "open_count": open_count,
        "closed_count": block.get("closeCount"),
        "open_debt_total": str(debt),
        "limit_reached": bool(block.get("limitReached")),
        "open_items": [
            {
                "ep_number": i.get("epNumber"),
                "subject": i.get("weSubjTypeName"),
                "debt_total": str(_money(i.get("debtBalanceTotal"))),
                "started": i.get("weRisedate"),
                "office": i.get("bailiffOfficeTerotdel"),
            }
            for i in open_items
        ],
    }
    if open_count:
        parsed["summary"] = f"открытых производств: {open_count}, долг {debt:.2f} ₽"
    else:
        parsed["summary"] = "открытых производств нет"
    return ("hit" if open_count else "ok"), parsed


# Статусы должника по описанию P.0124.BNK.HIS-SHT: есть в реестре / был.
_BANKRUPTCY_LISTED = {"IS_LISTED", "WAS_LISTED"}


def _parse_bankruptcy(block: dict) -> tuple[str, dict]:
    """P.0124.BNK.HIS-SHT — банкротство по ИНН/СНИЛС (ЕФРСБ). Найдено, если
    человек есть или был в реестре должников (debtorStatus) или по нему есть
    дело; пустой блок — не найдено."""
    debtor_status = block.get("debtorStatus")
    case_numbers = [n for n in block.get("legalCaseNumbers") or [] if n]
    found = debtor_status in _BANKRUPTCY_LISTED or bool(case_numbers) or bool(block.get("actualCaseStatus"))
    parsed = {
        "debtor_status": debtor_status,
        "case_status": block.get("actualCaseStatus"),
        "case_closed": block.get("legalCaseClosed"),
        "procedure": block.get("procedureType"),
        "case_numbers": case_numbers,
        "court": block.get("courtName"),
        "discharged": block.get("isDischarged"),
        "last_message": block.get("lastMessageName"),
        "last_message_date": block.get("lastMessageDate"),
    }
    if found:
        state = "дело завершено" if block.get("legalCaseClosed") else "дело идёт"
        parsed["summary"] = f"есть в реестре банкротств ({state})"
    else:
        parsed["summary"] = "в реестре банкротств нет"
    return ("hit" if found else "ok"), parsed


def _parse_passport(block: dict) -> tuple[str, dict]:
    """P.0308.PAS.BAS-TRU — реестр недействительных паспортов."""
    passport_status = block.get("passport_status")
    parsed = {
        "passport_status": passport_status,
        "details": block.get("details") or None,
        "date_first_appearance": block.get("date_first_appearance"),
    }
    if block.get("expired") is True or passport_status == "INVALID":
        reason = f": {block['details']}" if block.get("details") else ""
        parsed["summary"] = f"паспорт в реестре недействительных{reason}"
        return "hit", parsed
    if passport_status == "VALID":
        parsed["summary"] = "паспорт действителен"
        return "ok", parsed
    parsed["summary"] = "паспорт не найден в базе"
    return "no_data", parsed


def _parse_inn(block: dict) -> tuple[str, dict]:
    """P.0031.INN.BAS — ИНН по паспорту (ФНС)."""
    inn = block.get("value") or None
    if block.get("status") == "FOUND" and inn:
        return "ok", {"inn": inn, "summary": f"ИНН {inn}"}
    return "no_data", {"inn": None, "summary": "ИНН не найден"}


def _parse_npd(block: dict) -> tuple[str, dict]:
    """P.0247.NPD.ACT — статус самозанятого (по ИНН) на дату запроса."""
    parsed = {"request_date": block.get("request_date"), "message": block.get("message")}
    if block.get("status") is True:
        parsed["summary"] = "самозанятый"
        return "ok", parsed
    parsed["summary"] = "не самозанятый"
    return "no_data", parsed


def _parse_extremists(block: dict) -> tuple[str, dict]:
    """P.0043.TER.HIS — перечень террористов и экстремистов (Росфинмониторинг).
    Поиск по ФИО (и дате рождения) с релевантностью 0–100: совпадение может
    быть однофамильцем — показываем релевантность и с чем совпало, решает
    человек (у нас — своя проверка по перечню)."""
    on_list = block.get("beOnTheList")
    found = block.get("found") is True
    active = on_list == "IS_ON_THE_LIST" or (found and block.get("isActive") is True)
    parsed = {
        "on_list": on_list,
        "relevance": block.get("relevance"),
        "relevance_description": block.get("relevance_description"),
        "matches": [
            {
                "relevance": i.get("relevance"),
                "relevance_description": i.get("relevance_description"),
                "matched_middle_name": i.get("matched_middle_name"),
                "matched_birth_date": i.get("matched_birth_date"),
                "is_active": i.get("isActive"),
                "raw": (i.get("item") or {}).get("rawString"),
            }
            for i in block.get("items") or []
            if isinstance(i, dict)
        ],
    }
    why = f" ({parsed['relevance_description'].lower()})" if parsed["relevance_description"] else ""
    if active:
        parsed["summary"] = f"есть совпадение в перечне, релевантность {parsed['relevance']}%{why}"
        return "hit", parsed
    if on_list == "WAS_ON_THE_LIST":
        parsed["summary"] = f"был в перечне, исключён{why}"
        return "ok", parsed
    parsed["summary"] = "в перечне нет"
    return "ok", parsed


@dataclass(frozen=True)
class BureauBlock:
    path: tuple[str, ...]  # путь от content («Структура отчёта» в описании блока)
    source: str  # наш источник, рядом с проверкой которого показываем
    source_ids: tuple[str, ...]  # data[].state.sources[]._id — ответил ли источник
    parse: Callable[[dict], tuple[str, dict]]


# Блоки подробного отчёта по их описаниям (кабинет → Документация → блок →
# «Структура отчёта»). Блок, которого здесь нет, в карточке не показывается.
BUREAU_BLOCKS: list[BureauBlock] = [
    BureauBlock(
        path=("check_person", "proceeding_v2", "executive_v2"), source="fssp",
        source_ids=("check_person/executive_proceeding_base",), parse=_parse_fssp,
    ),
    BureauBlock(
        path=("check_person", "bankruptcy_short"), source="fedresurs",
        source_ids=("check_person/fedresurs_short",), parse=_parse_bankruptcy,
    ),
    BureauBlock(
        path=("check_person", "expired_passport_v2"), source="mvd",
        source_ids=("check_person/expired_passport_v2",), parse=_parse_passport,
    ),
    BureauBlock(
        path=("check_person", "inn"), source="fns_inn",
        source_ids=("check_person/person_inn",), parse=_parse_inn,
    ),
    BureauBlock(
        path=("check_person", "statusnpd_nalog"), source="fns_npd",
        source_ids=("check_person/statusnpd_nalog",), parse=_parse_npd,
    ),
    BureauBlock(
        path=("check_person", "extremists"), source="rosfinmon",
        source_ids=("check_person/extremist_list",), parse=_parse_extremists,
    ),
]


def _dig(content: dict, path: tuple[str, ...]) -> Any:
    node: Any = content
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def extract_bureau_checks(response: dict) -> list[BureauCheck]:
    """Проверки из подробного отчёта. Источник не ответил (state не OK) —
    source_unavailable; источник ответил, а блока нет — «ничего не найдено»;
    источника в отчёте нет вовсе (не входит в пакет) — проверки нет."""
    checks: list[BureauCheck] = []
    for item in response.get("data") or []:
        content = item.get("content") or {}
        states = {
            s.get("_id"): str(s.get("state", "")).upper()
            for s in (item.get("state") or {}).get("sources") or []
            if isinstance(s, dict)
        }
        for spec in BUREAU_BLOCKS:
            block_key = ".".join(spec.path)
            source_states = [states[i] for i in spec.source_ids if i in states]
            if source_states and "OK" not in source_states:
                checks.append(BureauCheck(
                    source=spec.source, block=block_key, status="source_unavailable",
                    parsed={"summary": "источник бюро не ответил", "source_state": source_states[0]},
                ))
                continue
            block = _dig(content, spec.path)
            if not isinstance(block, dict):
                if not source_states:
                    continue
                block = {}  # источник ответил OK, записей нет
            status, parsed = spec.parse(block)
            checks.append(BureauCheck(source=spec.source, block=block_key, status=status, parsed=parsed))
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
