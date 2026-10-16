import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# compliance — T12: не было в ролях ТЗ, заведена по вашему указанию (единственная
# роль, которая снимает заморозку по санкциям и помечает совпадения ложными).
# operator — T16, ваше явное указание: видит заявки и делает проверку ФССП
# (капча); правила/сотрудники/аудит недоступны — не риск-менеджер и не админ,
# у admin тоже нет доступа к /fssp/* (та же модель ролей — персданные
# заявителей не для админской роли, см. app/api/fssp.py).
# T23 (решение владельца): три роли — обязанности risk_manager и compliance
# перешли к admin (разбор совпадений с перечнем — ещё и к operator), миграция 0019.
EMPLOYEE_ROLES = ("admin", "operator", "viewer")
DOCUMENT_KINDS = ("selfie_passport", "passport_main", "passport_reg")
AUDIT_LOG_ACTIONS = (
    "view_application", "view_document", "view_credit_report", "change_ruleset", "export",
    "login", "login_failed", "logout", "lift_freeze", "mark_false_positive",
    "manage_employee", "manage_reference", "retry_check",
    "import_data", "link_contract",  # T15
    # T16: только факт попытки/кто/когда/результат — НИКОГДА картинка капчи
    # или введённый код (ваше явное указание), см. app/api/fssp.py.
    "fssp_search_started", "fssp_code_submitted",
)
STATUS_HISTORY_ACTOR_TYPES = ("system", "employee", "client")


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (sa.CheckConstraint(f"role IN {EMPLOYEE_ROLES!r}", name="ck_employees_role"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    # Блокировка по учётной записи (T12, ТЗ «Аутентификация»: "блокировка после
    # серии неудачных попыток") — короткая, дополняется блокировкой по IP
    # (app/security/rate_limit.py), которая не завязана на конкретный email.
    failed_login_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        sa.CheckConstraint(f"kind IN {DOCUMENT_KINDS!r}", name="ck_documents_kind"),
        sa.Index("ix_documents_application_id_kind", "application_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    mime: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
    )
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    delete_after: Mapped[date | None] = mapped_column(sa.Date)
    uploaded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        sa.CheckConstraint(f"action IN {AUDIT_LOG_ACTIONS!r}", name="ck_audit_log_action"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
    )
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(sa.Text)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(INET)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class StatusHistory(Base):
    __tablename__ = "status_history"
    __table_args__ = (
        sa.CheckConstraint(
            f"actor_type IN {STATUS_HISTORY_ACTOR_TYPES!r}", name="ck_status_history_actor_type"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(sa.Text)
    to_status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
