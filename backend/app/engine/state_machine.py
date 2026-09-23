"""Машина состояний заявки по диаграмме ТЗ («Жизненный цикл заявки»).
Недопустимый переход — исключение, а не молчаливый отказ (T07)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, StatusHistory

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "received": {"ocr"},
    "ocr": {"ocr_failed", "stage_a"},
    "ocr_failed": {"to_operator"},
    "stage_a": {"rejected_a", "to_operator"},
    "rejected_a": set(),
    "to_operator": {"stage_b"},
    "stage_b": {"rejected_b", "gray_zone", "approved", "to_operator"},
    "rejected_b": set(),
    "gray_zone": {"stage_b"},
    "approved": {"contracted", "expired"},
    "contracted": set(),
    "expired": set(),
}

ACTOR_TYPES = ("system", "employee", "client")


class InvalidTransitionError(Exception):
    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"недопустимый переход статуса {from_status!r} -> {to_status!r}")


def transition(
    session: AsyncSession,
    application: Application,
    to_status: str,
    *,
    actor_type: str,
    employee_id: UUID | None = None,
) -> None:
    """Меняет application.status и пишет status_history в той же транзакции
    (правило 1 CLAUDE.md). Не коммитит — коммит на совести вызывающего кода."""
    from_status = application.status
    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise InvalidTransitionError(from_status, to_status)

    application.status = to_status
    session.add(
        StatusHistory(
            application_id=application.id,
            from_status=from_status,
            to_status=to_status,
            actor_type=actor_type,
            employee_id=employee_id,
        )
    )
