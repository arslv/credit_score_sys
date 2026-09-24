"""Запуск источника с записью результата в checks (ТЗ, «Проверки и сырые ответы»)."""

import time
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Check
from app.sources.base import CheckResult, CheckStatus, SourceBase, Subject

logger = structlog.get_logger()


async def execute_check(source: SourceBase, subject: Subject) -> tuple[CheckResult, datetime, datetime, int]:
    """Вызывает source.check() и меряет время, без записи в БД. Отдельно от
    record_check — нужно, когда несколько источников идут параллельно через
    asyncio.gather (T09): AsyncSession не потокобезопасна для конкурентной
    записи, поэтому I/O делаем параллельно, а запись — последовательно."""
    started_at = datetime.now(timezone.utc)
    start = time.monotonic()

    try:
        result = await source.check(subject)
        if not isinstance(result, CheckResult):
            raise TypeError(f"{source.source_name} вернул {type(result).__name__}, ожидался CheckResult")
    except Exception as exc:  # защита от источника, нарушившего контракт — падением
        # или мусором вместо CheckResult (правило 4: наружу это уйти не должно)
        logger.exception("source_raised_unexpectedly", source=source.source_name)
        result = CheckResult(status=CheckStatus.ERROR, error_text=str(exc))

    duration_ms = int((time.monotonic() - start) * 1000)
    finished_at = datetime.now(timezone.utc)
    return result, started_at, finished_at, duration_ms


async def record_check(
    session: AsyncSession,
    *,
    application_id: UUID,
    subject_type: str,
    subject_id: UUID | None,
    source_name: str,
    result: CheckResult,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
) -> Check:
    check = Check(
        application_id=application_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source=source_name,
        status=result.status.value,
        request=result.request,
        response_raw=result.response_raw,
        parsed=result.parsed,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        error_text=result.error_text,
    )
    session.add(check)
    await session.flush()

    logger.info(
        "source_check_completed",
        source=source_name,
        status=result.status.value,
        duration_ms=duration_ms,
    )
    return check


async def run_and_record_check(
    session: AsyncSession,
    source: SourceBase,
    *,
    application_id: UUID,
    subject_type: str,  # applicant | guarantor
    subject_id: UUID | None,
    subject: Subject,
) -> Check:
    """Вызывает source.check(), пишет строку в checks. Источник обязан сам не
    бросать исключения (правило 4), но на всякий случай мы тоже это гарантируем —
    недоступный источник не должен ронять весь конвейер."""
    result, started_at, finished_at, duration_ms = await execute_check(source, subject)
    return await record_check(
        session,
        application_id=application_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source_name=source.source_name,
        result=result,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
    )
