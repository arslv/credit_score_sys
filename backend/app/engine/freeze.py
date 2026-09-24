"""Заморозка (T09): решение стадии А с freeze=true (сейчас — только санкционный
хит, см. dev.yaml sources.rosfinmon + hard_stop "sanctions") блокирует все
последующие заявки того же паспорта без повторных проверок.

Идентичность клиента определяется точным совпадением серии+номера паспорта —
таблицы clients ещё нет (T02 не реализован), а нечёткое ФИО-сопоставление
(как у Росфинмониторинга) для блокировки слишком рискованно: здесь нужен
точный, а не вероятностный матч. См. отчёт T09 про ограничение и про то, что
freeze в decisions (append-only, правило 1 CLAUDE.md) сейчас необратим — снять
заморозку нечем, ManualOverride с этим не связан."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Decision, OcrResult


async def find_active_sanctions_freeze(
    session: AsyncSession, *, passport_series: str, passport_number: str
) -> str | None:
    """reason_code активной заморозки для этого паспорта, если есть, иначе None."""
    stmt = (
        select(Decision.reason_code)
        .join(OcrResult, OcrResult.application_id == Decision.application_id)
        .where(
            Decision.freeze.is_(True),
            OcrResult.fields[("fields", "passport_series", "value")].astext == passport_series,
            OcrResult.fields[("fields", "passport_number", "value")].astext == passport_number,
        )
        .order_by(Decision.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()
