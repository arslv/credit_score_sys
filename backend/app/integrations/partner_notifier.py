"""Исходящая доставка в CRM — по их API (api.yml от CRM, «Входящие запросы» с
их стороны). Базовый адрес и токен — из env (PARTNER_OUTBOUND_URL,
PARTNER_OUTBOUND_TOKEN), см. docs/integration_contract.md.

- POST {base}/v1/integration/scoring/passport-result — распознанный паспорт,
  сразу после OCR.
- POST {base}/v1/integration/scoring/result — решение стадии А или Б:
  статус, балл, причина текстом.

Отдельного эндпоинта для комментариев у CRM нет — дополнительный текст
(предложение другого тарифа) уходит в причине того же решения."""

from decimal import Decimal
from typing import Any

import httpx

from app.integrations.base import PartnerBadRequestError, PartnerConflictError, PartnerUnavailableError

PASSPORT_RESULT_PATH = "/v1/integration/scoring/passport-result"
RESULT_PATH = "/v1/integration/scoring/result"

# Наши паспортные поля (app/ocr) → поля PassportRecognitionResult.
_PASSPORT_FIELD_NAMES = {
    "last_name": "lastName",
    "first_name": "firstName",
    "middle_name": "fatherName",
    "birth_date": "birthDate",
    "passport_series": "passportSeries",
    "passport_number": "passportNumber",
    "issued_by": "issuedBy",
    "issue_date": "issuedDate",
    "department_code": "divisionCode",
    "registration_address": "registrationAddress",
}


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {}


def order_id(external_id: str) -> int | str:
    """orderId в их API — число; external_id у нас — строка из их же orderId.
    Нечисловой (заявки, созданные не через /new-order, — тесты, старые
    данные) отдаём как есть."""
    return int(external_id) if external_id.isdigit() else external_id


def _field_value(value: Any) -> Any:
    # Распознанное поле — {"value": ..., "confidence": ...} или уже значение.
    if isinstance(value, dict):
        return value.get("value")
    return value


def passport_result_body(external_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"orderId": order_id(external_id)}
    for ours, theirs in _PASSPORT_FIELD_NAMES.items():
        body[theirs] = _field_value(fields.get(ours))
    return body


def result_body(external_id: str, status: str, score: Decimal | int | float | None, reason: str | None) -> dict[str, Any]:
    return {
        "orderId": order_id(external_id),
        "status": status,
        "score": int(score) if score is not None else None,
        "reason": reason,
    }


class PartnerNotifier:
    """Конструктор принимает базовый адрес/токен явно (не читает
    get_settings() сам) — тесты указывают на fake_partner, прод — на адрес
    CRM, подмена на уровне вызывающего кода."""

    def __init__(self, outbound_url: str, outbound_token: str, *, timeout_seconds: float = 10.0) -> None:
        self._base_url = outbound_url.rstrip("/")
        self._token = outbound_token
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> None:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json=body, headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise PartnerUnavailableError(str(exc)) from exc

        # В их API ответ только 200. 409 с {current_status, reason} — на
        # случай, если CRM начнёт сообщать, что оператор уже сменил статус
        # вручную (правило 12 CLAUDE.md); пока не придёт — не срабатывает.
        if response.status_code == 409:
            payload = _safe_json(response)
            raise PartnerConflictError(
                current_status=payload.get("current_status"), reason=payload.get("reason"),
            )
        if response.status_code >= 500:
            raise PartnerUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PartnerBadRequestError(f"HTTP {response.status_code}: {response.text[:500]}")

    async def write_status(
        self,
        external_id: str,
        expected_current_status: str | None,
        new_status: str,
        comment: str | None = None,
        *,
        score: Decimal | int | float | None = None,
    ) -> None:
        """Решение стадии А или Б. expected_current_status в их API не
        передаётся — оставлен в сигнатуре для журнала/конфликта."""
        await self._post(RESULT_PATH, result_body(external_id, new_status, score, comment))

    async def send_ocr_result(self, external_id: str, fields: dict[str, Any]) -> None:
        """Распознанный паспорт — сразу после OCR, не дожидаясь решения."""
        await self._post(PASSPORT_RESULT_PATH, passport_result_body(external_id, fields))

    async def resend(self, event_type: str, external_id: str, payload: dict[str, Any], event_id: str) -> None:
        """Повтор из outbox (app/tasks/partner_outbox.py) — payload сохранён
        при первой неудачной попытке."""
        if event_type == "decision":
            await self._post(
                RESULT_PATH, result_body(external_id, payload["status"], payload.get("score"), payload.get("reason"))
            )
        elif event_type == "ocr_result":
            await self._post(PASSPORT_RESULT_PATH, passport_result_body(external_id, payload["fields"]))
        else:
            # «comment» — из прежнего формата; у CRM такого входа нет.
            raise PartnerBadRequestError(f"событие {event_type!r} в API CRM не поддерживается")
