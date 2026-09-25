"""ФНС/Федресурс/ФССП — общий механизм для заявителя (стадия А, T09) и
поручителя (стадия Б, T10): один и тот же submit/poll/gather, разница только
в subject_type/subject_id (кого проверяем) и stage (какую финализацию дёргать
по готовности ФССП — finalize_stage_a или finalize_stage_b, см.
app/tasks/fssp_poll.py)."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.config import Settings
from app.models import FsspRequest
from app.sources.base import CheckResult, CheckStatus, Subject
from app.sources.fssp import FsspSource, FsspSubmitError
from app.sources.runner import execute_check, record_check


async def run_fns_and_fedresurs(
    session,
    *,
    settings: Settings,
    application_id: UUID,
    subject_type: str,
    subject_id: UUID | None,
    subject: Subject,
    fns_source,
    fedresurs_source,
) -> None:
    """ФНС и Федресурс — параллельно (asyncio.gather), в рамках общего дедлайна
    на весь синхронный внешний блок (T09, п.2 и п. про общий дедлайн). I/O —
    конкурентно; запись в общую AsyncSession — последовательно после."""
    try:
        (fns_result, fns_started, fns_finished, fns_duration), (
            fedresurs_result,
            fedresurs_started,
            fedresurs_finished,
            fedresurs_duration,
        ) = await asyncio.wait_for(
            asyncio.gather(execute_check(fns_source, subject), execute_check(fedresurs_source, subject)),
            timeout=settings.stage_a_external_checks_deadline_seconds,
        )
    except asyncio.TimeoutError:
        now = datetime.now(timezone.utc)
        timeout_result = CheckResult(
            status=CheckStatus.SOURCE_UNAVAILABLE, error_text="общий дедлайн внешнего блока истёк"
        )
        fns_result = fedresurs_result = timeout_result
        fns_started = fedresurs_started = now
        fns_finished = fedresurs_finished = now
        fns_duration = fedresurs_duration = 0

    await record_check(
        session,
        application_id=application_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source_name=fns_source.source_name,
        result=fns_result,
        started_at=fns_started,
        finished_at=fns_finished,
        duration_ms=fns_duration,
    )
    await record_check(
        session,
        application_id=application_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source_name=fedresurs_source.source_name,
        result=fedresurs_result,
        started_at=fedresurs_started,
        finished_at=fedresurs_finished,
        duration_ms=fedresurs_duration,
    )


async def submit_fssp(
    session,
    settings: Settings,
    *,
    application_id: UUID,
    subject_type: str,
    subject_id: UUID | None,
    stage: str,
    subject: Subject,
    fssp_source: FsspSource,
) -> bool:
    """Возвращает True, если ФССП уже "settled" (отказался принять запрос, или
    ответил мгновенно на первый poll()) — финализировать можно сразу; False —
    запрос поставлен в очередь на опрос, финализация откладывается до
    fssp.poll_pending (T09, п.4 поправок; T10 — обобщено на поручителя).

    После успешного submit() пробуем ОДИН немедленный poll() — не цикл и не
    sleep, просто использование того, что уже есть под рукой: если источник
    ответил мгновенно (в т.ч. фейковый источник без сконфигурированной
    задержки в тестах и в безопасном режиме "нет доступа" по умолчанию), не
    заставляем стадию ждать следующего тика отдельной задачи опроса ради
    результата, который уже готов."""
    now = datetime.now(timezone.utc)
    try:
        request_id = await fssp_source.submit(subject)
    except FsspSubmitError as exc:
        await record_check(
            session,
            application_id=application_id,
            subject_type=subject_type,
            subject_id=subject_id,
            source_name="fssp",
            result=CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text=str(exc)),
            started_at=now,
            finished_at=datetime.now(timezone.utc),
            duration_ms=0,
        )
        return True

    immediate_result = await fssp_source.poll(request_id)
    if immediate_result is not None:
        await record_check(
            session,
            application_id=application_id,
            subject_type=subject_type,
            subject_id=subject_id,
            source_name="fssp",
            result=immediate_result,
            started_at=now,
            finished_at=datetime.now(timezone.utc),
            duration_ms=0,
        )
        return True

    session.add(
        FsspRequest(
            application_id=application_id,
            stage=stage,
            subject_type=subject_type,
            subject_id=subject_id,
            external_request_id=request_id,
            status="pending",
            deadline=now + timedelta(seconds=settings.fssp_poll_deadline_seconds),
        )
    )
    return False
