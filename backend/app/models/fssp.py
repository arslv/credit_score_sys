import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FSSP_REQUEST_STATUSES = ("pending", "done", "expired")


class FsspRequest(Base):
    """Состояние опроса ФССП «запрос -> ID -> опрос статуса» (T09). Не входит в
    список append-only таблиц правила 1 CLAUDE.md — это техническое состояние
    опроса, а не бизнес-факт, поэтому строка обновляется на месте (без гонки за
    "последней" строкой, см. урок T08 про created_at внутри одной транзакции)."""

    __tablename__ = "fssp_requests"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {FSSP_REQUEST_STATUSES!r}", name="ck_fssp_requests_status"),
        sa.Index("ix_fssp_requests_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    external_request_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    deadline: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()
    )
