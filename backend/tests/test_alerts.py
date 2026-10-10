"""T19: оповещения в Telegram — источник недоступен дольше N минут, заявки
застряли, ответы заказчику не доходят, бэкап не сделан. Telegram — внешний
HTTP-вызов, мокается через respx (правило 10 CLAUDE.md)."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import respx
from httpx import Response

from app.config import Settings
from app.models import Application, Check, PartnerOutboxEvent, StatusHistory
from app.redis import get_redis
from app.tasks.alerts import (
    _check_backup_stale,
    _check_outbox_stuck,
    _check_sources_unavailable,
    _check_stuck_applications,
    _dedup_allows_send,
)
from app.alerts.telegram import send_telegram_alert

_TELEGRAM_URL = "https://api.telegram.org/bottest-token/sendMessage"


def _settings(**overrides) -> Settings:
    return Settings(telegram_bot_token="test-token", telegram_chat_id="12345", **overrides)


async def _application(session, *, status: str = "to_operator") -> Application:
    now = datetime.now(timezone.utc)
    application = Application(
        external_id=f"EXT-{uuid.uuid4().hex[:10]}", status=status,
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={}, consent_pd_at=now, submitted_at=now,
    )
    session.add(application)
    await session.flush()
    return application


async def test_send_telegram_alert_posts_to_bot_api():
    with respx.mock:
        route = respx.post(_TELEGRAM_URL).mock(return_value=Response(200, json={"ok": True}))
        await send_telegram_alert(_settings(), "тестовое сообщение")
        assert route.called
        assert route.calls.last.request.content
        import json

        body = json.loads(route.calls.last.request.content)
        assert body == {"chat_id": "12345", "text": "тестовое сообщение"}


async def test_send_telegram_alert_skips_when_not_configured():
    with respx.mock:
        route = respx.post(_TELEGRAM_URL).mock(return_value=Response(200, json={"ok": True}))
        await send_telegram_alert(Settings(telegram_bot_token="", telegram_chat_id=""), "не отправится")
        assert not route.called


async def test_send_telegram_alert_failure_does_not_raise():
    with respx.mock:
        respx.post(_TELEGRAM_URL).mock(return_value=Response(500))
        await send_telegram_alert(_settings(), "бот недоступен")  # не бросает


async def test_source_unavailable_detected_when_all_recent_checks_failed(session_factory):
    settings = _settings(alerts_source_unavailable_minutes=30)
    async with session_factory() as session:
        application = await _application(session)
        now = datetime.now(timezone.utc)
        for status in ("source_unavailable", "error", "source_unavailable"):
            session.add(Check(
                application_id=application.id, subject_type="applicant", source="mvd", status=status,
                started_at=now - timedelta(minutes=5),
            ))
        await session.commit()

        messages = await _check_sources_unavailable(session, settings)
        assert any("mvd" in m for m in messages)


async def test_source_unavailable_not_flagged_when_a_recent_check_succeeded(session_factory):
    settings = _settings(alerts_source_unavailable_minutes=30)
    async with session_factory() as session:
        application = await _application(session)
        now = datetime.now(timezone.utc)
        session.add(Check(
            application_id=application.id, subject_type="applicant", source="rosfinmon", status="source_unavailable",
            started_at=now - timedelta(minutes=5),
        ))
        session.add(Check(
            application_id=application.id, subject_type="applicant", source="rosfinmon", status="ok",
            started_at=now - timedelta(minutes=2),
        ))
        await session.commit()

        messages = await _check_sources_unavailable(session, settings)
        assert not any("rosfinmon" in m for m in messages)


async def test_source_with_no_checks_at_all_is_not_flagged(session_factory):
    settings = _settings(alerts_source_unavailable_minutes=30)
    async with session_factory() as session:
        messages = await _check_sources_unavailable(session, settings)
        assert messages == []


async def test_stuck_applications_detected(session_factory):
    settings = _settings(alerts_stuck_applications_minutes=60)
    async with session_factory() as session:
        application = await _application(session, status="to_operator")
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        session.add(StatusHistory(
            application_id=application.id, from_status="received", to_status="to_operator",
            actor_type="system", created_at=old,
        ))
        await session.commit()

        message = await _check_stuck_applications(session, settings)
        assert message is not None
        assert "1 заявок" in message


async def test_stuck_applications_not_flagged_for_terminal_status(session_factory):
    settings = _settings(alerts_stuck_applications_minutes=60)
    async with session_factory() as session:
        application = await _application(session, status="rejected_a")
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        session.add(StatusHistory(
            application_id=application.id, from_status="received", to_status="rejected_a",
            actor_type="system", created_at=old,
        ))
        await session.commit()

        message = await _check_stuck_applications(session, settings)
        assert message is None


async def test_outbox_stuck_detected(session_factory):
    settings = _settings(partner_outbox_stuck_minutes=30)
    async with session_factory() as session:
        application = await _application(session)
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        session.add(PartnerOutboxEvent(
            application_id=application.id, external_id=application.external_id, event_type="decision",
            payload={"status": "approved"}, status="pending", created_at=old,
        ))
        await session.commit()

        message = await _check_outbox_stuck(session, settings)
        assert message is not None
        assert "1 ответов" in message


async def test_outbox_not_flagged_when_sent(session_factory):
    settings = _settings(partner_outbox_stuck_minutes=30)
    async with session_factory() as session:
        application = await _application(session)
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        session.add(PartnerOutboxEvent(
            application_id=application.id, external_id=application.external_id, event_type="decision",
            payload={"status": "approved"}, status="sent", created_at=old, sent_at=old,
        ))
        await session.commit()

        message = await _check_outbox_stuck(session, settings)
        assert message is None


def test_backup_stale_when_marker_missing(tmp_path):
    settings = _settings(backup_marker_path=str(tmp_path / "missing"))
    message = _check_backup_stale(settings)
    assert message is not None
    assert "ни разу" in message


def test_backup_stale_when_marker_too_old(tmp_path):
    marker = tmp_path / ".last_success"
    marker.write_text("ok")
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    import os

    os.utime(marker, (old.timestamp(), old.timestamp()))
    settings = _settings(backup_marker_path=str(marker), alerts_backup_max_age_hours=26)

    message = _check_backup_stale(settings)
    assert message is not None
    assert "назад" in message


def test_backup_fresh_when_marker_recent(tmp_path):
    marker = tmp_path / ".last_success"
    marker.write_text("ok")
    settings = _settings(backup_marker_path=str(marker), alerts_backup_max_age_hours=26)

    message = _check_backup_stale(settings)
    assert message is None


async def test_dedup_allows_send_once_then_blocks_until_ttl():
    key = f"test-dedup-{uuid.uuid4().hex[:8]}"
    try:
        first = await _dedup_allows_send(key, resend_interval_minutes=60)
        second = await _dedup_allows_send(key, resend_interval_minutes=60)
        assert first is True
        assert second is False
    finally:
        await get_redis().delete(f"alerts:sent:{key}")
