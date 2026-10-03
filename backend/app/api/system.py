"""T12: состояние системы — один эндпоинт для admin (ваше уточнение): свежесть
реестров, совпадение правил с файлом, ФССП в очереди/просрочено, подключено
ли БКИ и сколько платных запросов за месяц, ошибки интеграции за сутки, заявки
без смены статуса дольше часа.

T13c (страховка после расследования петли повторной обработки стадии Б):
stage_b_reprocessing_loop_applications/status — сигнал, что где-то у заявки
за последний час появилось подозрительно много снимков или решений стадии Б.
Мы не смогли воспроизвести саму петлю после фикса content_hash, но именно
поэтому нужен монитор, а не только тест на конкретный (уже закрытый) баг —
если он вернётся другим путём, это должно быть видно на экране состояния
системы и в логе, а не только в аномальных числах в decisions/БКИ.

stuck_in_ocr (ваше явное указание после находки с 22 зависшими заявками):
заявки дольше OCR_STUCK_MINUTES в статусе "ocr" — автоматически уводятся
оператору тем же тиком, что обрабатывает новые заявки
(app/tasks/stage_a.py::recover_stuck_ocr_applications), так что в норме
счётчик всегда 0; ненулевое значение означает, что фоновая задача не
отрабатывает (beat/worker не запущены, упали и т.п.), а не разовый сбой
одной заявки — это и есть degraded, не просто предупреждающая строка."""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.engine.pipeline import find_stuck_ocr_application_ids
from app.engine.schema import load_ruleset_from_yaml
from app.engine.state_machine import ALLOWED_TRANSITIONS
from app.engine.versions import compute_config_hash, get_active_ruleset_version, load_config
from app.models import (
    Application,
    BkiReport,
    Decision,
    Employee,
    ExternalSnapshot,
    FsspRequest,
    IntegrationEvent,
    StatusHistory,
)
from app.registries.sync import is_registry_stale
from app.security.deps import require_roles

logger = structlog.get_logger()

_STAGE_B_LOOP_THRESHOLD = 3

router = APIRouter(prefix="/api/system", tags=["system"])

_REGISTRIES = ("mvd_invalid_passports", "rosfinmon_sanctions")
_TERMINAL_STATUSES = {status for status, targets in ALLOWED_TRANSITIONS.items() if not targets}


class RegistryStatus(BaseModel):
    registry: str
    stale: bool


class SystemStatus(BaseModel):
    status: Literal["ok", "degraded"]
    """Триггеры — stage_b_reprocessing_loop_applications и stuck_in_ocr ниже.
    Не свод всех warn-индикаторов на этом экране (устаревший реестр/просрочка
    ФССП и т.п. сами по себе статус не меняют — они уже видны отдельными
    строками)."""
    registries: list[RegistryStatus]
    ruleset_active_version: int | None
    ruleset_matches_file: bool | None
    fssp_pending: int
    fssp_overdue: int
    bki_configured: bool
    bki_paid_requests_this_month: int
    partner_integration_errors_last_24h: int
    stuck_applications_over_1h: int
    stuck_in_ocr: int
    """Заявки дольше OCR_STUCK_MINUTES в статусе "ocr" (ваше явное указание) —
    в норме 0 (авто-восстановление каждым тиком стадии А); ненулевое значение
    переводит status в degraded."""
    stage_b_reprocessing_loop_applications: int
    """Заявки, у которых за последний час появилось больше
    _STAGE_B_LOOP_THRESHOLD снимков стадии Б или больше
    _STAGE_B_LOOP_THRESHOLD решений стадии Б — признак петли повторной
    обработки (T13c: found bug — content_hash снимка включал наши же
    статус/комментарий; после фикса не смогли воспроизвести, но защита
    нужна на случай другого пути к тому же симптому)."""


