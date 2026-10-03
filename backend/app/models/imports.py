import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

IMPORT_BATCH_KINDS = ("contracts", "payments", "historical_applications", "payment_schedule")
IMPORT_BATCH_STATUSES = ("processing", "completed", "failed")
CONTRACT_LINK_CANDIDATE_STATUSES = ("pending", "confirmed", "rejected")


class ImportBatch(Base):
    """Пакет импорта (T15, ТЗ «Импорт платежей») — append-only: исходный файл
    сохраняется целиком (как исходный ответ источника, правило 3 CLAUDE.md), с
    ним же — кто/когда загрузил и сколько строк разобрано. file_sha256 UNIQUE —
    идемпотентность: повторная загрузка того же файла не создаёт новый пакет
    (критерий приёмки ТЗ). Сами байты — через Storage (ваше явное указание: в
    выгрузке есть ФИО и телефоны — храним так же, как фото паспортов, не в БД
    как текст/blob)."""

    __tablename__ = "import_batches"
    __table_args__ = (
        sa.CheckConstraint(f"kind IN {IMPORT_BATCH_KINDS!r}", name="ck_import_batches_kind"),
        sa.CheckConstraint(f"status IN {IMPORT_BATCH_STATUSES!r}", name="ck_import_batches_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    filename: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False, unique=True)
    storage_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    row_count: Mapped[int | None] = mapped_column(sa.Integer)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    error_text: Mapped[str | None] = mapped_column(sa.Text)
    # Для kind="payments" (ваше явное указание, T15 п.5): дата, по которую в
    # пакете есть данные — окно наблюдения договора заканчивается ею, а не
    # днём пересчёта. Для kind="contracts" — тоже используется (T15, правка
    # после первой версии): проверка полноты выгрузки платежей сопоставляет
    # пакет платежей с пакетом договоров на ТУ ЖЕ дату (см.
    # app/engine/imports.py::import_payments), а не со строками самого файла
    # платежей — у просрочника, который не платит, строк там просто нет, это
    # не признак неполноты выгрузки. Для historical_applications/
    # payment_schedule не нужна.
    data_as_of: Mapped[date | None] = mapped_column(sa.Date)


class ContractLinkCandidate(Base):
    """Кандидат на привязку договора к заявке по телефону/ФИО (T15, ваше явное
    указание: автоматически — только по однозначному идентификатору;
    совпадение по телефону/ФИО — только кандидат, привязку подтверждает
    человек, с записью в аудит). source_name/source_phone — как было в строке
    выгрузки, для решения человека; не на Contract — это рабочие данные
    процесса подтверждения, а не постоянный атрибут договора."""

    __tablename__ = "contract_link_candidates"
    __table_args__ = (
        sa.CheckConstraint(
            f"status IN {CONTRACT_LINK_CANDIDATE_STATUSES!r}", name="ck_contract_link_candidates_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    match_reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(sa.Text)
    source_phone: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="pending", index=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
