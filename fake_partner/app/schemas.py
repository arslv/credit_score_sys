from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PartnerStatus(StrEnum):
    """Статусы в системе заказчика (см. ТЗ, раздел «Соответствие статусов»)."""

    NEW = "new"  # Новая
    ON_CALL = "on_call"  # На звонок
    ON_REVIEW = "on_review"  # На рассмотрении (оператор нажал «Рассмотреть»)
    REJECTED = "rejected"  # Отказ
    MANUAL_DECISION = "manual_decision"  # Требует решения
    APPROVED = "approved"  # Одобрено


class ConsentInfo(BaseModel):
    given_at: datetime
    ip: str
    text_version: str


class ApplicationForm(BaseModel):
    last_name: str
    first_name: str
    middle_name: str | None = None
    phone: str
    goods_name: str
    merchant_name: str | None = None
    merchant_city: str | None = None
    goods_price: float | None = None
    down_payment: float | None = None
    term_months: int | None = None
    tariff: str | None = None
    consent_pd: ConsentInfo
    consent_bki: ConsentInfo | None = None


class TechnicalData(BaseModel):
    ip: str | None = None
    user_agent: str | None = None
    form_timings: dict[str, Any] | None = None
    utm: dict[str, Any] | None = None


class StageBFields(BaseModel):
    deal: dict[str, Any] | None = None
    employment: dict[str, Any] | None = None
    income: dict[str, Any] | None = None
    obligations: dict[str, Any] | None = None
    family: dict[str, Any] | None = None
    address: dict[str, Any] | None = None
    contact: dict[str, Any] | None = None
    guarantor: dict[str, Any] | None = None


class Comment(BaseModel):
    text: str
    created_at: datetime


class StatusHistoryEntry(BaseModel):
    from_status: PartnerStatus | None
    to_status: PartnerStatus
    changed_by: str  # system | operator
    changed_at: datetime


class ApplicationSummary(BaseModel):
    id: str
    status: PartnerStatus
    created_at: datetime
    updated_at: datetime
    last_name: str
    first_name: str
    goods_name: str


class ApplicationDetail(ApplicationSummary):
    form: ApplicationForm
    technical: TechnicalData
    photos: list[str]
    stage_b: StageBFields | None
    comments: list[Comment]
    status_history: list[StatusHistoryEntry]


class StatusChangeRequest(BaseModel):
    status: str
    comment: str | None = Field(default=None, max_length=2000)


class CommentRequest(BaseModel):
    text: str = Field(max_length=2000)


class CreateApplicationRequest(BaseModel):
    last_name: str = "Тестов"
    first_name: str = "Тест"
    middle_name: str | None = "Тестович"
    phone: str = "+79990000000"
    goods_name: str = "Стиральная машина"
    merchant_name: str | None = "ООО Ромашка"
    merchant_city: str | None = "Казань"
    goods_price: float | None = 60000.0
    down_payment: float | None = 6000.0
    term_months: int | None = 12
    tariff: str | None = "standard"
    consent_bki: bool = True
    birth_date: date | None = None  # для тестов сценариев, завязанных на возраст
