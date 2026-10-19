import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemSetting(Base):
    """Настройки сервиса, которые админ меняет в админке (например, ocr_enabled).
    Только INSERT (правило 1 CLAUDE.md): каждое изменение — новая строка,
    действует последняя по created_at для своего key. Секреты сюда не пишутся
    (правило 8) — только флаги и параметры без тайны."""

    __tablename__ = "system_settings"
    __table_args__ = (sa.Index("ix_system_settings_key_created_at", "key", sa.desc("created_at")),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
