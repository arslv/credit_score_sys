"""МВД, недействительные паспорта — локальная копия (ТЗ, «Файловые реестры»)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvalidPassport
from app.sources.base import CheckResult, CheckStatus, Subject


class MvdSource:
    source_name = "mvd"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self, subject: Subject) -> CheckResult:
        if not subject.passport_series or not subject.passport_number:
            return CheckResult(status=CheckStatus.NO_DATA)

        request = {"passport_series": subject.passport_series, "passport_number": subject.passport_number}
        try:
            row = (
                await self._session.execute(
                    select(InvalidPassport).where(
                        InvalidPassport.passport_series == subject.passport_series,
                        InvalidPassport.passport_number == subject.passport_number,
                    )
                )
            ).scalar_one_or_none()
        except Exception as exc:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, request=request, error_text=str(exc))

        if row is None:
            return CheckResult(status=CheckStatus.OK, request=request)

        return CheckResult(
            status=CheckStatus.HIT,
            request=request,
            parsed={"loaded_at": row.loaded_at.isoformat()},
        )
