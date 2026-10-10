"""T18: интеграция по новой схеме (push, не polling) — таблицы дедупликации
входящих вебхуков по event_id (partner_inbound_events) и очереди исходящих
уведомлений с повторами (partner_outbox_events, CLAUDE.md правило 1,
исключение — техническое состояние доставки, не бизнес-факт, см. докстринг
app/models/partner_webhook.py). Плюс applications.partner_last_known_status —
их статус, каким они его нам последний раз сообщили (заменяет захардкоженные
"new"/"on_review" из polling-адаптера T04).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("partner_last_known_status", sa.Text(), nullable=True))

    op.create_table(
        "partner_inbound_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", sa.Text(), nullable=False, unique=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "partner_outbox_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("event_type IN ('ocr_result', 'decision')", name="ck_partner_outbox_event_type"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'conflict')", name="ck_partner_outbox_status"),
    )
    op.create_index("ix_partner_outbox_pending", "partner_outbox_events", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_partner_outbox_pending", table_name="partner_outbox_events")
    op.drop_table("partner_outbox_events")
    op.drop_table("partner_inbound_events")
    op.drop_column("applications", "partner_last_known_status")
