"""T19: периодическая проверка здоровья системы и оповещение дежурного в
Telegram (app/alerts/telegram.py) — не экран для риск-менеджера
(app/api/system.py, T12), а именно алерты для сисадмина/дежурного, см.
docs/deployment.md.

Четыре сигнала (ваше явное указание):
- источник недоступен дольше N минут (ALERTS_SOURCE_UNAVAILABLE_MINUTES);
- заявки застряли (ALERTS_STUCK_APPLICATIONS_MINUTES);
- ответы заказчику не доходят (переиспользует PARTNER_OUTBOX_STUCK_MINUTES,
  T18 — тот же смысл: pending дольше этого в partner_outbox_events);
- бэкап не сделан (ALERTS_BACKUP_MAX_AGE_HOURS, читает mtime
  BACKUP_MARKER_PATH, который обновляет scripts/backup_postgres.sh).

Дедуп — в Redis (та же техника, что app/sources/throttle.py: SET NX EX):
при затянувшемся сбое не шлём то же сообщение чаще, чем раз в
ALERTS_RESEND_INTERVAL_MINUTES. Когда сигнал исчезает, ключ дедупа не
трогаем намеренно — он просто истечёт по TTL; это осознанно проще, чем
отдельное "всё снова хорошо"-сообщение (не просили)."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

from app.alerts.telegram import send_telegram_alert
from app.celery_app import celery_app
from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.state_machine import ALLOWED_TRANSITIONS
from app.models import Application, Check, PartnerOutboxEvent, StatusHistory
from app.models.check import CHECK_SOURCES
from app.redis import get_redis

logger = structlog.get_logger()

_TERMINAL_STATUSES = {status for status, targets in ALLOWED_TRANSITIONS.items() if not targets}
_BAD_CHECK_STATUSES = ("source_unavailable", "error")


async def _dedup_allows_send(key: str, resend_interval_minutes: int) -> bool:
    redis = get_redis()
    reserved = await redis.set(f"alerts:sent:{key}", "1", nx=True, ex=resend_interval_minutes * 60)
    return bool(reserved)


async def _check_sources_unavailable(session, settings) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.alerts_source_unavailable_minutes)
    messages = []
    for source in CHECK_SOURCES:
        total = (
            await session.execute(
                select(func.count()).select_from(Check).where(Check.source == source, Check.started_at >= cutoff)
            )
        ).scalar_one()
        if total == 0:
            continue
        bad = (
            await session.execute(
                select(func.count())
                .select_from(Check)
                .where(Check.source == source, Check.started_at >= cutoff, Check.status.in_(_BAD_CHECK_STATUSES))
            )
        ).scalar_one()
        if bad == total:
            messages.append(
                f"Источник {source!r} недоступен дольше "
                f"{settings.alerts_source_unavailable_minutes} мин. ({total} неудачных проверок подряд)."
            )
    return messages


async def _check_stuck_applications(session, settings) -> str | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.alerts_stuck_applications_minutes)
    last_change_subq = (
        select(StatusHistory.application_id, func.max(StatusHistory.created_at).label("last_change"))
        .group_by(StatusHistory.application_id)
        .subquery()
    )
    stuck = (
        await session.execute(
            select(func.count())
            .select_from(Application)
            .join(last_change_subq, last_change_subq.c.application_id == Application.id)
            .where(Application.status.notin_(_TERMINAL_STATUSES), last_change_subq.c.last_change < cutoff)
        )
    ).scalar_one()
    if stuck:
        return f"{stuck} заявок без смены статуса дольше {settings.alerts_stuck_applications_minutes} мин."
    return None


async def _check_outbox_stuck(session, settings) -> str | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.partner_outbox_stuck_minutes)
    stuck = (
        await session.execute(
            select(func.count())
            .select_from(PartnerOutboxEvent)
            .where(PartnerOutboxEvent.status == "pending", PartnerOutboxEvent.created_at < cutoff)
        )
    ).scalar_one()
    if stuck:
        return (
            f"{stuck} ответов заказчику не доставлены дольше "
            f"{settings.partner_outbox_stuck_minutes} мин. (очередь partner_outbox_events)."
        )
    return None


def _check_backup_stale(settings) -> str | None:
    try:
        mtime = os.stat(settings.backup_marker_path).st_mtime
    except OSError:
        return f"Бэкап базы ни разу не завершился успешно (нет файла {settings.backup_marker_path})."

    age_hours = (datetime.now(timezone.utc).timestamp() - mtime) / 3600
    if age_hours > settings.alerts_backup_max_age_hours:
        return f"Последний успешный бэкап базы — {age_hours:.1f} ч назад (порог {settings.alerts_backup_max_age_hours} ч)."
    return None


async def _check_health() -> dict[str, int]:
    settings = get_settings()
    engine, session_factory = new_sessionmaker()
    sent = 0
    try:
        messages: list[tuple[str, str]] = []
        async with session_factory() as session:
            for message in await _check_sources_unavailable(session, settings):
                messages.append((message, message))

            stuck_message = await _check_stuck_applications(session, settings)
            if stuck_message:
                messages.append(("stuck_applications", stuck_message))

            outbox_message = await _check_outbox_stuck(session, settings)
            if outbox_message:
                messages.append(("outbox_stuck", outbox_message))

        backup_message = _check_backup_stale(settings)
        if backup_message:
            messages.append(("backup_stale", backup_message))

        for dedup_key, text in messages:
            if await _dedup_allows_send(dedup_key, settings.alerts_resend_interval_minutes):
                await send_telegram_alert(settings, f"DIALOG Scoring: {text}")
                sent += 1
                logger.warning("alert_sent", dedup_key=dedup_key, text=text)
    finally:
        await engine.dispose()
    return {"checked": True, "sent": sent}


@celery_app.task(name="alerts.check_health")
def check_health_task() -> dict[str, int]:
    return asyncio.run(_check_health())
