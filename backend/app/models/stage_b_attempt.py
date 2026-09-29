import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StageBSnapshotAttempt(Base):
    """Билет «стадия Б уже бралась за этот снимок анкеты» (найденный баг:
    finalize_stage_b идемпотентна ВНУТРИ одного цикла обработки — SELECT ...
    FOR UPDATE + проверка status == "stage_b" — но run_stage_b подбирает
    заявки ПО СТАТУСУ (to_operator/gray_zone), а эти статусы, в отличие от
    "received" у стадии А, ПЕРЕИСПОЛЬЗУЮТСЯ: gray_zone/to_operator — валидные
    конечные состояния, в которые заявка возвращается ПОСЛЕ решения. Каждый
    тик celery beat находил такую заявку снова и прогонял стадию Б заново —
    тот же снимок, то же решение, новая запись в decisions/integration_events/
    bki_reports. В деве это дало тысячи дублей на одну заявку (T13c, отчёт).

    external_snapshots — append-only (правило 1 CLAUDE.md), метку "обработан"
    на неё саму не повесить. Эта таблица — отдельный append-only билет:
    ПЕРВАЯ попытка обработки снимка вставляет строку (просто INSERT, ловим
    IntegrityError на повторе); UNIQUE на snapshot_id — гарантия на уровне БД,
    а не только проверка в коде (ваше явное указание), работает даже если
    вызов придёт в обход обычного пути. Не Decision — потому что снимок мог
    быть и "неполным" (to_operator без решения вовсе), а закрывать нужно оба
    случая."""

    __tablename__ = "stage_b_snapshot_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False, index=True
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("external_snapshots.id"), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
