"""T12: сессии сотрудников и решения комплаенса (снятие заморозки, отметка
ложного совпадения). Сессии — не append-only (техническое состояние входа,
как fssp_requests/bki_requests_in_flight), freeze_lifts/compliance_reviews —
append-only (правило 1 CLAUDE.md): снятие заморозки и разбор совпадения —
решения человека, которые не должны перезаписываться или исчезать."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

COMPLIANCE_REVIEW_VERDICTS = ("false_positive", "confirmed")


class Session(Base):
    """Непрозрачный токен сессии — хранится только его хеш (тот же принцип, что
    у password_hash): утечка БД не даёт живых токенов напрямую. Абсолютный
    срок (expires_at) и тайм-аут простоя (last_seen_at + конфиг) проверяются
    вместе в app/security/sessions.py."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(sa.Text)


class FreezeLift(Base):
    """Снятие заморозки по санкциям (T09 decisions.freeze, T12 — кто и почему
    снял). НЕ трогает decisions (правило 1) — app.engine.freeze сверяет самое
    свежее из freeze-решения и снятия по времени."""

    __tablename__ = "freeze_lifts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False, index=True
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("decisions.id"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ComplianceReview(Base):
    """Разбор совпадения по перечню — по паре (клиент, конкретная запись
    перечня). false_positive подавляет будущие hit ТОЛЬКО для этой пары (см.
    app/engine/compliance_overrides.py) — не отменяет уже записанный Check
    (правило 1), не трогает decisions.

    sanctions_entry_key — устойчивый признак записи (официальный id из перечня,
    если он есть в файле, иначе нормализованные ФИО+ДР записи —
    app/sources/name_normalize.sanctions_entry_key), НЕ sanctions_list.id и без
    FK на него: реестр целиком перезагружается атомарной подменой таблицы
    (app/registries/sync.py: rename + DROP старой) — при НЕИЗМЕННОЙ записи её
    id всё равно меняется на каждой синхронизации. Ключ по id тогда слетал бы
    при каждой перезаливке реестра (пометка «ложное совпадение» переставала бы
    действовать на следующий день без каких-либо изменений записи — не то, что
    нужно). При этом если запись ДЕЙСТВИТЕЛЬНО изменилась (другое ФИО/ДР) или
    совпадение — с другой записью, ключ меняется и подавление обоснованно не
    применяется (ваше уточнение)."""

    __tablename__ = "compliance_reviews"
    __table_args__ = (
        sa.CheckConstraint(f"verdict IN {COMPLIANCE_REVIEW_VERDICTS!r}", name="ck_compliance_reviews_verdict"),
        sa.Index("ix_compliance_reviews_client_id_sanctions_entry_key", "client_id", "sanctions_entry_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False)
    sanctions_entry_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("decisions.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(sa.Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
