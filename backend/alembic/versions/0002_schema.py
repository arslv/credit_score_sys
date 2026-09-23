"""schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

uuid_pk = lambda: sa.Column(
    "id",
    postgresql.UUID(as_uuid=True),
    primary_key=True,
    server_default=sa.text("gen_random_uuid()"),
)
timestamptz_now = lambda name="created_at": sa.Column(
    name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "employees",
        uuid_pk(),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        timestamptz_now(),
        sa.CheckConstraint("role IN ('risk_manager', 'admin', 'viewer')", name="ck_employees_role"),
    )

    op.create_table(
        "goods_categories",
        uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("sharia_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("liquidity", sa.Text(), nullable=False),
        sa.Column("lgd_estimate", sa.Numeric(4, 3)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "liquidity IN ('high', 'medium', 'low')", name="ck_goods_categories_liquidity"
        ),
    )

    op.create_table(
        "reason_codes",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "kind IN ('reject', 'override_approve', 'override_reject')",
            name="ck_reason_codes_kind",
        ),
    )

    op.create_table(
        "clients",
        uuid_pk(),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("middle_name", sa.Text()),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("gender", sa.Text()),
        sa.Column("passport_series", sa.CHAR(4), nullable=False),
        sa.Column("passport_number", sa.CHAR(6), nullable=False),
        sa.Column("inn", sa.CHAR(12)),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        timestamptz_now(),
        sa.UniqueConstraint("passport_series", "passport_number"),
    )

    op.create_table(
        "applications",
        uuid_pk(),
        # Клиент известен только после успешного распознавания паспорта (модуль OCR).
        # До этого момента есть лишь ФИО и телефон из формы, их недостаточно для clients.
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id")),
        sa.Column("external_id", sa.Text(), nullable=False, unique=True),
        sa.Column("number", sa.BigInteger(), sa.Identity(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("goods_name", sa.Text(), nullable=False),
        sa.Column(
            "goods_category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goods_categories.id")
        ),
        sa.Column("merchant_name", sa.Text()),
        sa.Column("merchant_city", sa.Text()),
        sa.Column("goods_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("down_payment", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.Text(), nullable=False),
        sa.Column("requested_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("ip", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("device_fingerprint", sa.Text()),
        sa.Column("form_timings", postgresql.JSONB()),
        sa.Column("utm", postgresql.JSONB()),
        sa.Column("consent_pd_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_bki_at", sa.DateTime(timezone=True)),
        timestamptz_now("submitted_at"),
        timestamptz_now(),
        sa.CheckConstraint(
            "status IN ('received', 'ocr', 'ocr_failed', 'stage_a', 'rejected_a', "
            "'to_operator', 'stage_b', 'rejected_b', 'gray_zone', 'approved', "
            "'contracted', 'expired')",
            name="ck_applications_status",
        ),
    )
    op.create_index("ix_applications_client_id", "applications", ["client_id"])
    op.execute(
        "CREATE INDEX ix_applications_status_submitted_at ON applications "
        "(status, submitted_at DESC)"
    )

    op.create_table(
        "guarantors",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id")),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        timestamptz_now(),
    )

    op.create_table(
        "ruleset_versions",
        uuid_pk(),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        timestamptz_now(),
    )

    op.create_table(
        "feature_snapshots",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("feature_set_ver", sa.Text(), nullable=False),
        timestamptz_now("computed_at"),
        sa.CheckConstraint("stage IN ('A', 'B')", name="ck_feature_snapshots_stage"),
    )

    op.create_table(
        "decisions",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text()),
        sa.Column("triggered_rules", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Numeric(6, 2)),
        sa.Column("score_source", sa.Text()),
        sa.Column("model_version", sa.Text()),
        sa.Column(
            "ruleset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ruleset_versions.id"),
            nullable=False,
        ),
        sa.Column("approved_amount", sa.Numeric(14, 2)),
        sa.Column("approved_term", sa.Integer()),
        sa.Column("offered_tariff", sa.Text()),
        sa.Column("required_down_pay", sa.Numeric(14, 2)),
        sa.Column(
            "feature_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("feature_snapshots.id")
        ),
        timestamptz_now(),
        sa.CheckConstraint("stage IN ('A', 'B')", name="ck_decisions_stage"),
        sa.CheckConstraint(
            "outcome IN ('approve', 'reject', 'manual_review')", name="ck_decisions_outcome"
        ),
        sa.CheckConstraint(
            "score_source IS NULL OR score_source IN ('bki_score', 'own_model')",
            name="ck_decisions_score_source",
        ),
    )
    op.create_index(
        "ix_decisions_application_id_created_at", "decisions", ["application_id", "created_at"]
    )

    op.create_table(
        "manual_overrides",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("decisions.id")),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id"),
            nullable=False,
        ),
        sa.Column("from_outcome", sa.Text(), nullable=False),
        sa.Column("to_outcome", sa.Text(), nullable=False),
        sa.Column(
            "reason_code", sa.Text(), sa.ForeignKey("reason_codes.code"), nullable=False
        ),
        sa.Column("comment", sa.Text()),
        timestamptz_now(),
    )

    op.create_table(
        "checks",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB()),
        sa.Column("response_raw", postgresql.JSONB()),
        sa.Column("parsed", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_text", sa.Text()),
        sa.CheckConstraint(
            "subject_type IN ('applicant', 'guarantor')", name="ck_checks_subject_type"
        ),
        sa.CheckConstraint(
            "source IN ('mvd', 'rosfinmon', 'fssp', 'fedresurs', 'fns', 'bki', 'blacklist')",
            name="ck_checks_source",
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'hit', 'no_data', 'source_unavailable', 'error')",
            name="ck_checks_status",
        ),
    )
    op.create_index("ix_checks_application_id_source", "checks", ["application_id", "source"])

    op.create_table(
        "contracts",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("external_number", sa.Text(), unique=True),
        sa.Column("principal", sa.Numeric(14, 2), nullable=False),
        sa.Column("markup", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("down_payment", sa.Numeric(14, 2), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.Date()),
        sa.Column("status", sa.Text(), nullable=False),
        timestamptz_now(),
        sa.CheckConstraint(
            "status IN ('active', 'closed', 'defaulted', 'repossessed')", name="ck_contracts_status"
        ),
    )

    op.create_table(
        "payment_schedule",
        uuid_pk(),
        sa.Column(
            "contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False
        ),
        sa.Column("period_no", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("due_amount", sa.Numeric(14, 2), nullable=False),
        sa.UniqueConstraint("contract_id", "period_no"),
    )

    op.create_table(
        "payments",
        uuid_pk(),
        sa.Column(
            "contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False
        ),
        sa.Column("paid_at", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        timestamptz_now("imported_at"),
        sa.CheckConstraint("source IN ('import', 'manual')", name="ck_payments_source"),
    )

    op.create_table(
        "delinquency_daily",
        sa.Column(
            "contract_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contracts.id"),
            primary_key=True,
        ),
        sa.Column("as_of", sa.Date(), primary_key=True),
        sa.Column("days_past_due", sa.Integer(), nullable=False),
        sa.Column("overdue_amount", sa.Numeric(14, 2), nullable=False),
    )

    op.create_table(
        "blacklist",
        uuid_pk(),
        sa.Column("last_name", sa.Text()),
        sa.Column("first_name", sa.Text()),
        sa.Column("middle_name", sa.Text()),
        sa.Column("birth_date", sa.Date()),
        sa.Column("passport_series", sa.CHAR(4)),
        sa.Column("passport_number", sa.CHAR(6)),
        sa.Column("phone", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        timestamptz_now("added_at"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "invalid_passports",
        sa.Column("passport_series", sa.CHAR(4), primary_key=True),
        sa.Column("passport_number", sa.CHAR(6), primary_key=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "sanctions_list",
        uuid_pk(),
        sa.Column("list_type", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("extra", postgresql.JSONB()),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "list_type IN ('te', 'fromu', 'mvk', 'un')", name="ck_sanctions_list_type"
        ),
    )
    op.execute(
        "CREATE INDEX ix_sanctions_list_name_normalized_trgm ON sanctions_list "
        "USING gin (name_normalized gin_trgm_ops)"
    )

    op.create_table(
        "registry_sync_log",
        uuid_pk(),
        sa.Column("registry", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_loaded", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_text", sa.Text()),
    )

    op.create_table(
        "documents",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("verified_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("delete_after", sa.Date()),
        timestamptz_now("uploaded_at"),
        sa.CheckConstraint(
            "kind IN ('selfie_passport', 'passport_main', 'passport_reg')", name="ck_documents_kind"
        ),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text()),
        sa.Column("object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ip", postgresql.INET()),
        sa.Column("details", postgresql.JSONB()),
        timestamptz_now(),
        sa.CheckConstraint(
            "action IN ('view_application', 'view_document', 'change_ruleset', 'export')",
            name="ck_audit_log_action",
        ),
    )

    op.create_table(
        "status_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        timestamptz_now(),
        sa.CheckConstraint(
            "actor_type IN ('system', 'employee', 'client')", name="ck_status_history_actor_type"
        ),
    )

    op.create_table(
        "external_snapshots",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("payload_raw", postgresql.JSONB(), nullable=False),
        timestamptz_now("received_at"),
        sa.CheckConstraint("stage IN ('A', 'B')", name="ck_external_snapshots_stage"),
    )

    op.create_table(
        "ocr_results",
        uuid_pk(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("response_raw", postgresql.JSONB(), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("min_confidence", sa.Numeric(4, 3)),
        timestamptz_now(),
    )

    op.create_table(
        "integration_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id")),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB()),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        timestamptz_now(),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')", name="ck_integration_events_direction"
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'conflict', 'retry')", name="ck_integration_events_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("integration_events")
    op.drop_table("ocr_results")
    op.drop_table("external_snapshots")
    op.drop_table("status_history")
    op.drop_table("audit_log")
    op.drop_table("documents")
    op.drop_table("registry_sync_log")
    op.drop_table("sanctions_list")
    op.drop_table("invalid_passports")
    op.drop_table("blacklist")
    op.drop_table("delinquency_daily")
    op.drop_table("payments")
    op.drop_table("payment_schedule")
    op.drop_table("contracts")
    op.drop_table("checks")
    op.drop_table("manual_overrides")
    op.drop_table("decisions")
    op.drop_table("feature_snapshots")
    op.drop_table("ruleset_versions")
    op.drop_table("guarantors")
    op.drop_table("applications")
    op.drop_table("clients")
    op.drop_table("reason_codes")
    op.drop_table("goods_categories")
    op.drop_table("employees")

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
