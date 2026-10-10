import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.schemas import PartnerStatus


@dataclass
class _ReceivedEvent:
    type: str
    event_id: str
    body: dict[str, Any]
    received_at: datetime


@dataclass
class _TrackedApplication:
    """Статус заявки, КАКИМ ЕГО ЗНАЕТ fake_partner — независимая от нас
    «правда», нужна только для симуляции конфликта (409): если его сменили
    через /_test/manual-status, следующая наша попытка записать решение с
    устаревшим prev_status не совпадёт — ровно так, как настоящая система
    заказчика отличила бы «оператор обогнал вас»."""

    external_id: str
    status: str = PartnerStatus.NEW.value
    received: list[_ReceivedEvent] = field(default_factory=list)


class Store:
    def __init__(self) -> None:
        self._apps: dict[str, _TrackedApplication] = {}
        self._photos: dict[str, bytes] = {}
        self.mode: str = "ok"  # ok | conflict | unavailable
        self.conflict_current_status: str = PartnerStatus.APPROVED.value
        self.conflict_reason: str | None = "оператор одобрил вручную"

    def reset(self) -> None:
        self._apps.clear()
        self._photos.clear()
        self.mode = "ok"
        self.conflict_current_status = PartnerStatus.APPROVED.value
        self.conflict_reason = "оператор одобрил вручную"

    def _app(self, external_id: str) -> _TrackedApplication:
        return self._apps.setdefault(external_id, _TrackedApplication(external_id=external_id))

    def receive(self, body: dict[str, Any]) -> None:
        """Запоминает то, что мы прислали на /partner/webhook, и — для
        type="decision" — обновляет «свой» статус этой заявки."""
        external_id = body["external_id"]
        app = self._app(external_id)
        app.received.append(
            _ReceivedEvent(
                type=body["type"], event_id=body["event_id"], body=body,
                received_at=datetime.now(timezone.utc),
            )
        )
        if body["type"] == "decision" and body.get("status"):
            app.status = body["status"]

    def received_items(self, external_id: str) -> list[dict[str, Any]]:
        app = self._apps.get(external_id)
        if app is None:
            return []
        return [
            {**e.body, "received_at": e.received_at.isoformat()}
            for e in app.received
        ]

    def current_status(self, external_id: str) -> str:
        return self._app(external_id).status

    def set_manual_status(self, external_id: str, status: str) -> None:
        self._app(external_id).status = status

    def store_photo(self, data: bytes) -> str:
        token = uuid.uuid4().hex
        self._photos[token] = data
        return token

    def get_photo(self, token: str) -> bytes | None:
        return self._photos.get(token)


store = Store()
