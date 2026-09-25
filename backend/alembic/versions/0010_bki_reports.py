"""bki_reports (append-only billing ledger) + bki_requests_in_flight
(double-payment protection, DB-level) — T11

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bki_reports",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("report_raw", postgresql.JSONB()),
        sa.Column("features", postgresql.JSONB()),
        sa.Column("score", sa.Numeric(6, 2)),
        sa.Column("error_text", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('ok', 'no_data', 'source_unavailable', 'error')", name="ck_bki_reports_status"
        ),
    )
    op.create_index(
        "ix_bki_reports_client_id_requested_at", "bki_reports", ["client_id", sa.text("requested_at DESC")]
    )

    op.create_table(
        "bki_requests_in_flight",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="in_flight"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('in_flight', 'done', 'failed')", name="ck_bki_in_flight_status"),
    )
    op.create_index(
        "ux_bki_in_flight_client_id_pending",
        "bki_requests_in_flight",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_flight'"),
    )


def downgrade() -> None:
    op.drop_index("ux_bki_in_flight_client_id_pending", table_name="bki_requests_in_flight")
    op.drop_table("bki_requests_in_flight")
    op.drop_index("ix_bki_reports_client_id_requested_at", table_name="bki_reports")
    op.drop_table("bki_reports")
