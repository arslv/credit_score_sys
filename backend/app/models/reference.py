import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

LIQUIDITY_LEVELS = ("high", "medium", "low")
REASON_CODE_KINDS = ("reject", "override_approve", "override_reject")


class GoodsCategory(Base):
    __tablename__ = "goods_categories"
    __table_args__ = (
        sa.CheckConstraint(f"liquidity IN {LIQUIDITY_LEVELS!r}", name="ck_goods_categories_liquidity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    sharia_allowed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    liquidity: Mapped[str] = mapped_column(sa.Text, nullable=False)
    lgd_estimate: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(4, 3))
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class ReasonCode(Base):
    __tablename__ = "reason_codes"
    __table_args__ = (
        sa.CheckConstraint(f"kind IN {REASON_CODE_KINDS!r}", name="ck_reason_codes_kind"),
    )

    code: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class Blacklist(Base):
    __tablename__ = "blacklist"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    last_name: Mapped[str | None] = mapped_column(sa.Text)
    first_name: Mapped[str | None] = mapped_column(sa.Text)
    middle_name: Mapped[str | None] = mapped_column(sa.Text)
    birth_date: Mapped[date | None] = mapped_column(sa.Date)
    passport_series: Mapped[str | None] = mapped_column(sa.CHAR(4))
    passport_number: Mapped[str | None] = mapped_column(sa.CHAR(6))
    phone: Mapped[str | None] = mapped_column(sa.Text)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
    )
    added_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
