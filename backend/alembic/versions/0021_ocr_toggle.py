"""Тумблер распознавания паспорта в админке.

- system_settings — настройки сервиса, которые меняет админ (append-only:
  каждое изменение — новая строка, действует последняя по key).
- Статус заявки awaiting_data — распознавание выключено, заявка ждёт
  паспортные данные от CRM в /review.
- Действие аудита change_setting.

Revision ID: 0021
Revises: 0020
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUSES_0002 = (
    "'received', 'ocr', 'ocr_failed', 'stage_a', 'rejected_a', "
    "'to_operator', 'stage_b', 'rejected_b', 'gray_zone', 'approved', "
    "'contracted', 'expired'"
)
_ACTIONS_0020 = (
    "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
    "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
    "'manage_employee', 'manage_reference', 'retry_check', "
    "'import_data', 'link_contract', "
    "'fssp_search_started', 'fssp_code_submitted', 'fedresurs_manual_check'"
)


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_system_settings_key_created_at", "system_settings", ["key", sa.text("created_at DESC")],
    )

    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status", "applications", f"status IN ({_STATUSES_0002}, 'awaiting_data')",
    )

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0020}, 'change_setting')",
    )


def downgrade() -> None:
    # Строки с awaiting_data / change_setting (если есть) не удаляем — downgrade
    # с ними упадёт на ограничении, это честнее, чем молча их стереть.
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0020})")

    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint("ck_applications_status", "applications", f"status IN ({_STATUSES_0002})")

    op.drop_index("ix_system_settings_key_created_at", table_name="system_settings")
    op.drop_table("system_settings")
