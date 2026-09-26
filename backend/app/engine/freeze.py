"""Заморозка (T09): решение стадии А с freeze=true (сейчас — только санкционный
хит, см. dev.yaml sources.rosfinmon + hard_stop "sanctions") блокирует все
последующие заявки того же КЛИЕНТА без повторных проверок — по связи
applications.client_id (app.engine.clients), а не по паспорту напрямую: это
значит, что тот же человек с новым (переоформленным) паспортом тоже остаётся
заблокирован, потому что find_or_create_client находит его по ФИО+ДР и
привязывает к тому же client_id.

freeze в decisions (append-only, правило 1 CLAUDE.md) сам по себе необратим —
снимает его compliance (T12) отдельной append-only записью в freeze_lifts, не
трогая decisions. Активность заморозки — сравнение по времени: если снятие
позже последнего freeze-решения, блокировки нет; новый хит после снятия
(новая запись в decisions) снова активирует блокировку."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, Decision, FreezeLift


async def find_active_sanctions_freeze(session: AsyncSession, *, client_id) -> str | None:
    """reason_code активной заморозки для этого клиента, если есть, иначе None."""
    stmt = (
        select(Decision.reason_code, Decision.created_at)
        .join(Application, Application.id == Decision.application_id)
        .where(Decision.freeze.is_(True), Application.client_id == client_id)
        .order_by(Decision.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    reason_code, freeze_at = row

    last_lift_at = (
        await session.execute(
            select(FreezeLift.created_at)
            .where(FreezeLift.client_id == client_id)
            .order_by(FreezeLift.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if last_lift_at is not None and last_lift_at >= freeze_at:
        return None
    return reason_code
