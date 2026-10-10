import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

APPLICATION_STATUSES = (
    "received",
    "ocr",
    "ocr_failed",
    "stage_a",
    "rejected_a",
    "to_operator",
    "stage_b",
    "rejected_b",
    "gray_zone",
    "approved",
    "contracted",
    "expired",
)

# T15: заявка из исторической выгрузки (не через наш конвейер — нет OCR/БКИ/
# снимков признаков, импортирована для базового уровня дефолтности и reject
# inference, ТЗ «Логирование под будущую модель»). "live" — всё, что прошло
# (или ещё проходит) обычный путь приёма через систему заказчика.
APPLICATION_SOURCES = ("live", "historical_import")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        sa.CheckConstraint(
            f"status IN {APPLICATION_STATUSES!r}", name="ck_applications_status"
        ),
        sa.CheckConstraint(
            f"source IN {APPLICATION_SOURCES!r}", name="ck_applications_source"
        ),
        sa.Index("ix_applications_client_id", "client_id"),
        sa.Index("ix_applications_status_submitted_at", "status", sa.desc("submitted_at")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Клиент известен только после успешного распознавания паспорта (см. пайплайн OCR).
    # До этого момента у нас есть лишь ФИО и телефон из формы — их недостаточно для clients.
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clients.id")
    )
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    number: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="live")

    # параметры сделки
    goods_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    goods_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("goods_categories.id")
    )
    merchant_name: Mapped[str | None] = mapped_column(sa.Text)
    merchant_city: Mapped[str | None] = mapped_column(sa.Text)
    goods_price: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    down_payment: Mapped[sa.Numeric] = mapped_column(
        sa.Numeric(14, 2), nullable=False, server_default="0"
    )
    term_months: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tariff: Mapped[str] = mapped_column(sa.Text, nullable=False)
    requested_amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)

    # анкета: снимок на момент подачи, неизменяемый
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # технические данные
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    device_fingerprint: Mapped[str | None] = mapped_column(sa.Text)
    form_timings: Mapped[dict | None] = mapped_column(JSONB)
    utm: Mapped[dict | None] = mapped_column(JSONB)
    consent_pd_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    consent_bki_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    # T18: статус заявки В ИХ СИСТЕМЕ, каким они его нам сообщили последний раз
    # (вебхук /applications — всегда "new"; /review — их поле status). Нужен
    # как prev_status в исходящих уведомлениях и как expected_current_status
    # при записи решения (замена захардкоженных "new"/"on_review" из T04 —
    # теперь это не то, что МЫ ожидаем увидеть при опросе, а то, что ОНИ сами
    # нам сказали). Мутируется на месте — тот же принцип, что client_id выше:
    # техническое поле синхронизации, не append-only бизнес-факт.
    partner_last_known_status: Mapped[str | None] = mapped_column(sa.Text)


class Guarantor(Base):
    __tablename__ = "guarantors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clients.id")
    )
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
