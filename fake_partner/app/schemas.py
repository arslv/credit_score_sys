from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PartnerStatus(StrEnum):
    """Статусы в системе заказчика (ТЗ, «Соответствие статусов») — вокабуляр
    не менялся в T18, меняется только способ доставки (вебхук, не опрос)."""

    NEW = "new"
    ON_CALL = "on_call"
    ON_REVIEW = "on_review"
    REJECTED = "rejected"
    MANUAL_DECISION = "manual_decision"
    APPROVED = "approved"


# --- то, что ОНА (заглушка) шлёт НАМ, имитируя заказчика -----------------------


class TriggerNewApplicationRequest(BaseModel):
    """POST /_test/trigger-new-application — fake_partner строит тело вебхука
    .../api/v1/partner/applications и сама его отправляет нам."""

    external_id: str | None = None  # None -> сгенерировать
    last_name: str = "Тестов"
    first_name: str = "Тест"
    middle_name: str | None = "Тестович"
    phone: str = "+79990000000"
    birth_date: date | None = None  # для сценариев, завязанных на возраст
    photo_mode: str = "base64"  # "base64" | "url" — какой способ передачи фото проверяем


class TriggerReviewRequest(BaseModel):
    """POST /_test/trigger-review/{external_id} — тело вебхука .../review."""

    status: str = PartnerStatus.ON_REVIEW.value
    goods_category_code: str = "household_appliances"
    goods_name: str = "Стиральная машина"
    goods_price: float = 60000.0
    down_payment: float = 6000.0
    term_months: int = 12
    tariff: str = "standard"
    employment_type: str = "employed"
    declared_monthly_income: float = 90000.0
    declared_monthly_obligations: float = 0.0
    bki_consent: bool = True
    guarantor: dict[str, Any] | None = None


class ConfigureRequest(BaseModel):
    """POST /_test/configure — режим ответа на /partner/webhook (для тестов
    outbox-повторов и конфликта статуса)."""

    mode: str = "ok"  # "ok" | "conflict" | "unavailable"
    conflict_current_status: str = PartnerStatus.APPROVED.value
    conflict_reason: str | None = "оператор одобрил вручную"


class ManualStatusRequest(BaseModel):
    """T10/T18: имитирует оператора, вручную сменившего статус в обход нас —
    следующая наша попытка записать решение с prev_status, который мы знали
    ДО этого, получит 409 от /partner/webhook."""

    status: str
