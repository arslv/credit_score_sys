import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ManualOverride(Base):
    __tablename__ = "manual_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("decisions.id")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False
    )
    from_outcome: Mapped[str] = mapped_column(sa.Text, nullable=False)
    to_outcome: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(
        sa.Text, sa.ForeignKey("reason_codes.code"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
