"""T14: редактор правил и симуляция (ТЗ, «Конфиг» -> «Версионирование»,
«Обязательная симуляция»; «Админка» -> «Правила и пороги»). Роль экрана —
ТОЛЬКО risk_manager (ТЗ, карта экранов: «Правила и пороги | риск-менеджер») —
раньше сюда по ошибке допускался и admin, исправлено по вашему замечанию."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.engine.ruleset_simulation import ApplicationSimulationRow, SimulationSummary, run_simulation
from app.engine.schema import parse_ruleset
from app.engine.versions import compute_config_hash, get_active_ruleset_version, load_config, save_ruleset_version
from app.models import Employee, RulesetSimulation, RulesetVersion
from app.security.audit import record_audit
from app.security.deps import has_full_pii, require_roles

router = APIRouter(prefix="/api/rulesets", tags=["rulesets"])

_ROLES = ("risk_manager",)


def _parse_yaml_or_400(text: str):
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"YAML не читается: {exc}") from exc
    try:
        return parse_ruleset(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"конфиг не проходит схему: {exc}") from exc


def _row_to_dict(row: ApplicationSimulationRow, *, full_pii: bool) -> dict[str, Any]:
    return {
        "application_id": str(row.application_id),
        "external_id": row.external_id,
        "client_name": row.client_name if full_pii else None,
        "active_outcome": row.active_outcome,
        "active_reason_code": row.active_reason_code,
        "candidate_outcome": row.candidate_outcome,
        "candidate_reason_code": row.candidate_reason_code,
        "actual_decision_outcome": row.actual_decision_outcome,
        "actual_decision_reason_code": row.actual_decision_reason_code,
    }


def _summary_to_dict(summary: SimulationSummary, *, full_pii: bool) -> dict[str, Any]:
    return {
        "sample_size_requested": summary.sample_size_requested,
        "sample_size_actual": summary.sample_size_actual,
        "changed_count": summary.changed_count,
        "approval_rate_active": summary.approval_rate_active,
        "approval_rate_candidate": summary.approval_rate_candidate,
        "known_denominator": summary.known_denominator,
        "swap_in": [_row_to_dict(r, full_pii=full_pii) for r in summary.swap_in],
        "swap_out": [_row_to_dict(r, full_pii=full_pii) for r in summary.swap_out],
        "unknown_after_stage_a_pass": [
            _row_to_dict(r, full_pii=full_pii) for r in summary.unknown_after_stage_a_pass
        ],
        "unknown_after_stage_a_pass_total": summary.unknown_after_stage_a_pass_total,
        "insufficient_data_count": summary.insufficient_data_count,
        "not_computable": summary.not_computable,
        "defaultness_by_known_outcome": summary.defaultness_by_known_outcome,
    }


class SimulateRequest(BaseModel):
    yaml_text: str = Field(alias="yaml")
    sample_size: int | None = None

    model_config = {"populate_by_name": True}


@router.get("/active")
async def get_active(
    _employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    active = await get_active_ruleset_version(session)
    if active is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="нет активной версии конфига")
    config = load_config(active)
    return {
        "version": active.version,
        "comment": active.comment,
        "effective_from": active.effective_from,
        "yaml": yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
    }


@router.post("/simulate")
async def simulate(
    payload: SimulateRequest,
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidate_config = _parse_yaml_or_400(payload.yaml_text)

    active = await get_active_ruleset_version(session)
    if active is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="нет активной версии для сравнения")
    active_config = load_config(active)

    settings = get_settings()
    sample_size = payload.sample_size or settings.ruleset_simulation_default_sample_size

    summary = await run_simulation(
        session, active_config=active_config, candidate_config=candidate_config, sample_size=sample_size
    )

    config_hash = compute_config_hash(candidate_config)
    simulation = RulesetSimulation(
        config_hash=config_hash,
        active_version_id=active.id,
        requested_by=employee.id,
        sample_size=sample_size,
        summary=_summary_to_dict(summary, full_pii=True),  # хранится полностью, редактируется на отдаче
    )
    session.add(simulation)
    await session.commit()

    expires_at = simulation.requested_at + timedelta(hours=settings.ruleset_simulation_ttl_hours)

    return {
        "simulation_id": str(simulation.id),
        "active_version": active.version,
        "expires_at": expires_at,
        "summary": _summary_to_dict(summary, full_pii=has_full_pii(employee)),
    }


class SaveRequest(BaseModel):
    yaml_text: str = Field(alias="yaml")
    simulation_id: UUID

    model_config = {"populate_by_name": True}


@router.post("/save")
async def save(
    payload: SaveRequest,
    request: Request,
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    config = _parse_yaml_or_400(payload.yaml_text)
    config_hash = compute_config_hash(config)

    simulation = await session.get(RulesetSimulation, payload.simulation_id)
    if simulation is None or simulation.requested_by != employee.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="сначала прогоните симуляцию по этому конфигу",
        )
    if simulation.config_hash != config_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="конфиг изменился после симуляции — повторите симуляцию",
        )

    active = await get_active_ruleset_version(session)
    if active is None or active.id != simulation.active_version_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="правила изменились после симуляции (кто-то уже сохранил или обновился файл) — повторите симуляцию",
        )

    settings = get_settings()
    age = datetime.now(timezone.utc) - simulation.requested_at
    if age > timedelta(hours=settings.ruleset_simulation_ttl_hours):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="симуляция устарела — повторите симуляцию",
        )

    row = await save_ruleset_version(session, config, author_id=employee.id, config_hash=config_hash)
    await record_audit(
        session, employee_id=employee.id, action="change_ruleset", object_type="ruleset_version",
        object_id=row.id, request=request,
        details={"op": "save", "version": row.version, "simulation_id": str(simulation.id)},
    )
    await session.commit()

    return {"version": row.version, "id": str(row.id)}


class RollbackRequest(BaseModel):
    version: int
    reason: str = Field(min_length=1)


@router.post("/rollback")
async def rollback(
    payload: RollbackRequest,
    request: Request,
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not payload.reason.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="причина обязательна")

    target = (
        await session.execute(select(RulesetVersion).where(RulesetVersion.version == payload.version))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="версия не найдена")

    # Копия правил версии N, но effective_from — сегодня, не дата из старого
    # YAML: откат должен подействовать сейчас, а не (возможно) остаться вне
    # диапазона "уже вступило в силу" или проиграть сравнение с другой версией
    # по effective_from (app.engine.versions.get_active_ruleset_version сортирует
    # именно по нему). Хеш поэтому тоже пересчитываем — effective_from часть
    # содержимого, которое хешируется (compute_config_hash), совпасть с
    # target.config_hash он больше не может.
    old_config = load_config(target)
    rollback_config = old_config.model_copy(update={"effective_from": datetime.now(timezone.utc).date()})
    new_hash = compute_config_hash(rollback_config)

    row = await save_ruleset_version(session, rollback_config, author_id=employee.id, config_hash=new_hash)
    row.comment = f"Откат к версии {target.version}: {payload.reason.strip()}"
    await record_audit(
        session, employee_id=employee.id, action="change_ruleset", object_type="ruleset_version",
        object_id=row.id, request=request,
        details={"op": "rollback", "from_version": target.version, "new_version": row.version, "reason": payload.reason},
    )
    await session.commit()

    return {"version": row.version, "id": str(row.id)}


@router.post("/accept-file")
async def accept_file(
    request: Request,
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Найденный баг (T14, ваше замечание): версия, принятая из файла, —
    author_id=None (она пришла из файла, не от человека; нажатие — только
    запись в аудит, не делает человека "автором правил"). Раньше ставился
    author_id=employee.id — каждое нажатие создавало версию с человеком-
    автором, которая на следующей проверке сама считалась конфликтующей с
    файлом (sync_ruleset_from_file конфликтует именно тогда, когда у активной
    версии есть автор), поэтому плодились версии при повторных нажатиях.
    Если активная версия уже совпадает с файлом по хешу — no-op, ничего не
    создаём (повторное нажатие ничего не меняет)."""
    settings = get_settings()
    try:
        from app.engine.schema import load_ruleset_from_yaml

        file_config = load_ruleset_from_yaml(settings.ruleset_path)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"файл не читается: {exc}") from exc

    file_hash = compute_config_hash(file_config)
    active = await get_active_ruleset_version(session)
    if active is not None and active.config_hash == file_hash:
        return {"version": active.version, "id": str(active.id), "created": False}

    # effective_from — сегодня, не дата из файла (тот же баг, что уже чинили
    # для отката): активная версия сейчас может иметь более позднюю дату
    # вступления, и принятая "как есть" версия файла проиграет ей по
    # сортировке в get_active_ruleset_version, реально не станет активной.
    # Хеш не меняется — effective_from в него не входит (п.1 выше).
    config_to_save = file_config.model_copy(update={"effective_from": datetime.now(timezone.utc).date()})
    row = await save_ruleset_version(session, config_to_save, author_id=None, config_hash=file_hash)
    await record_audit(
        session, employee_id=employee.id, action="change_ruleset", object_type="ruleset_version",
        object_id=row.id, request=request,
        details={"op": "accept_file", "version": row.version},
    )
    await session.commit()

    return {"version": row.version, "id": str(row.id), "created": True}


