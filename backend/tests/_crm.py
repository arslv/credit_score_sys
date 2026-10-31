"""Тела запросов CRM к нам по их API (api.yml): /new-order и /scoring.
Общие для тестов, которые ведут заявку через настоящий вход."""

import uuid
from typing import Any

from app.config import get_settings

NEW_ORDER_PATH = "/api/v1/crm/new-order"
SCORING_PATH = "/api/v1/crm/scoring"

# PNG-сигнатура (T21: только JPEG/PNG) + условное содержимое.
PHOTO_MAIN_B64 = "iVBORw0KGgpmYWtlLXBhc3Nwb3J0LW1haW4="
PHOTO_REG_B64 = "iVBORw0KGgpmYWtlLXBhc3Nwb3J0LXJlZw=="


def new_order_id() -> str:
    """orderId у CRM — число; external_id у нас — та же строка."""
    return str(uuid.uuid4().int % 10**12)


def crm_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().partner_inbound_token}"}


def new_order_body(
    external_id: str, *, last_name: str = "Тестов", first_name: str = "Клиент",
    middle_name: str | None = "Клиентович", phone: str = "+70000000000", photos: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orderId": int(external_id),
        "lastName": last_name,
        "firstName": first_name,
        "fatherName": middle_name,
        "phone": phone,
        "passportPhotos": photos if photos is not None else [PHOTO_MAIN_B64, PHOTO_REG_B64],
    }


def scoring_body(
    external_id: str, *, category: str = "household_appliances", price: float = 60000, down: float = 6000,
    term: int = 12, income: float | None = 90000, obligations: float | None = 0, bki_consent: bool = True,
    guarantors: list[dict[str, Any]] | None = None, **extensions: Any,
) -> dict[str, Any]:
    """extensions — наши необязательные поля (employment, contacts, applicant,
    goodsName, tariff, monthlyPayment), в их формате имён."""
    body: dict[str, Any] = {
        "orderId": int(external_id),
        "productCategory": category,
        "price": price,
        "firstPayment": down,
        "period": term,
        "clientIncome": income,
        "otherCreditPayments": obligations,
        "bkiConsent": bki_consent,
        "guarantors": guarantors or [],
    }
    body.update(extensions)
    return {k: v for k, v in body.items() if v is not None}


_GUARANTOR_NAMES = {
    "last_name": "lastName", "first_name": "firstName", "middle_name": "fatherName", "phone": "phone",
    "birth_date": "birthDate", "passport_series": "passportSeries", "passport_number": "passportNumber",
    "issue_date": "issuedDate", "registration_address": "registrationAddress",
}
_APPLICANT_NAMES = {
    "last_name": "lastName", "first_name": "firstName", "middle_name": "fatherName", "birth_date": "birthDate",
    "birth_place": "birthPlace", "gender": "gender", "passport_series": "passportSeries",
    "passport_number": "passportNumber", "issue_date": "issuedDate", "issued_by": "issuedBy",
    "department_code": "divisionCode", "registration_address": "registrationAddress",
}


def _renamed(values: dict[str, Any] | None, names: dict[str, str]) -> dict[str, Any] | None:
    if not values:
        return None
    return {names[k]: v for k, v in values.items() if k in names and v is not None}


def scoring_body_from_internal(
    external_id: str, payload: dict[str, Any], *, bki_consent: bool = True,
    applicant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Внутренний снимок анкеты стадии Б (deal/employment/income/obligations/
    guarantor/contacts — как его пишет вход /scoring) → тело /scoring CRM.
    Тесты стадии Б описывают анкету во внутреннем виде; так она проходит
    через настоящий вход."""
    deal = payload.get("deal") or {}
    income = payload.get("income") or {}
    obligations = payload.get("obligations") or {}

    def number(value: Any) -> int | float | None:
        # Число как есть: целое остаётся целым ("6500", не "6500.0").
        if value is None or isinstance(value, (int, float)):
            return value
        return int(value) if str(value).isdigit() else float(value)

    guarantor = _renamed(payload.get("guarantor"), _GUARANTOR_NAMES)
    body: dict[str, Any] = {
        "orderId": int(external_id),
        "productCategory": deal.get("goods_category_code"),
        "price": number(deal.get("goods_price")),
        "firstPayment": number(deal.get("down_payment")),
        "period": deal.get("term_months"),
        "clientIncome": number(income.get("declared_monthly")),
        "otherCreditPayments": number(obligations.get("monthly_payment_total")),
        "bkiConsent": bki_consent,
        "guarantors": [guarantor] if guarantor else [],
        "goodsName": deal.get("goods_name"),
        "tariff": deal.get("tariff"),
        "monthlyPayment": number(deal.get("monthly_payment")),
        "employment": payload.get("employment") or None,
        "contacts": payload.get("contacts") or None,
        "applicant": _renamed(applicant, _APPLICANT_NAMES),
    }
    return {k: v for k, v in body.items() if v is not None}
