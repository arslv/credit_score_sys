import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SANCTIONS_LIST_TYPES = ("te", "fromu", "mvk", "un")


class InvalidPassport(Base):
    __tablename__ = "invalid_passports"

    passport_series: Mapped[str] = mapped_column(sa.CHAR(4), primary_key=True)
    passport_number: Mapped[str] = mapped_column(sa.CHAR(6), primary_key=True)
    loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class SanctionsList(Base):
    __tablename__ = "sanctions_list"
    __table_args__ = (
        sa.CheckConstraint(f"list_type IN {SANCTIONS_LIST_TYPES!r}", name="ck_sanctions_list_type"),
        sa.Index(
            "ix_sanctions_list_name_normalized_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    list_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)
    birth_date: Mapped[date | None] = mapped_column(sa.Date)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class RegistrySyncLog(Base):
    __tablename__ = "registry_sync_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Атрибут переименован в Python (registry -> registry_name) — "registry" в
    # Declarative зарезервировано под реестр классов SQLAlchemy (SAWarning),
    # имя колонки в БД оставлено прежним, миграция не трогается.
    registry_name: Mapped[str] = mapped_column("registry", sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rows_loaded: Mapped[int | None] = mapped_column(sa.Integer)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_text: Mapped[str | None] = mapped_column(sa.Text)
