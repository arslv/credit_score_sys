"""Удаление сотрудника из админки (T23, по запросу владельца).

Сотрудника нельзя стереть строкой: на него ссылаются append-only таблицы
(audit_log, status_history, ruleset_versions и др., CLAUDE.md правило 1).
«Удалён» — отметка deleted_at: сотрудник пропадает из списка и не может войти
(active = false), а его прошлые действия остаются в журнале с именем.

Revision ID: 0022
Revises: 0021
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "deleted_at")
