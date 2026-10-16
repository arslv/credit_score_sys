"""T23: три роли вместо пяти — admin, operator, viewer (решение владельца).

Обязанности risk_manager (правила, аналитика, полный доступ к персданным)
и compliance (разбор совпадений с перечнем Росфинмониторинга) переходят к
admin; разбор совпадений — также к operator. Действующие сотрудники с
ролями risk_manager и compliance становятся admin — ни один не теряет
доступа к тому, что делал раньше.

Revision ID: 0019
Revises: 0018
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE employees SET role = 'admin' WHERE role IN ('risk_manager', 'compliance')")
    op.drop_constraint("ck_employees_role", "employees", type_="check")
    op.create_check_constraint("ck_employees_role", "employees", "role IN ('admin', 'operator', 'viewer')")


def downgrade() -> None:
    # Обратного разделения ролей нет (кто из admin был риск-менеджером — не
    # сохранено); возвращаем только прежний список допустимых значений.
    op.drop_constraint("ck_employees_role", "employees", type_="check")
    op.create_check_constraint(
        "ck_employees_role", "employees", "role IN ('risk_manager', 'admin', 'viewer', 'compliance', 'operator')",
    )
