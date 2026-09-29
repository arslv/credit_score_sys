"""Fix: stage_b_snapshot_attempts — стадия Б обрабатывает каждый снимок
анкеты ровно один раз (найденный баг: gray_zone/to_operator — переиспользуемые
статусы, celery beat пере-подбирал уже решённую заявку на каждый тик).
UNIQUE на snapshot_id — защита на уровне БД, не только в коде.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stage_b_snapshot_attempts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column(
            "snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("external_snapshots.id"),
            nullable=False, unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_stage_b_snapshot_attempts_application_id", "stage_b_snapshot_attempts", ["application_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_stage_b_snapshot_attempts_application_id", table_name="stage_b_snapshot_attempts")
    op.drop_table("stage_b_snapshot_attempts")
