"""Текущий поручитель заявки.

При каждом «Рассмотреть» с поручителем в анкете он записывается новой
строкой guarantors (прежние не трогаем). Действующий — последний записанный:
по нему проверяется ФССП перед одобрением, его показывает карточка. Раньше
брался «первый попавшийся» без сортировки — после повторного «Рассмотреть»
это мог оказаться прежний поручитель."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Guarantor


async def current_guarantor(session: AsyncSession, application_id: UUID) -> Guarantor | None:
    return (
        await session.execute(
            select(Guarantor)
            .where(Guarantor.application_id == application_id)
            .order_by(Guarantor.created_at.desc(), Guarantor.id.desc())
            .limit(1)
        )
    ).scalars().first()