@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    _employee: Employee = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> SystemStatus:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    session_factory = get_sessionmaker()
    ruleset_row = await get_active_ruleset_version(session)
    ruleset_active_version = ruleset_row.version if ruleset_row else None
    ruleset_matches_file: bool | None = None
    registries: list[RegistryStatus] = []

    if ruleset_row is not None:
        try:
            file_config = load_ruleset_from_yaml(settings.ruleset_path)
            ruleset_matches_file = compute_config_hash(file_config) == ruleset_row.config_hash
        except Exception:
            ruleset_matches_file = False

        max_staleness_days = load_config(ruleset_row).sources.registries.max_staleness_days
        for registry in _REGISTRIES:
            stale = await is_registry_stale(session_factory, registry=registry, max_staleness_days=max_staleness_days)
            registries.append(RegistryStatus(registry=registry, stale=stale))

    fssp_pending = (
        await session.execute(select(func.count()).select_from(FsspRequest).where(FsspRequest.status == "pending"))
    ).scalar_one()
    fssp_overdue = (
        await session.execute(
            select(func.count())
            .select_from(FsspRequest)
            .where(FsspRequest.status == "pending", FsspRequest.deadline < now)
        )
    ).scalar_one()

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    bki_paid_requests_this_month = (
        await session.execute(
            select(func.count()).select_from(BkiReport).where(BkiReport.requested_at >= month_start)
        )
    ).scalar_one()

    day_ago = now - timedelta(hours=24)
    partner_errors = (
        await session.execute(
            select(func.count())
            .select_from(IntegrationEvent)
            .where(IntegrationEvent.status == "error", IntegrationEvent.created_at >= day_ago)
        )
    ).scalar_one()

    hour_ago = now - timedelta(hours=1)
    last_change_subq = (
        select(StatusHistory.application_id, func.max(StatusHistory.created_at).label("last_change"))
        .group_by(StatusHistory.application_id)
        .subquery()
    )
    stuck = (
        await session.execute(
            select(func.count())
            .select_from(Application)
            .join(last_change_subq, last_change_subq.c.application_id == Application.id)
            .where(Application.status.notin_(_TERMINAL_STATUSES), last_change_subq.c.last_change < hour_ago)
        )
    ).scalar_one()

    heavy_snapshots = (
        select(ExternalSnapshot.application_id)
        .where(ExternalSnapshot.stage == "B", ExternalSnapshot.received_at >= hour_ago)
        .group_by(ExternalSnapshot.application_id)
        .having(func.count() > _STAGE_B_LOOP_THRESHOLD)
    )
    heavy_decisions = (
        select(Decision.application_id)
        .where(Decision.stage == "B", Decision.created_at >= hour_ago)
        .group_by(Decision.application_id)
        .having(func.count() > _STAGE_B_LOOP_THRESHOLD)
    )
    stage_b_loop_ids: list[UUID] = (
        await session.execute(select(heavy_snapshots.union(heavy_decisions).subquery().c.application_id))
    ).scalars().all()
    stage_b_reprocessing_loop_applications = len(stage_b_loop_ids)

    if stage_b_reprocessing_loop_applications:
        logger.error(
            "stage_b_reprocessing_loop_detected",
            application_ids=[str(i) for i in stage_b_loop_ids],
            threshold=_STAGE_B_LOOP_THRESHOLD,
        )

    ocr_cutoff = now - timedelta(minutes=settings.ocr_stuck_minutes)
    stuck_in_ocr_ids = await find_stuck_ocr_application_ids(session, cutoff=ocr_cutoff)
    stuck_in_ocr = len(stuck_in_ocr_ids)
    if stuck_in_ocr:
        logger.error(
            "stage_a_ocr_stuck_detected",
            application_ids=[str(i) for i in stuck_in_ocr_ids],
            stuck_minutes=settings.ocr_stuck_minutes,
        )

    return SystemStatus(
        status="degraded" if (stage_b_reprocessing_loop_applications or stuck_in_ocr) else "ok",
        registries=registries,
        ruleset_active_version=ruleset_active_version,
        ruleset_matches_file=ruleset_matches_file,
        fssp_pending=fssp_pending,
        fssp_overdue=fssp_overdue,
        bki_configured=settings.bki_provider_mode != "not_configured",
        bki_paid_requests_this_month=bki_paid_requests_this_month,
        partner_integration_errors_last_24h=partner_errors,
        stuck_applications_over_1h=stuck,
        stuck_in_ocr=stuck_in_ocr,
        stage_b_reprocessing_loop_applications=stage_b_reprocessing_loop_applications,
    )
