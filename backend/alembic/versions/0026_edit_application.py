"""Правка анкеты сотрудником (решение владельца 16.11.2026): действие аудита
edit_application — кто и когда поправил анкету (заявка затем проверяется
заново).

Revision ID: 0026
Revises: 0025
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS_0025 = (
    "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
    "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
    "'manage_employee', 'manage_reference', 'retry_check', "
    "'import_data', 'link_contract', "
    "'fssp_search_started', 'fssp_code_submitted', 'fedresurs_manual_check', 'change_setting', "
    "'request_bki_report', 'create_application'"
)


def upgrade() -> None:
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0025}, 'edit_application')",
    )


def downgrade() -> None:
    # Записи аудита не удаляем (правило 1 CLAUDE.md): откат невозможен, пока они есть.
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0025})")
