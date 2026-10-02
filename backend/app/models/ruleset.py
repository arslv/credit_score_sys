import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RulesetVersion(Base):
    __tablename__ = "ruleset_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, unique=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # sha256 от канонической сериализации config — сравнение с хешем файла при
    # старте приложения (см. app.engine.versions.sync_ruleset_from_file, T08).
    config_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("employees.id")
    )
    # "Активная" версия определяется чисто по effective_from/version (см.
    # app.engine.versions.get_active_ruleset_version) — effective_to никогда не
    # читался и не писался нигде в коде; убран миграцией 0014 (T14, ваше явное
    # указание), чтобы не выглядеть рабочим механизмом для будущей сессии.
    effective_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
