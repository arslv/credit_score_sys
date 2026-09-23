"""integration adapter: idempotency hash and lookup indexes

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("external_snapshots", sa.Column("content_hash", sa.CHAR(64), nullable=False))
    op.execute(
        "CREATE INDEX ix_external_snapshots_application_stage_received ON external_snapshots "
        "(application_id, stage, received_at DESC)"
    )
    op.create_index("ix_documents_application_id_kind", "documents", ["application_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_documents_application_id_kind", table_name="documents")
    op.drop_index(
        "ix_external_snapshots_application_stage_received", table_name="external_snapshots"
    )
    op.drop_column("external_snapshots", "content_hash")
