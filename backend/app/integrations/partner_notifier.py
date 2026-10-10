"""T18: исходящая доставка на ИХ эндпоинт (URL и токен из env — docs/integration_contract.md
задаёт форму тела и ответов). Раньше называлось FakePartnerClient и опрашивало
их API по расписанию — опроса больше нет, заявки приходят вебхуками
(app/api/partner_webhooks.py), а этот класс делает только исходящий POST."""

import uuid
from decimal import Decimal
from typing import Any

import httpx

from app.integrations.base import PartnerBadRequestError, PartnerConflictError, PartnerUnavailableError


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {}


class PartnerNotifier:
    """Один HTTP-клиент на одно уведомление их эндпоинта (ТЗ T18: «их эндпоинт»,
    единственное число) — три вида события различаются полем "type" в теле,
    не разными URL. Конструктор принимает URL/токен явно (не читает
    get_settings() сам) — тесты указывают на fake_partner, прод — на
    настоящий адрес заказчика, подмена на уровне вызывающего кода."""

    def __init__(self, outbound_url: str, outbound_token: str, *, timeout_seconds: float = 10.0) -> None:
        self._url = outbound_url
        self._token = outbound_token
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, body: dict[str, Any]) -> None:
        try:
            response = await self._client.post(
                self._url, json=body, headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise PartnerUnavailableError(str(exc)) from exc

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
        event_id: str | None = None,
    ) -> None:
        """type="decision" — после стадии А и Б (ТЗ T18: статус, балл, причина
        текстом, prev_status). score — только когда есть (стадия А его не знает)."""
        await self._post(
            {
                "type": "decision",
                "external_id": external_id,
                "event_id": event_id or str(uuid.uuid4()),
                "prev_status": expected_current_status,
                "status": new_status,
                "score": int(score) if score is not None else None,
                "reason": comment,
            }
        )

    async def add_comment(self, external_id: str, text: str, *, event_id: str | None = None) -> None:
        """type="comment" — дополнительные замечания (например, предложение
        альтернативного тарифа), отдельно от смены статуса."""
        await self._post(
            {
                "type": "comment",
                "external_id": external_id,
                "event_id": event_id or str(uuid.uuid4()),
                "text": text,
            }
        )

    async def resend(
        self, event_type: str, external_id: str, payload: dict[str, Any], event_id: str,
    ) -> None:
        """Повтор из outbox (app/tasks/partner_outbox.py) — тело уже собрано
        при первой (неудачной) попытке, здесь только оборачиваем type/external_id/
        event_id. event_id стабильный (id строки outbox) — их же идемпотентность
        по event_id защищает от двойной обработки, если наш POST дошёл, а наш
        процесс не успел увидеть ответ (упал между отправкой и обновлением строки)."""
        await self._post({"type": event_type, "external_id": external_id, "event_id": event_id, **payload})

    async def send_ocr_result(
        self, external_id: str, fields: dict[str, Any], *, event_id: str | None = None
    ) -> None:
        """type="ocr_result" — распознанные паспортные поля, сразу после OCR
        (ТЗ T18, не дожидаясь решения стадии А)."""
        await self._post(
            {
                "type": "ocr_result",
                "external_id": external_id,
                "event_id": event_id or str(uuid.uuid4()),
                "fields": fields,
            }
        )
