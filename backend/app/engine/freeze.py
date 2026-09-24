"""Заморозка (T09): решение стадии А с freeze=true (сейчас — только санкционный
хит, см. dev.yaml sources.rosfinmon + hard_stop "sanctions") блокирует все
последующие заявки того же КЛИЕНТА без повторных проверок — по связи
applications.client_id (app.engine.clients), а не по паспорту напрямую: это
значит, что тот же человек с новым (переоформленным) паспортом тоже остаётся
заблокирован, потому что find_or_create_client находит его по ФИО+ДР и
привязывает к тому же client_id.

freeze в decisions (append-only, правило 1 CLAUDE.md) сейчас необратим — снять
заморозку нечем, ManualOverride с этим не связан (см. docs/TASKS.md, T12)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, Decision


async def find_active_sanctions_freeze(session: AsyncSession, *, client_id) -> str | None:
    """reason_code активной заморозки для этого клиента, если есть, иначе None."""
    stmt = (
        select(Decision.reason_code)
        .join(Application, Application.id == Decision.application_id)
        .where(Decision.freeze.is_(True), Application.client_id == client_id)
        .order_by(Decision.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()
