from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PartnerStatus(StrEnum):
    """Статусы в CRM — пока наши условные (config/status_mapping.yaml); список
    настоящих статусов CRM ещё не прислала."""

    NEW = "new"
    ON_CALL = "on_call"
    ON_REVIEW = "on_review"
    REJECTED = "rejected"
    MANUAL_DECISION = "manual_decision"
    APPROVED = "approved"


# --- то, что ОНА (заглушка) шлёт НАМ, имитируя заказчика -----------------------


class TriggerNewApplicationRequest(BaseModel):
    """POST /_test/trigger-new-application — fake_partner строит тело
    POST /new-order (NewOrderNotification) и сама его отправляет нам."""

    order_id: int | None = None  # None -> сгенерировать
    last_name: str = "Тестов"
    first_name: str = "Тест"
    middle_name: str | None = "Тестович"
    phone: str = "+79990000000"
    birth_date: date | None = None  # для сценариев, завязанных на возраст
    photo_mode: str = "base64"  # "base64" | "url" — какой способ передачи фото проверяем
    # T23: поля, которые рисуются на синтетических фото паспорта (серия,
    # номер, дата выдачи, код подразделения, адрес…) — поверх случайных
    # значений по умолчанию. Демо-сид (app.cli.seed_demo) передаёт те же
    # поля, что потом «распознаёт» OCR-фикстура, — картинка и распознанное
    # совпадают.
    passport: dict[str, str] | None = None


class TriggerReviewRequest(BaseModel):
    """POST /_test/trigger-review/{external_id} — тело POST /scoring."""

    goods_category_code: str = "household_appliances"
    goods_name: str = "Стиральная машина"
    goods_price: float = 60000.0
    down_payment: float = 6000.0
    term_months: int = 12
    tariff: str = "standard"
    # T21: ежемесячный платёж по рассрочке из их графика. None — посчитать
    # как настоящая система без наценки ((цена − взнос) / срок);
    # send_monthly_payment=False — не слать поле вовсе (наш запасной расчёт).
    monthly_payment: float | None = None
    send_monthly_payment: bool = True
    employment_type: str = "employed"
    # T23: для рейтинга клиента — место работы, должность, контакты
    # ([{"name", "phone", "relation": parent|sibling|relative|spouse|friend|colleague|other}]).
    employer: str | None = None
    position: str | None = None
    contacts: list[dict[str, Any]] | None = None
    declared_monthly_income: float = 90000.0
    declared_monthly_obligations: float = 0.0
    bki_consent: bool = True
    # Поручитель в их формате (ScoringGuarantor: lastName, firstName,
    # fatherName, phone, income, otherCreditPayments) + наши расширения
    # (birthDate, passportSeries, passportNumber, issuedDate, registrationAddress).
    guarantor: dict[str, Any] | None = None
    # Паспорт заявителя (расширение applicant) — когда распознавание выключено.
    applicant: dict[str, Any] | None = None
    # False — строго формат api.yml CRM, без наших расширений (goodsName,
    # tariff, monthlyPayment, employment, contacts, applicant).
    send_extensions: bool = True


class ConfigureRequest(BaseModel):
    """POST /_test/configure — режим ответа на наши POST (для тестов
    outbox-повторов и конфликта статуса)."""

    mode: str = "ok"  # "ok" | "conflict" | "unavailable"
    conflict_current_status: str = PartnerStatus.APPROVED.value
    conflict_reason: str | None = "оператор одобрил вручную"


class ManualStatusRequest(BaseModel):
    """T10/T18: имитирует оператора, вручную сменившего статус в обход нас."""

    status: str
