import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

BKI_REPORT_STATUSES = ("ok", "no_data", "source_unavailable", "error")
BKI_IN_FLIGHT_STATUSES = ("in_flight", "done", "failed")


class BkiReport(Base):
    """Журнал ПЛАТНЫХ обращений в бюро (T11) — append-only (правило 1
    CLAUDE.md): каждая реальная попытка вызова, независимо от исхода. Кэш —
    просто «есть ли строка status='ok' младше bki.cache_days по client_id».
    Он же — источник для дашборда и сверки со счетами бюро (count(*) за
    месяц). Если провайдер «не настроен» — вызова не было, строка не пишется."""

    __tablename__ = "bki_reports"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {BKI_REPORT_STATUSES!r}", name="ck_bki_reports_status"),
        sa.Index("ix_bki_reports_client_id_requested_at", "client_id", sa.desc("requested_at")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    report_raw: Mapped[dict | None] = mapped_column(JSONB)
    features: Mapped[dict | None] = mapped_column(JSONB)
    score: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(6, 2))
    error_text: Mapped[str | None] = mapped_column(sa.Text)
    requested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class BkiRequestInFlight(Base):
    """Защита от двойной оплаты (T11) — на уровне БД, не только в коде:
    частичный уникальный индекс по client_id WHERE status='in_flight' не даёт
    второй строке встать, пока первая не завершилась. Не append-only (как
    fssp_requests в T09) — это техническое состояние опроса, а не бизнес-факт."""

    __tablename__ = "bki_requests_in_flight"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {BKI_IN_FLIGHT_STATUSES!r}", name="ck_bki_in_flight_status"),
        sa.Index(
            "ux_bki_in_flight_client_id_pending",
            "client_id",
            unique=True,
            postgresql_where=sa.text("status = 'in_flight'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="in_flight")
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
