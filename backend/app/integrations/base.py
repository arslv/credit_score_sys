from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


class PartnerUnavailableError(Exception):
    """Сетевая ошибка, таймаут или 5xx — временная проблема, стоит повторить попытку."""


class PartnerConflictError(Exception):
    """Статус заявки в их системе не совпал с ожидаемым — скорее всего,
    оператор изменил его вручную (правило 12 CLAUDE.md)."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected status {expected!r}, actual {actual!r}")


class PartnerBadRequestError(Exception):
    """Запрос отклонён их API как некорректный (4xx, кроме конфликта статуса)."""


@dataclass(frozen=True)
class PartnerApplicationSummary:
    external_id: str
    status: str
    updated_at: datetime


@dataclass(frozen=True)
class PartnerApplicationDetail:
    external_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_name: str
    first_name: str
    middle_name: str | None
    phone: str
    goods_name: str
    merchant_name: str | None
    merchant_city: str | None
    goods_price: Decimal | None
    down_payment: Decimal | None
    term_months: int | None
    tariff: str | None
    consent_pd_at: datetime
    consent_bki_at: datetime | None
    technical_ip: str | None
    technical_user_agent: str | None
    photo_kinds: list[str]
    raw: dict[str, Any]
    """Весь сырой ответ их API как пришёл — уходит в external_snapshots.payload_raw
    без изменений (правило 3 CLAUDE.md)."""


class PartnerClient(Protocol):
    """Единый интерфейс к системе заказчика. Реальная реализация появится, когда
    будет документация их API — весь остальной код зависит только от этого протокола."""

    async def list_applications(
        self, status: str, since: datetime | None = None
    ) -> list[PartnerApplicationSummary]: ...

    async def get_application(self, external_id: str) -> PartnerApplicationDetail: ...

    async def download_photo(self, external_id: str, kind: str) -> bytes: ...

    async def write_status(
        self,
        external_id: str,
        expected_current_status: str,
        new_status: str,
        comment: str | None = None,
    ) -> None: ...

    async def add_comment(self, external_id: str, text: str) -> None: ...
