"""ФНС — отдельная задача Celery (T16, ваше явное указание): стадия А/Б не
ждёт ответа ФНС вовсе, финализируется сразу по локальным признакам; эта
задача запускается "и забыли" (.delay()) сразу после — результат приходит в
checks асинхронно, карточка заявки видит его при обычном обновлении (ничего
не блокирует, никакого опроса с фронта не требуется, просто GET заявки
покажет новую строку checks, когда она появится).

fns_npd/fns_egrip запускаются только если fns_inn нашёл ИНН — без него
спрашивать нечего (сами эти источники принимают ИНН, не ФИО)."""

import asyncio
from datetime import date, datetime, timezone
from uuid import UUID

import structlog

from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.sources.base import CheckStatus, Subject
from app.sources.factory import build_fns_egrip_source, build_fns_inn_source, build_fns_npd_source
from app.sources.runner import execute_check, record_check

logger = structlog.get_logger()


def _subject_from_payload(payload: dict) -> Subject:
    return Subject(
        last_name=payload.get("last_name"),
        first_name=payload.get("first_name"),
        middle_name=payload.get("middle_name"),
        birth_date=date.fromisoformat(payload["birth_date"]) if payload.get("birth_date") else None,
        passport_series=payload.get("passport_series"),
        passport_number=payload.get("passport_number"),
        passport_issue_date=(
            date.fromisoformat(payload["passport_issue_date"]) if payload.get("passport_issue_date") else None
        ),
    )


async def _run(application_id: str, subject_type: str, subject_id: str | None, subject_payload: dict) -> dict:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    subject = _subject_from_payload(subject_payload)
    app_uuid = UUID(application_id)
    subj_uuid = UUID(subject_id) if subject_id else None

    summary = {"fns_inn": "skipped", "fns_npd": "skipped", "fns_egrip": "skipped"}
    try:
        fns_inn_source = build_fns_inn_source(settings)
        inn_result, started_at, finished_at, duration_ms = await execute_check(fns_inn_source, subject)
        async with session_factory() as session:
            await record_check(
                session, application_id=app_uuid, subject_type=subject_type, subject_id=subj_uuid,
                source_name="fns_inn", result=inn_result, started_at=started_at, finished_at=finished_at,
                duration_ms=duration_ms,
            )
            await session.commit()
        summary["fns_inn"] = inn_result.status.value

        inn = (inn_result.parsed or {}).get("inn") if inn_result.status == CheckStatus.OK else None
        if inn:
            npd_source = build_fns_npd_source(settings)
            egrip_source = build_fns_egrip_source(settings)

            npd_started = datetime.now(timezone.utc)
            npd_result = await npd_source.check_by_inn(inn, as_of=date.today())
            egrip_result = await egrip_source.check_by_inn(inn)
            npd_finished = datetime.now(timezone.utc)

            async with session_factory() as session:
                await record_check(
                    session, application_id=app_uuid, subject_type=subject_type, subject_id=subj_uuid,
                    source_name="fns_npd", result=npd_result, started_at=npd_started, finished_at=npd_finished,
                    duration_ms=0,
                )
                await record_check(
                    session, application_id=app_uuid, subject_type=subject_type, subject_id=subj_uuid,
                    source_name="fns_egrip", result=egrip_result, started_at=npd_started, finished_at=npd_finished,
                    duration_ms=0,
                )
                await session.commit()
            summary["fns_npd"] = npd_result.status.value
            summary["fns_egrip"] = egrip_result.status.value
    finally:
        await engine.dispose()

    logger.info("fns_checks_completed", application_id=application_id, subject_type=subject_type, **summary)
    return summary


@celery_app.task(name="fns.run_checks")
def run_fns_checks_task(
    application_id: str, subject_type: str, subject_id: str | None, subject_payload: dict,
) -> dict:
    return asyncio.run(_run(application_id, subject_type, subject_id, subject_payload))
