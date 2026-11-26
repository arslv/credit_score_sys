"""Удаление заявки из админки (решение владельца 26.11.2026): действие аудита
delete_application — кто и когда удалил заявку (номер и номер в CRM, без
персданных). Сама заявка удаляется целиком (app/api/applications.py,
app/cli/delete_application.py), запись аудита остаётся.

Revision ID: 0028
Revises: 0027
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS_0026 = (
    "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
    "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
    "'manage_employee', 'manage_reference', 'retry_check', "
    "'import_data', 'link_contract', "
    "'fssp_search_started', 'fssp_code_submitted', 'fedresurs_manual_check', 'change_setting', "
    "'request_bki_report', 'create_application', 'edit_application'"
)


def upgrade() -> None:
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0026}, 'delete_application')",
    )


def downgrade() -> None:
    # Записи аудита не удаляем (правило 1 CLAUDE.md): откат невозможен, пока они есть.
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0026})")
