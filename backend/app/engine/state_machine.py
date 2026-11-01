"""Машина состояний заявки по диаграмме ТЗ («Жизненный цикл заявки»).
Недопустимый переход — исключение, а не молчаливый отказ (T07)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, StatusHistory

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "received": {"ocr", "awaiting_data"},
    # Распознавание выключено: паспортные данные приходят от CRM в /review,
    # сразу стадия А по ним (app.engine.pipeline.run_stage_a_from_crm_data).
    "awaiting_data": {"stage_a"},
    "ocr": {"ocr_failed", "stage_a"},
    "ocr_failed": {"to_operator"},
    "stage_a": {"rejected_a", "to_operator"},
    "rejected_a": set(),
    "to_operator": {"stage_b"},
    "stage_b": {"rejected_b", "gray_zone", "approved", "to_operator", "awaiting_bki"},
    # Ждёт заказа отчёта БКИ сотрудником; заказ (или новая анкета от CRM) —
    # снова stage_b.
    "awaiting_bki": {"stage_b"},
    "rejected_b": set(),
    # awaiting_bki — повторный запрос отчёта БКИ, если прошлый не получен
    # (app/api/bki.py, кнопка в карточке).
    "gray_zone": {"stage_b", "awaiting_bki"},
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
