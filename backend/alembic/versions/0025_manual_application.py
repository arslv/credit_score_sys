"""Заявка, заведённая вручную в админке (решение владельца 09.11.2026).

- applications.source = 'manual': заявки нет в CRM — решения по ней в CRM
  не отправляются (app/integrations/adapter.py), видны только в админке.
- Действие аудита create_application: кто завёл заявку.

Revision ID: 0025
Revises: 0024
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS_0023 = (
    "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
    "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
    "'manage_employee', 'manage_reference', 'retry_check', "
    "'import_data', 'link_contract', "
    "'fssp_search_started', 'fssp_code_submitted', 'fedresurs_manual_check', 'change_setting', "
    "'request_bki_report'"
)


def upgrade() -> None:
    op.drop_constraint("ck_applications_source", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_source", "applications", "source IN ('live', 'historical_import', 'manual')",
    )
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0023}, 'create_application')",
    )


def downgrade() -> None:
    # Ручные заявки и записи аудита не удаляем (правило 1 CLAUDE.md) —
    # откат невозможен, пока они есть: ограничение не встанет.
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0023})")
    op.drop_constraint("ck_applications_source", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_source", "applications", "source IN ('live', 'historical_import')",
    )
