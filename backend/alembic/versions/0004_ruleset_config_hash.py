"""ruleset_versions: config_hash for startup file/DB sync

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ruleset_versions", sa.Column("config_hash", sa.CHAR(64), nullable=False))


def downgrade() -> None:
    op.drop_column("ruleset_versions", "config_hash")
