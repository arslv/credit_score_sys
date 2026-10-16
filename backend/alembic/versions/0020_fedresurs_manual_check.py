"""T23: ручная проверка банкротства на Федресурсе — новое действие аудита.

Сайт fedresurs.ru / bankrot.fedresurs.ru закрыт защитой от ботов (Qrator,
JS-проверка), автоматически его не опросить без обхода защиты — этого не
делаем. Сотрудник проверяет на сайте сам и отмечает результат в карточке:
строка checks(source='fedresurs') (значение уже разрешено ck_checks_source)
и запись в audit_log с новым действием 'fedresurs_manual_check'.

Revision ID: 0020
Revises: 0019
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS_0017 = (
    "'view_application', 'view_document', 'view_credit_report', 'change_ruleset', 'export', "
    "'login', 'login_failed', 'logout', 'lift_freeze', 'mark_false_positive', "
    "'manage_employee', 'manage_reference', 'retry_check', "
    "'import_data', 'link_contract', "
    "'fssp_search_started', 'fssp_code_submitted'"
)


def upgrade() -> None:
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0017}, 'fedresurs_manual_check')",
    )


def downgrade() -> None:
    # Строки с 'fedresurs_manual_check' (append-only) остаются — downgrade с
    # ними упадёт на ограничении, это честнее, чем молча их удалить.
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({_ACTIONS_0017})")
