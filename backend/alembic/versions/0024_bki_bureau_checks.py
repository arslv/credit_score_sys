"""Проверки из подробного отчёта бюро — отдельно от наших.

bki_bureau_checks: ФССП, банкротство, паспорт, ИНН и т. п. из пакета бюро
(Спектрум Дата, «Премиум») — append-only; в карточке рядом с нашими с
пометкой «от БКИ», в решение не входят.

Revision ID: 0024
Revises: 0023
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bki_bureau_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("block", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("parsed", postgresql.JSONB()),
        sa.Column("report_type", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bki_bureau_checks_application", "bki_bureau_checks", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_bki_bureau_checks_application", table_name="bki_bureau_checks")
    op.drop_table("bki_bureau_checks")
