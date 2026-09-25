"""БКИ — интерфейс и заглушка (T11, ТЗ «Стадия Б» -> «Запрос в БКИ»). Нейтральная
внутренняя структура отчёта (BkiReport) придумана нами, не подсмотрена у
конкретного бюро: договора нет, и неизвестно, с каким бюро он будет. Разбор
реального формата бюро — отдельный будущий слой (parse_bureau_report),
подключаемый когда бюро станет известно; сюда не входит.

is_configured=False (NotConfiguredBkiProvider) — честное «нет доступа», а не
техническая недоступность: вызывающий код должен пропустить сам вызов (и не
писать в bki_reports — платежа не было) и сразу уйти в исход on_unavailable."""

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.sources.base import CheckStatus, Subject


@dataclass(frozen=True)
class BkiContract:
    opened_at: date
    closed_at: date | None
    status: str  # active | closed
    monthly_payment: str  # Decimal как строка — сырой отчёт, разбор в app.engine.bki_features
    balance: str
    overdue_days_max: int
    subject: str  # "потребительский кредit" и т.п. — для будущей классификации по типу


@dataclass(frozen=True)
class BkiInquiry:
    requested_at: date
    requester: str


@dataclass(frozen=True)
class BkiReport:
    contracts: list[BkiContract] = field(default_factory=list)
    inquiries: list[BkiInquiry] = field(default_factory=list)
    score: int | None = None  # покупной балл бюро


@dataclass(frozen=True)
class BkiProviderResult:
    status: CheckStatus  # ok | no_data | source_unavailable | error
    report: BkiReport | None = None
    error_text: str | None = None


class BkiProvider(Protocol):
    is_configured: bool

    async def request_report(self, subject: Subject) -> BkiProviderResult:
        """Никогда не бросает исключение наружу (правило 4 CLAUDE.md, тот же
        принцип, что у SourceBase, хоть БКИ и не application-scoped источник)."""
        ...


class NotConfiguredBkiProvider:
    """Правило 1 из ваших уточнений T11: пока договора с бюро нет, провайдер
    «не настроен» — не техническая недоступность. Вызывающий код проверяет
    is_configured ДО попытки запроса и вообще не вызывает request_report."""

    is_configured = False

    async def request_report(self, subject: Subject) -> BkiProviderResult:
        return BkiProviderResult(
            status=CheckStatus.SOURCE_UNAVAILABLE, error_text="БКИ не подключено — нет договора с бюро"
        )


class FakeBkiProvider:
    """reports: {"Фамилия|Имя|ГГГГ-ММ-ДД": BkiReport} — детерминированный фейк
    для тестов. Нет записи для субъекта -> no_data (пустая КИ), не ошибка."""

    is_configured = True

    def __init__(
        self,
        *,
        reports: dict[str, BkiReport] | None = None,
        raise_unavailable: bool = False,
        raise_error: bool = False,
    ) -> None:
        self._reports = reports or {}
        self._raise_unavailable = raise_unavailable
        self._raise_error = raise_error
        self.requested_subjects: list[Subject] = []

    async def request_report(self, subject: Subject) -> BkiProviderResult:
        self.requested_subjects.append(subject)
        if self._raise_unavailable:
            return BkiProviderResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="fake bki timeout")
        if self._raise_error:
            return BkiProviderResult(status=CheckStatus.ERROR, error_text="fake bki error")

        key = f"{subject.last_name}|{subject.first_name}|{subject.birth_date.isoformat() if subject.birth_date else ''}"
        report = self._reports.get(key)
        if report is None:
            return BkiProviderResult(status=CheckStatus.NO_DATA, report=BkiReport())
        return BkiProviderResult(status=CheckStatus.OK, report=report)


# --- готовые синтетические отчёты для тестов/дев-режима -----------------------


def good_history_report() -> BkiReport:
    return BkiReport(
        contracts=[
            BkiContract(
                opened_at=date(2022, 3, 1), closed_at=date(2023, 9, 1), status="closed",
                monthly_payment="12000", balance="0", overdue_days_max=0, subject="Потребительский кредит",
            ),
        ],
        inquiries=[BkiInquiry(requested_at=date(2025, 1, 15), requester="Банк А")],
        score=780,
    )


def overdue_history_report() -> BkiReport:
    return BkiReport(
        contracts=[
            BkiContract(
                opened_at=date(2023, 1, 1), closed_at=None, status="active",
                monthly_payment="15000", balance="130000", overdue_days_max=95, subject="Кредит наличными",
            ),
        ],
        inquiries=[BkiInquiry(requested_at=date(2025, 8, 1), requester="Банк Б")],
        score=380,
    )


def overloaded_report() -> BkiReport:
    return BkiReport(
        contracts=[
            BkiContract(
                opened_at=date(2023, 1, 1), closed_at=None, status="active",
                monthly_payment="18000", balance="200000", overdue_days_max=10, subject="Кредит наличными",
            ),
            BkiContract(
                opened_at=date(2024, 2, 1), closed_at=None, status="active",
                monthly_payment="9000", balance="80000", overdue_days_max=0, subject="Микрозайм",
            ),
            BkiContract(
                opened_at=date(2024, 6, 1), closed_at=None, status="active",
                monthly_payment="7000", balance="60000", overdue_days_max=0, subject="Кредитная карта",
            ),
        ],
        inquiries=[
            BkiInquiry(requested_at=date(2025, 6, 1), requester="Банк В"),
            BkiInquiry(requested_at=date(2025, 7, 1), requester="МФО Г"),
            BkiInquiry(requested_at=date(2025, 8, 1), requester="Банк Д"),
        ],
        score=520,
    )


def empty_history_report() -> BkiReport:
    return BkiReport(contracts=[], inquiries=[], score=None)


# --- сериализация для report_raw (JSONB) и повторной сборки из кэша -----------


def serialize_report(report: BkiReport) -> dict:
    """dataclasses.asdict не годится напрямую — date не сериализуется в JSON
    молча (зависит от настройки движка), поэтому переводим явно, здесь же."""
    return {
        "contracts": [
            {
                "opened_at": c.opened_at.isoformat(),
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                "status": c.status,
                "monthly_payment": c.monthly_payment,
                "balance": c.balance,
                "overdue_days_max": c.overdue_days_max,
                "subject": c.subject,
            }
            for c in report.contracts
        ],
        "inquiries": [
            {"requested_at": i.requested_at.isoformat(), "requester": i.requester} for i in report.inquiries
        ],
        "score": report.score,
    }


def deserialize_report(raw: dict) -> BkiReport:
    return BkiReport(
        contracts=[
            BkiContract(
                opened_at=date.fromisoformat(c["opened_at"]),
                closed_at=date.fromisoformat(c["closed_at"]) if c.get("closed_at") else None,
                status=c["status"],
                monthly_payment=c["monthly_payment"],
                balance=c["balance"],
                overdue_days_max=c["overdue_days_max"],
                subject=c["subject"],
            )
            for c in (raw or {}).get("contracts", [])
        ],
        inquiries=[
            BkiInquiry(requested_at=date.fromisoformat(i["requested_at"]), requester=i["requester"])
            for i in (raw or {}).get("inquiries", [])
        ],
        score=(raw or {}).get("score"),
    )
