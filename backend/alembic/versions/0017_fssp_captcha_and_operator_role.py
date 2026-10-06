"""T16: роль operator, таблица fssp_captcha_sessions взамен fssp_requests
(старый протокол api-ip.fssp.gov.ru закрыт с 2022, опрашивать больше нечего —
новый протокол: капча, которую решает оператор, а не опрос статуса), новые
действия аудита, таблица fssp_post_decision_alerts (T16, п.3 ваших правок:
результат ФССП пришёл после уже принятого решения стадии Б и сработал бы на
hard_stop — статус не меняем, правило 12 CLAUDE.md, но запоминаем факт для
предупреждения в карточке и счётчика на экране состояния системы).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- роль operator ---------------------------------------------------
    op.drop_constraint("ck_employees_role", "employees", type_="check")
    op.create_check_constraint(
        "ck_employees_role", "employees", "role IN ('risk_manager', 'admin', 'viewer', 'compliance', 'operator')",
    )

    # --- действия аудита (T16) --------------------------------------------
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ("
        "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference', 'retry_check', "
        "'import_data', 'link_contract', "
        "'fssp_search_started', 'fssp_code_submitted')",
    )

    # --- checks.source: fns_inn/fns_npd/fns_egrip вместо одного fns (T16) ---
    # fns/fedresurs остаются допустимыми значениями — старые строки (правило 1
    # CLAUDE.md, checks append-only) не трогаем.
    op.drop_constraint("ck_checks_source", "checks", type_="check")
    op.create_check_constraint(
        "ck_checks_source",
        "checks",
        "source IN ('mvd', 'rosfinmon', 'fssp', 'fedresurs', 'fns', 'fns_inn', 'fns_npd', 'fns_egrip', 'bki', 'blacklist')",
    )

    # --- fssp_requests (старый протокол api-ip.fssp.gov.ru) -> удаляем ---
    # Закрыт с 2022 (ваше явное указание) — опрашивать нечего, строки этой
    # таблицы технические (правило 1 CLAUDE.md, исключение), не бизнес-факт,
    # поэтому дропаем как есть, без переноса данных.
    op.drop_table("fssp_requests")

    # --- fssp_captcha_sessions (новый протокол: капча решает оператор) ---
    op.create_table(
        "fssp_captcha_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("subject_type", sa.Text, nullable=False, server_default="applicant"),
        sa.Column("subject_id", UUID(as_uuid=True)),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("region_code", sa.Text, nullable=False),
        sa.Column("code_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending_captcha"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending_captcha', 'wrong_code', 'expired', 'resolved')",
            name="ck_fssp_captcha_sessions_status",
        ),
        sa.CheckConstraint(
            "subject_type IN ('applicant', 'guarantor')", name="ck_fssp_captcha_sessions_subject_type",
        ),
    )
    op.create_index(
        "ix_fssp_captcha_sessions_application", "fssp_captcha_sessions",
        ["application_id", "subject_type", "subject_id"],
    )

    # --- fssp_post_decision_alerts (T16, п.3) -----------------------------
    # append-only (правило 1 CLAUDE.md) — факт-лог "решение уже было принято,
    # а новый результат ФССП сработал бы на hard_stop", не техническое
    # состояние: статус заявки это НЕ меняет, запись только для видимости.
    op.create_table(
        "fssp_post_decision_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True)),
        sa.Column("check_id", UUID(as_uuid=True), sa.ForeignKey("checks.id"), nullable=False),
        sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "subject_type IN ('applicant', 'guarantor')", name="ck_fssp_post_decision_alerts_subject_type",
        ),
    )
    op.create_index(
        "ix_fssp_post_decision_alerts_application", "fssp_post_decision_alerts", ["application_id"],
    )


def downgrade() -> None:
    op.drop_table("fssp_post_decision_alerts")
    op.drop_table("fssp_captcha_sessions")

    op.create_table(
        "fssp_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("stage", sa.Text, nullable=False, server_default="A"),
        sa.Column("subject_type", sa.Text, nullable=False, server_default="applicant"),
        sa.Column("subject_id", UUID(as_uuid=True)),
        sa.Column("external_request_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
        sa.Column("result", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('pending', 'done', 'expired')", name="ck_fssp_requests_status"),
        sa.CheckConstraint("stage IN ('A', 'B')", name="ck_fssp_requests_stage"),
        sa.CheckConstraint("subject_type IN ('applicant', 'guarantor')", name="ck_fssp_requests_subject_type"),
    )
    op.create_index("ix_fssp_requests_status", "fssp_requests", ["status"])

    op.drop_constraint("ck_checks_source", "checks", type_="check")
    op.create_check_constraint(
        "ck_checks_source",
        "checks",
        "source IN ('mvd', 'rosfinmon', 'fssp', 'fedresurs', 'fns', 'bki', 'blacklist')",
    )

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ("
        "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference', 'retry_check', "
        "'import_data', 'link_contract')",
    )

    op.drop_constraint("ck_employees_role", "employees", type_="check")
    op.create_check_constraint(
        "ck_employees_role", "employees", "role IN ('risk_manager', 'admin', 'viewer', 'compliance')",
    )
