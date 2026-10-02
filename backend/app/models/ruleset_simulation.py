import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RulesetSimulation(Base):
    """Билет «симуляция была сделана против конкретного конфига и конкретной
    активной версии» (T14, ваше явное указание, п.3) — гейт для сохранения:

    - config_hash сохраняемого YAML должен совпасть с сохранённым здесь —
      иначе человек симулировал одно, а сохраняет другое.
    - active_version_id должен совпасть с ТЕКУЩЕЙ активной версией на момент
      сохранения — иначе кто-то другой уже сохранил (или пришла версия из
      файла) между симуляцией и сохранением, и сравнение в summary больше не
      про то, с чем реально будут сравнивать пороги.
    - requested_at + settings.ruleset_simulation_ttl_hours — просроченная
      симуляция (по умолчанию старше суток) тоже не годится для сохранения,
      даже если обе версии совпали: слишком долго думали, конфиг вокруг мог
      обновиться данными (новые заявки/снимки), сравнение устарело по сути.

    Append-only, как и остальные таблицы решений (правило 1 CLAUDE.md) —
    сама запись не бизнес-факт, но история "кто что симулировал и когда"
    полезна для разбора, лишний UPDATE здесь не нужен."""

    __tablename__ = "ruleset_simulations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    config_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    active_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("ruleset_versions.id"), nullable=False
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False, index=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    sample_size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
