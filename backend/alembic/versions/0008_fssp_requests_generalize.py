"""fssp_requests: generalize to applicant/guarantor, stage A/B (T10)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fssp_requests", sa.Column("stage", sa.Text(), nullable=False, server_default="A"))
    op.add_column(
        "fssp_requests", sa.Column("subject_type", sa.Text(), nullable=False, server_default="applicant")
    )
    op.add_column("fssp_requests", sa.Column("subject_id", postgresql.UUID(as_uuid=True)))
    op.create_check_constraint("ck_fssp_requests_stage", "fssp_requests", "stage IN ('A', 'B')")
    op.create_check_constraint(
        "ck_fssp_requests_subject_type", "fssp_requests", "subject_type IN ('applicant', 'guarantor')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_fssp_requests_subject_type", "fssp_requests", type_="check")
    op.drop_constraint("ck_fssp_requests_stage", "fssp_requests", type_="check")
    op.drop_column("fssp_requests", "subject_id")
    op.drop_column("fssp_requests", "subject_type")
    op.drop_column("fssp_requests", "stage")
