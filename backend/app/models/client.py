import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (sa.UniqueConstraint("passport_series", "passport_number"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    last_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    first_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    middle_name: Mapped[str | None] = mapped_column(sa.Text)
    birth_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    gender: Mapped[str | None] = mapped_column(sa.Text)
    passport_series: Mapped[str] = mapped_column(sa.CHAR(4), nullable=False)
    passport_number: Mapped[str] = mapped_column(sa.CHAR(6), nullable=False)
    inn: Mapped[str | None] = mapped_column(sa.CHAR(12))
    phone: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
