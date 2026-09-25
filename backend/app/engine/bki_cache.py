"""Кэш отчёта БКИ (по client_id, не по заявке) + защита от двойной оплаты на
уровне БД (T11, ваши уточнения к плану). Клейм in-flight — короткая,
самостоятельная транзакция; сам платный вызов бюро происходит СТРОГО вне
какой-либо транзакции (правило 13 CLAUDE.md: не держим блокировку во время
обращения к внешнему источнику — здесь то же самое, хоть FOR UPDATE и не на
applications, а частичный уникальный индекс на bki_requests_in_flight)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import BkiReport as BkiReportRow
from app.models import BkiRequestInFlight

logger = structlog.get_logger()


async def get_cached_report_row(session, client_id: UUID, cache_days: int) -> BkiReportRow | None:
    """Последний успешный (status='ok') отчёт по клиенту младше cache_days.
    Вызывающий код (stage_b_pipeline) обязан сначала проверить согласие на
    КИ у ТЕКУЩЕЙ заявки — кэш сам по согласию не судит (оно привязано к
    заявке, не к клиенту)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=cache_days)
    return (
        await session.execute(
            select(BkiReportRow)
            .where(
                BkiReportRow.client_id == client_id,
                BkiReportRow.status == "ok",
                BkiReportRow.requested_at >= cutoff,
            )
            .order_by(BkiReportRow.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _expire_stale_in_flight(session, client_id: UUID, timeout_minutes: int) -> None:
    """Строка in_flight старше срока — воркер, скорее всего, упал после
    записи, не успев завершить или пометить сбой. Не блокирует клиента
    навсегда (ваше уточнение к плану T11)."""
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    await session.execute(
        update(BkiRequestInFlight)
        .where(
            BkiRequestInFlight.client_id == client_id,
            BkiRequestInFlight.status == "in_flight",
            BkiRequestInFlight.started_at < stale_before,
        )
        .values(status="failed", completed_at=datetime.now(timezone.utc))
    )


async def claim_in_flight(
    session_factory: async_sessionmaker, *, client_id: UUID, application_id: UUID, timeout_minutes: int
) -> UUID | None:
    """Короткая отдельная транзакция: сначала истекшие in_flight — сбойные,
    затем попытка застолбить новую строку через частичный уникальный индекс
    (ux_bki_in_flight_client_id_pending, миграция 0010). Возвращает id
    заклеймленной строки, либо None — по клиенту уже идёт другой платный
    запрос, ждать не нужно, заявка остаётся в stage_b на следующий подбор."""
    async with session_factory() as session:
        await _expire_stale_in_flight(session, client_id, timeout_minutes)

        stmt = (
            pg_insert(BkiRequestInFlight)
            .values(client_id=client_id, application_id=application_id, status="in_flight")
            .on_conflict_do_nothing(
                index_elements=[BkiRequestInFlight.client_id],
                index_where=text("status = 'in_flight'"),
            )
            .returning(BkiRequestInFlight.id)
        )
        row_id = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
        return row_id


async def release_in_flight(session_factory: async_sessionmaker, in_flight_id: UUID, *, failed: bool) -> None:
    async with session_factory() as session:
        row = await session.get(BkiRequestInFlight, in_flight_id)
        if row is not None:
            row.status = "failed" if failed else "done"
            row.completed_at = datetime.now(timezone.utc)
            await session.commit()


async def record_attempt(
    session_factory: async_sessionmaker,
    *,
    client_id: UUID,
    application_id: UUID,
    status: str,
    report_raw: dict | None,
    features: dict | None,
    score,
    error_text: str | None,
) -> None:
    """Журнал ПЛАТНЫХ попыток (T11) — append-only (правило 1 CLAUDE.md),
    каждая реальная попытка вызова бюро независимо от исхода. "Не настроено"
    сюда не попадает вообще — вызывающий код (stage_b_pipeline) проверяет
    provider.is_configured ДО вызова этой функции и до claim_in_flight."""
    async with session_factory() as session:
        session.add(
            BkiReportRow(
                client_id=client_id,
                application_id=application_id,
                status=status,
                report_raw=report_raw,
                features=features,
                score=score,
                error_text=error_text,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
