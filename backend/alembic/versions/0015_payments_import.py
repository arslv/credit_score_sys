"""T15: импорт платежей и целевая переменная.

- import_batches (пакет импорта — исходный файл + метаданные, идемпотентность
  по file_sha256), contract_link_candidates (кандидаты на привязку договора
  по телефону/ФИО — подтверждает человек, автоматически не привязываем).
- contracts.application_id -> nullable (не всегда привязывается однозначно),
  contracts.import_batch_id.
- contracts.closure_reason/closure_outcome (ваше замечание после первой
  версии: закрытие — не всегда погашение, причина закрытия и её трактовка
  (хороший/плохой/неизвестный исход) — из выгрузки и конфига, не "closed_at
  есть -> хорошо").
- contracts.schedule_source (generated|imported — ваше замечание: график по
  умолчанию генерируем равными платежами, но если выгрузка графика пришла —
  используется она).
- payments.kind (regular|penalty|reversal), payments.reverses_payment_id,
  payments.import_batch_id.
- delinquency_daily.computed_at (диагностика, не в PK).
- applications.source (live|historical_import).
- audit_log.action: + import_data, link_contract.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- applications.source ---------------------------------------------------
    op.add_column(
        "applications",
        sa.Column("source", sa.Text, nullable=False, server_default="live"),
    )
    op.create_check_constraint(
        "ck_applications_source", "applications", "source IN ('live', 'historical_import')",
    )

    # --- audit_log.action --------------------------------------------------------
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference', 'retry_check', 'import_data', 'link_contract')",
    )

    # --- import_batches ------------------------------------------------------------
    op.create_table(
        "import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("file_sha256", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("row_count", sa.Integer),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error_text", sa.Text),
        sa.Column("data_as_of", sa.Date),
        sa.CheckConstraint(
            "kind IN ('contracts', 'payments', 'historical_applications', 'payment_schedule')",
            name="ck_import_batches_kind",
        ),
        sa.CheckConstraint("status IN ('processing', 'completed', 'failed')", name="ck_import_batches_status"),
    )

    # --- contracts -------------------------------------------------------------
    op.alter_column("contracts", "application_id", nullable=True)
    op.add_column(
        "contracts",
        sa.Column("import_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("import_batches.id")),
    )
    op.add_column("contracts", sa.Column("closure_reason", sa.Text))
    op.add_column("contracts", sa.Column("closure_outcome", sa.Text))
    op.create_check_constraint(
        "ck_contracts_closure_outcome", "contracts", "closure_outcome IN ('good', 'bad', 'unknown')",
    )
    op.add_column(
        "contracts", sa.Column("schedule_source", sa.Text, nullable=False, server_default="generated"),
    )
    op.create_check_constraint(
        "ck_contracts_schedule_source", "contracts", "schedule_source IN ('generated', 'imported')",
    )

    # --- payments ----------------------------------------------------------------
    op.add_column("payments", sa.Column("kind", sa.Text, nullable=False, server_default="regular"))
    op.add_column(
        "payments",
        sa.Column("reverses_payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id")),
    )
    op.add_column(
        "payments",
        sa.Column("import_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("import_batches.id")),
    )
    op.create_check_constraint(
        "ck_payments_kind", "payments", "kind IN ('regular', 'penalty', 'reversal')",
    )

    # --- delinquency_daily --------------------------------------------------------
    op.add_column(
        "delinquency_daily",
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- contract_link_candidates --------------------------------------------------
    op.create_table(
        "contract_link_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("match_reason", sa.Text, nullable=False),
        sa.Column("source_name", sa.Text),
        sa.Column("source_phone", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')", name="ck_contract_link_candidates_status"
        ),
    )
    op.create_index(
        "ix_contract_link_candidates_status", "contract_link_candidates", ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_contract_link_candidates_status", table_name="contract_link_candidates")
    op.drop_table("contract_link_candidates")

    op.drop_column("delinquency_daily", "computed_at")

    op.drop_constraint("ck_payments_kind", "payments", type_="check")
    op.drop_column("payments", "import_batch_id")
    op.drop_column("payments", "reverses_payment_id")
    op.drop_column("payments", "kind")

    op.drop_constraint("ck_contracts_schedule_source", "contracts", type_="check")
    op.drop_column("contracts", "schedule_source")
    op.drop_constraint("ck_contracts_closure_outcome", "contracts", type_="check")
    op.drop_column("contracts", "closure_outcome")
    op.drop_column("contracts", "closure_reason")
    op.drop_column("contracts", "import_batch_id")
    op.alter_column("contracts", "application_id", nullable=False)

    op.drop_table("import_batches")

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference', 'retry_check')",
    )

    op.drop_constraint("ck_applications_source", "applications", type_="check")
    op.drop_column("applications", "source")
