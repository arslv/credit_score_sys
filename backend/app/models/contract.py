import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CONTRACT_STATUSES = ("active", "closed", "defaulted", "repossessed")
PAYMENT_SOURCES = ("import", "manual")
# T15: regular — по графику (единственное, что участвует в разнесении по
# периодам, app.engine.delinquency); penalty — неустойка за просрочку, по
# мурабахе идёт на благотворительность и не доход, в разнесение по графику не
# засчитывается, только хранится (сырые данные); reversal — отмена/исправление
# задним числом (ваше явное указание): payments append-only, отмена — НОВАЯ
# строка (reverses_payment_id -> исходная), а не UPDATE/DELETE исходной.
PAYMENT_KINDS = ("regular", "penalty", "reversal")
# T15, ваше замечание после первой версии: закрытие договора — не всегда
# погашение (товар могли изъять или долг списать — это дефолт, не хороший
# исход). Причина закрытия приходит в выгрузке, трактовка — в конфиге
# (config/import_mappings/contracts.yaml::closure_outcome_map), не в коде.
# "unknown" — closed_at есть, а причина не распознана конфигом: таргет для
# такого договора NULL, не 0 (см. target_bad_60_6/90_12).
CLOSURE_OUTCOMES = ("good", "bad", "unknown")
SCHEDULE_SOURCES = ("generated", "imported")


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {CONTRACT_STATUSES!r}", name="ck_contracts_status"),
        sa.CheckConstraint(f"closure_outcome IN {CLOSURE_OUTCOMES!r}", name="ck_contracts_closure_outcome"),
        sa.CheckConstraint(f"schedule_source IN {SCHEDULE_SOURCES!r}", name="ck_contracts_schedule_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # T15: nullable — договор из выгрузки не всегда удаётся привязать к нашей
    # заявке (ваше явное указание: автоматически — только по однозначному
    # идентификатору; остальное — кандидат в отчёте импорта, подтверждает
    # человек). Непривязанный договор всё равно импортируется — график и
    # платежи не теряются, см. ContractLinkCandidate.
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id")
    )
    external_number: Mapped[str | None] = mapped_column(sa.Text, unique=True)
    principal: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    markup: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    total_amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    down_payment: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    term_months: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    issued_at: Mapped[date] = mapped_column(sa.Date, nullable=False)
    closed_at: Mapped[date | None] = mapped_column(sa.Date)
    # Причина закрытия как в выгрузке (для прозрачности/аудита) и её трактовка
    # по config/import_mappings/contracts.yaml::closure_outcome_map. Оба NULL,
    # пока closed_at не заполнен (договор не закрыт).
    closure_reason: Mapped[str | None] = mapped_column(sa.Text)
    closure_outcome: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # "generated" — равными платежами от нас (по умолчанию); "imported" —
    # пришёл отдельным файлом (app.engine.imports.import_payment_schedule) и
    # заменил сгенерированный (ваше замечание: график — импортом, генерация
    # только запасной вариант).
    schedule_source: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="generated")
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("import_batches.id")
    )
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
        sa.CheckConstraint(f"kind IN {PAYMENT_KINDS!r}", name="ck_payments_kind"),
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
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="regular")
    reverses_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("payments.id")
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("import_batches.id")
    )
    imported_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class DelinquencyDaily(Base):
    """Производная таблица — целиком вычислима из payments (append-only),
    перезапись разрешена (правило 1 CLAUDE.md, явное исключение, T15).
    computed_at — для диагностики (когда в последний раз пересчитана именно
    эта строка), намеренно НЕ в первичном ключе: PK остаётся (contract_id,
    as_of) по ТЗ, одна строка на день."""

    __tablename__ = "delinquency_daily"

    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("contracts.id"), primary_key=True
    )
    as_of: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    days_past_due: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    overdue_amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(14, 2), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
