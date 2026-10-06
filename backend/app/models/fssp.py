import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FSSP_CAPTCHA_SESSION_STATUSES = ("pending_captcha", "wrong_code", "expired", "resolved")
FSSP_CAPTCHA_SESSION_SUBJECT_TYPES = ("applicant", "guarantor")


class FsspCaptchaSession(Base):
    """Состояние диалога с сайтом ФССП «показали картинку -> оператор вводит
    код» (T16) — техническое состояние конкретной попытки, не бизнес-факт
    (правило 1 CLAUDE.md, исключение рядом с delinquency_daily/
    fssp_requests/bki_requests_in_flight), строка обновляется на месте.
    Итоговый результат проверки (найдено/не найдено/ошибка) — как и у любого
    источника, в append-only `checks` (source=fssp), эта таблица только
    координирует человека с сайтом, ничего финального не хранит сверх
    технических деталей попытки.

    Одна активная сессия на (application_id, subject_type, subject_id) —
    ваше явное указание: новый поиск помечает предыдущую строку status=expired
    (а не удаляет её), code_id из неё перестаёт приниматься эндпоинтом
    submit-code (см. app/api/fssp.py)."""

    __tablename__ = "fssp_captcha_sessions"
    __table_args__ = (
        sa.CheckConstraint(f"status IN {FSSP_CAPTCHA_SESSION_STATUSES!r}", name="ck_fssp_captcha_sessions_status"),
        sa.CheckConstraint(
            f"subject_type IN {FSSP_CAPTCHA_SESSION_SUBJECT_TYPES!r}", name="ck_fssp_captcha_sessions_subject_type"
        ),
        sa.Index("ix_fssp_captcha_sessions_application", "application_id", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="applicant")
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False
    )
    region_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # code_id с сайта ФССП — техническая ссылка на конкретную капчу, не сам
    # код и не картинка (ваше явное указание: ни то ни другое никуда не
    # пишем, ни в логи, ни в аудит, см. app/api/fssp.py).
    code_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="pending_captcha")
    attempt_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class FsspPostDecisionAlert(Base):
    """T16, ваше явное указание (п.3 правок): результат ФССП пришёл, когда
    решение стадии Б (или отказ стадии А) уже принято, и он сработал бы на
    hard_stop fssp_credit_debt, если бы был известен вовремя. Статус заявки
    НЕ меняем (правило 12 CLAUDE.md — не трогаем уже принятое решение), но
    запоминаем факт — append-only (это факт-лог, не техническое состояние,
    в отличие от fssp_captcha_sessions выше): видно в карточке заявки
    (предупреждение) и счётчиком на экране состояния системы
    (app/api/system.py)."""

    __tablename__ = "fssp_post_decision_alerts"
    __table_args__ = (
        sa.CheckConstraint(
            f"subject_type IN {FSSP_CAPTCHA_SESSION_SUBJECT_TYPES!r}",
            name="ck_fssp_post_decision_alerts_subject_type",
        ),
        sa.Index("ix_fssp_post_decision_alerts_application", "application_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    check_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("checks.id"), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("decisions.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
