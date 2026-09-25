"""manual_overrides.employee_id nullable + reason_codes seed for
system-detected partner-operator overrides (T10).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED = [
    ("PARTNER_OPERATOR_OVERRIDE_APPROVE", "override_approve", "Оператор в системе заказчика одобрил вопреки нашему решению"),
    ("PARTNER_OPERATOR_OVERRIDE_REJECT", "override_reject", "Оператор в системе заказчика отклонил вопреки нашему решению"),
]

reason_codes = sa.table(
    "reason_codes",
    sa.column("code", sa.Text),
    sa.column("kind", sa.Text),
    sa.column("title", sa.Text),
    sa.column("active", sa.Boolean),
)


def upgrade() -> None:
    op.alter_column("manual_overrides", "employee_id", nullable=True)
    op.bulk_insert(
        reason_codes,
        [{"code": code, "kind": kind, "title": title, "active": True} for code, kind, title in _SEED],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM reason_codes WHERE code IN ({})".format(
            ", ".join(f"'{code}'" for code, *_ in _SEED)
        )
    )
    op.alter_column("manual_overrides", "employee_id", nullable=False)
