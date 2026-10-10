"""T18: заказчик сам шлёт нам заявки (webhook), мы отвечаем на их эндпоинт —
ТЗ «Интеграция по новой схеме» (ваше явное указание, формат определяем мы).

Две таблицы, обе — исключения из правила 1 CLAUDE.md (append-only), по той же
логике, что уже документирована для bki_requests_in_flight/fssp_captcha_sessions/
delinquency_daily:

- PartnerInboundEvent — НЕ факт-лог, а техническое состояние дедупликации:
  "этот event_id мы уже видели и обработали, не делай этого снова". Сам факт
  получения заявки/анкеты остаётся append-only в integration_events
  (direction="inbound") — эта таблица только гасит повтор одной и той же
  доставки (их ретраи на таймауте/5xx на нашей стороне, HTTP гарантий "ровно
  один раз" не бывает).
- PartnerOutboxEvent — координация доставки, не бизнес-факт (как
  fssp_captcha_sessions координирует диалог с сайтом ФССП, не изменяет сам
  результат проверки): "какой статус/балл мы ДОЛЖНЫ сообщить и уже сообщили
  ли" — обновляется на месте (attempt/next_attempt_at/status) по мере
  повторов. Каждая попытка доставки всё равно пишется в integration_events
  (direction="outbound") — append-only лог попыток остаётся нетронутым."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

PARTNER_OUTBOX_EVENT_TYPES = ("ocr_result", "decision")
PARTNER_OUTBOX_STATUSES = ("pending", "sent", "conflict")


class PartnerInboundEvent(Base):
    """Дедупликация входящих вебхуков по event_id (ТЗ: «Идемпотентность по
    event_id»). endpoint — какой из двух вебхуков («applications» | «review»)."""

    __tablename__ = "partner_inbound_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    endpoint: Mapped[str] = mapped_column(sa.Text, nullable=False)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id")
    )
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class PartnerOutboxEvent(Base):
    """Очередь исходящих уведомлений на их эндпоинт — «ничего не теряем» (ваше
    явное указание): неудача (5xx/таймаут) не роняет обработку заявки, строка
    остаётся pending и подбирается повторно с нарастающей задержкой
    (app/tasks/partner_outbox.py). 409 с их стороны — конфликт статуса
    (оператор уже изменил), не ошибка — status="conflict", retry не нужен."""

    __tablename__ = "partner_outbox_events"
    __table_args__ = (
        sa.CheckConstraint(f"event_type IN {PARTNER_OUTBOX_EVENT_TYPES!r}", name="ck_partner_outbox_event_type"),
        sa.CheckConstraint(f"status IN {PARTNER_OUTBOX_STATUSES!r}", name="ck_partner_outbox_status"),
        sa.Index("ix_partner_outbox_pending", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="pending")
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
