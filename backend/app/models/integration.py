import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.feature import STAGES

INTEGRATION_EVENT_DIRECTIONS = ("inbound", "outbound")
INTEGRATION_EVENT_STATUSES = ("ok", "error", "conflict", "retry")


class ExternalSnapshot(Base):
    __tablename__ = "external_snapshots"
    __table_args__ = (
        sa.CheckConstraint(f"stage IN {STAGES!r}", name="ck_external_snapshots_stage"),
        sa.Index(
            "ix_external_snapshots_application_stage_received",
            "application_id",
            "stage",
            sa.desc("received_at"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload_raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Идемпотентность по external_id + хешу снимка (см. T04): sha256 от
    # канонической сериализации payload_raw, чтобы не писать тот же снимок повторно.
    content_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class OcrResult(Base):
    __tablename__ = "ocr_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    response_raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    min_confidence: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(4, 3))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class IntegrationEvent(Base):
    __tablename__ = "integration_events"
    __table_args__ = (
        sa.CheckConstraint(
            f"direction IN {INTEGRATION_EVENT_DIRECTIONS!r}", name="ck_integration_events_direction"
        ),
        sa.CheckConstraint(
            f"status IN {INTEGRATION_EVENT_STATUSES!r}", name="ck_integration_events_status"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id")
    )
    direction: Mapped[str] = mapped_column(sa.Text, nullable=False)
    operation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    request: Mapped[dict | None] = mapped_column(JSONB)
    response: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
