"""T14: ruleset_simulations — гейт "сохранение только после симуляции" (ваше
явное указание); drop ruleset_versions.effective_to — колонка никогда не
читалась и не писалась (активная версия определяется чисто по
effective_from/version, см. app.engine.versions.get_active_ruleset_version),
оставлять как приманку для будущей путаницы не стоит (ваше явное указание).

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ruleset_versions", "effective_to")

    op.create_table(
        "ruleset_simulations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("config_hash", sa.CHAR(64), nullable=False),
        sa.Column(
            "active_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ruleset_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("summary", postgresql.JSONB, nullable=False),
    )
    op.create_index(
        "ix_ruleset_simulations_requested_by", "ruleset_simulations", ["requested_by"],
    )


def downgrade() -> None:
    op.drop_index("ix_ruleset_simulations_requested_by", table_name="ruleset_simulations")
    op.drop_table("ruleset_simulations")

    op.add_column("ruleset_versions", sa.Column("effective_to", sa.DateTime(timezone=True)))
