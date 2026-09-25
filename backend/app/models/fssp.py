import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FSSP_REQUEST_STATUSES = ("pending", "done", "expired")
FSSP_REQUEST_STAGES = ("A", "B")
FSSP_REQUEST_SUBJECT_TYPES = ("applicant", "guarantor")


class FsspRequest(Base):
    """Состояние опроса ФССП «запрос -> ID -> опрос статуса» (T09, обобщено в
    T10 на поручителя стадии Б через subject_type/subject_id/stage — опрос
    один и тот же механизм для заявителя и поручителя, разница только в том,
    какую финализацию дёргать по готовности: finalize_stage_a или
    finalize_stage_b). Не входит в список append-only таблиц правила 1
    CLAUDE.md — это техническое состояние опроса, а не бизнес-факт, поэтому
    строка обновляется на месте (без гонки за "последней" строкой, см. урок
    T08 про created_at внутри одной транзакции)."""

    __tablename__ = "fssp_requests"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {FSSP_REQUEST_STATUSES!r}", name="ck_fssp_requests_status"),
        sa.CheckConstraint(f"stage IN {FSSP_REQUEST_STAGES!r}", name="ck_fssp_requests_stage"),
        sa.CheckConstraint(
            f"subject_type IN {FSSP_REQUEST_SUBJECT_TYPES!r}", name="ck_fssp_requests_subject_type"
        ),
        sa.Index("ix_fssp_requests_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="A")
    subject_type: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="applicant")
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
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
