"""Чёрный список организации — своя таблица (ТЗ, «Стадия А, шаг 2»).
Совпадение по паспорту или телефону — точное: список маленький и curated вручную,
нечёткий поиск здесь не заявлен (в отличие от Росфинмониторинга)."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Blacklist
from app.sources.base import CheckResult, CheckStatus, Subject


class BlacklistSource:
    source_name = "blacklist"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self, subject: Subject) -> CheckResult:
        conditions = []
        if subject.passport_series and subject.passport_number:
            conditions.append(
                (Blacklist.passport_series == subject.passport_series)
                & (Blacklist.passport_number == subject.passport_number)
            )
        if subject.phone:
            conditions.append(Blacklist.phone == subject.phone)

        if not conditions:
            return CheckResult(status=CheckStatus.NO_DATA)

        request = {"passport": bool(subject.passport_series), "phone": bool(subject.phone)}
        try:
            row = (
                await self._session.execute(
                    select(Blacklist).where(Blacklist.active.is_(True), or_(*conditions)).limit(1)
                )
            ).scalar_one_or_none()
        except Exception as exc:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, request=request, error_text=str(exc))

        if row is None:
            return CheckResult(status=CheckStatus.OK, request=request)

        return CheckResult(
            status=CheckStatus.HIT,
            request=request,
            parsed={"reason": row.reason, "added_at": row.added_at.isoformat()},
        )
