"""T12: роль compliance, блокировка входа, сессии сотрудников, снятие
заморозки, разбор совпадений по перечню

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_employees_role", "employees", type_="check")
    op.create_check_constraint(
        "ck_employees_role", "employees", "role IN ('risk_manager', 'admin', 'viewer', 'compliance')"
    )
    op.add_column("employees", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("employees", sa.Column("locked_until", sa.DateTime(timezone=True)))

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference')",
    )

    op.create_table(
        "sessions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("ip", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_sessions_employee_id", "sessions", ["employee_id"])

    op.create_table(
        "freeze_lifts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_freeze_lifts_client_id", "freeze_lifts", ["client_id"])

    op.create_table(
        "compliance_reviews",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        # Устойчивый признак записи перечня (не sanctions_list.id и без FK на
        # него) — реестр перезагружается целиком атомарной подменой таблицы
        # (app/registries/sync.py), id меняется даже у неизменной записи; см.
        # docstring ComplianceReview в app/models/auth.py.
        sa.Column("sanctions_entry_key", sa.Text(), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("decisions.id")),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "verdict IN ('false_positive', 'confirmed')", name="ck_compliance_reviews_verdict"
        ),
    )
    op.create_index(
        "ix_compliance_reviews_client_id_sanctions_entry_key",
        "compliance_reviews", ["client_id", "sanctions_entry_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_reviews_client_id_sanctions_entry_key", table_name="compliance_reviews")
    op.drop_table("compliance_reviews")
    op.drop_index("ix_freeze_lifts_client_id", table_name="freeze_lifts")
    op.drop_table("freeze_lifts")
    op.drop_index("ix_sessions_employee_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('view_application', 'view_document', 'change_ruleset', 'export')",
    )

    op.drop_column("employees", "locked_until")
    op.drop_column("employees", "failed_login_count")
    op.drop_constraint("ck_employees_role", "employees", type_="check")
    op.create_check_constraint("ck_employees_role", "employees", "role IN ('risk_manager', 'admin', 'viewer')")
