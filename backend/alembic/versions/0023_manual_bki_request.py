"""Запрос в БКИ — по кнопке сотрудника (по решению владельца).

- Статус заявки awaiting_bki: стадия Б прошла дешёвые проверки и ждёт, пока
  сотрудник выберет вид отчёта («обычный» или «подробный»).
- bki_request_orders — кто и какой отчёт заказал (append-only, правило 1).
- bki_reports.report_type — вид отчёта в журнале платных обращений
  (NULL у прежних строк — вид тогда не выбирался).
- Действие аудита request_bki_report.

Revision ID: 0023
Revises: 0022
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUSES_0021 = (
    "'received', 'ocr', 'ocr_failed', 'stage_a', 'rejected_a', "
    "'to_operator', 'stage_b', 'rejected_b', 'gray_zone', 'approved', "
    "'contracted', 'expired', 'awaiting_data'"
)
_ACTIONS_0021 = (
    "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
    "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
    "'manage_employee', 'manage_reference', 'retry_check', "
    "'import_data', 'link_contract', "
    "'fssp_search_started', 'fssp_code_submitted', 'fedresurs_manual_check', 'change_setting'"
)


def upgrade() -> None:
    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status", "applications", f"status IN ({_STATUSES_0021}, 'awaiting_bki')",
    )

    op.create_table(
        "bki_request_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_bki_request_orders_application_created",
        "bki_request_orders", ["application_id", sa.text("created_at DESC")],
    )

    op.add_column("bki_reports", sa.Column("report_type", sa.Text(), nullable=True))

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0021}, 'request_bki_report')",
    )


def downgrade() -> None:
    # Строки с awaiting_bki / request_bki_report (если есть) не удаляем —
    # downgrade с ними упадёт на ограничении, это честнее, чем молча их стереть.
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0021})")

    op.drop_column("bki_reports", "report_type")
    op.drop_index("ix_bki_request_orders_application_created", table_name="bki_request_orders")
    op.drop_table("bki_request_orders")

    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint("ck_applications_status", "applications", f"status IN ({_STATUSES_0021})")
