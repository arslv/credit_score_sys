import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

STAGES = ("A", "B")
DECISION_OUTCOMES = ("approve", "reject", "manual_review")
SCORE_SOURCES = ("bki_score", "own_model")


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        sa.CheckConstraint(f"stage IN {STAGES!r}", name="ck_feature_snapshots_stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    feature_set_ver: Mapped[str] = mapped_column(sa.Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        sa.CheckConstraint(f"stage IN {STAGES!r}", name="ck_decisions_stage"),
        sa.CheckConstraint(f"outcome IN {DECISION_OUTCOMES!r}", name="ck_decisions_outcome"),
        sa.CheckConstraint(
            f"score_source IS NULL OR score_source IN {SCORE_SOURCES!r}",
            name="ck_decisions_score_source",
        ),
        sa.Index("ix_decisions_application_id_created_at", "application_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    outcome: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(sa.Text)
    triggered_rules: Mapped[dict] = mapped_column(JSONB, nullable=False)
    score: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(6, 2))
    score_source: Mapped[str | None] = mapped_column(sa.Text)
    model_version: Mapped[str | None] = mapped_column(sa.Text)
    ruleset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("ruleset_versions.id"), nullable=False
    )
    approved_amount: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(14, 2))
    approved_term: Mapped[int | None] = mapped_column(sa.Integer)
    offered_tariff: Mapped[str | None] = mapped_column(sa.Text)
    required_down_pay: Mapped[sa.Numeric | None] = mapped_column(sa.Numeric(14, 2))
    feature_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("feature_snapshots.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
