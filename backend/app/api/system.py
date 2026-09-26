"""T12: состояние системы — один эндпоинт для admin (ваше уточнение): свежесть
реестров, совпадение правил с файлом, ФССП в очереди/просрочено, подключено
ли БКИ и сколько платных запросов за месяц, ошибки интеграции за сутки, заявки
без смены статуса дольше часа."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.engine.schema import load_ruleset_from_yaml
from app.engine.state_machine import ALLOWED_TRANSITIONS
from app.engine.versions import compute_config_hash, get_active_ruleset_version, load_config
from app.models import Application, BkiReport, Employee, FsspRequest, IntegrationEvent, StatusHistory
from app.registries.sync import is_registry_stale
from app.security.deps import require_roles

router = APIRouter(prefix="/api/system", tags=["system"])

_REGISTRIES = ("mvd_invalid_passports", "rosfinmon_sanctions")
_TERMINAL_STATUSES = {status for status, targets in ALLOWED_TRANSITIONS.items() if not targets}


class RegistryStatus(BaseModel):
    registry: str
    stale: bool


class SystemStatus(BaseModel):
    registries: list[RegistryStatus]
    ruleset_active_version: int | None
    ruleset_matches_file: bool | None
    fssp_pending: int
    fssp_overdue: int
    bki_configured: bool
    bki_paid_requests_this_month: int
    partner_integration_errors_last_24h: int
    stuck_applications_over_1h: int


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

    return SystemStatus(
        registries=registries,
        ruleset_active_version=ruleset_active_version,
        ruleset_matches_file=ruleset_matches_file,
        fssp_pending=fssp_pending,
        fssp_overdue=fssp_overdue,
        bki_configured=settings.bki_provider_mode != "not_configured",
        bki_paid_requests_this_month=bki_paid_requests_this_month,
        partner_integration_errors_last_24h=partner_errors,
        stuck_applications_over_1h=stuck,
    )
