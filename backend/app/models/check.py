import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CHECK_SUBJECT_TYPES = ("applicant", "guarantor")
# fedresurs/fns — T16: больше не пишутся новыми проверками (Федресурс убран,
# ФНС теперь fns_inn/fns_npd/fns_egrip), но остаются в допустимых значениях —
# история (checks append-only, правило 1 CLAUDE.md) не трогается.
CHECK_SOURCES = (
    "mvd", "rosfinmon", "fssp", "fedresurs", "fns", "fns_inn", "fns_npd", "fns_egrip", "bki", "blacklist",
)
CHECK_STATUSES = ("ok", "hit", "no_data", "source_unavailable", "error")


class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (
        sa.CheckConstraint(
            f"subject_type IN {CHECK_SUBJECT_TYPES!r}", name="ck_checks_subject_type"
        ),
        sa.CheckConstraint(f"source IN {CHECK_SOURCES!r}", name="ck_checks_source"),
        sa.CheckConstraint(f"status IN {CHECK_STATUSES!r}", name="ck_checks_status"),
        sa.Index("ix_checks_application_id_source", "application_id", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    request: Mapped[dict | None] = mapped_column(JSONB)
    response_raw: Mapped[dict | None] = mapped_column(JSONB)
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    error_text: Mapped[str | None] = mapped_column(sa.Text)