@router.get("/versions")
async def list_versions(
    _employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()
    active = await get_active_ruleset_version(session)

    rows = (
        await session.execute(select(RulesetVersion).order_by(RulesetVersion.version.desc()).limit(200))
    ).scalars().all()

    versions = [
        {
            "id": str(r.id),
            "version": r.version,
            "comment": r.comment,
            "author_id": str(r.author_id) if r.author_id else None,
            "effective_from": r.effective_from,
            "created_at": r.created_at,
            "is_active": active is not None and r.id == active.id,
        }
        for r in rows
    ]

    file_conflict: dict[str, Any] = {"conflict": False, "file_valid": True, "error": None}
    if active is not None:
        try:
            from app.engine.schema import load_ruleset_from_yaml

            file_config = load_ruleset_from_yaml(settings.ruleset_path)
            file_hash = compute_config_hash(file_config)
            if file_hash != active.config_hash:
                if active.author_id is not None:
                    file_conflict["conflict"] = True
                # хеш отличается, но у активной версии нет автора — это не
                # конфликт, а обычная досинхронизация при следующем деплое
                # (app.engine.versions.sync_ruleset_from_file), без ручных
                # правок поверх файла — риск-менеджеру тут нечего решать.
        except Exception as exc:
            file_conflict["file_valid"] = False
            file_conflict["error"] = str(exc)

    return {"versions": versions, "file_conflict": file_conflict}
