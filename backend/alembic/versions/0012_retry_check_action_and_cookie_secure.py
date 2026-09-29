"""T13: retry_check в audit_log.action (карточка заявки — кнопка повтора для
недоступного источника, ваше уточнение T12)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference', 'retry_check')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
        "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
        "'manage_employee', 'manage_reference')",
    )
