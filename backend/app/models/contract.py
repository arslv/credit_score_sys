import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CONTRACT_STATUSES = ("active", "closed", "defaulted", "repossessed")
PAYMENT_SOURCES = ("import", "manual")


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {CONTRACT_STATUSES!r}", name="ck_contracts_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    external_number: Mapped[str | None] = mapped_column(sa.Text, unique=True)
    principal: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    markup: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    total_amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    down_payment: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    term_months: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    issued_at: Mapped[date] = mapped_column(sa.Date, nullable=False)
    closed_at: Mapped[date | None] = mapped_column(sa.Date)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class PaymentSchedule(Base):
    __tablename__ = "payment_schedule"
    __table_args__ = (sa.UniqueConstraint("contract_id", "period_no"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False
    )
    period_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    due_amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        sa.CheckConstraint(f"source IN {PAYMENT_SOURCES!r}", name="ck_payments_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False
    )
    paid_at: Mapped[date] = mapped_column(sa.Date, nullable=False)
    amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class DelinquencyDaily(Base):
    __tablename__ = "delinquency_daily"

    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("contracts.id"), primary_key=True
    )
    as_of: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    days_past_due: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    overdue_amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
