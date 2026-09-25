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
    # Nullable (T10): изначально это была ручная правка сотрудника через нашу
    # админку (T12, ещё не реализована). T10 добавляет второй источник —
    # автоматическое обнаружение расхождения между нашим решением и статусом,
    # который оператор выставил вручную в системе заказчика (см.
    # write_decision_status/PartnerConflictError) — там нет сотрудника-актора
    # с нашей стороны, поэтому employee_id=NULL для таких строк.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
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
