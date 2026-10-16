"""T15: импорт договоров/платежей/исторических заявок (ТЗ «Договоры и платежи»).
Роли — admin (T23; раньше admin и risk_manager). Каждая загрузка и каждое
подтверждение привязки — в audit_log (ваше явное указание)."""

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.engine.imports import (
    ImportFileInvalid,
    import_contracts,
    import_historical_applications,
    import_payment_schedule,
    import_payments,
)
from app.models import Contract, ContractLinkCandidate, Employee
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.storage.base import Storage

router = APIRouter(prefix="/api/imports", tags=["imports"])

_ROLES = ("admin",)


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def _result_to_dict(result) -> dict[str, Any]:
    data = asdict(result)
    data["batch_id"] = str(result.batch_id)
    return data


@router.post("/contracts")
async def upload_contracts(
    request: Request,
    file: UploadFile = File(...),
    # Необязательная (ваше явное указание): нужна только если оператор хочет,
    # чтобы предупреждение о полноте выгрузки платежей той же датой сработало
    # (app/engine/imports.py::_missing_active_contracts) — разовая
    # первоначальная загрузка без даты по-прежнему работает как раньше.
    data_as_of: date | None = Form(None),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    raw = await file.read()
    try:
        result = await import_contracts(
            session, raw=raw, filename=file.filename or "contracts.csv", uploaded_by=employee.id,
            storage=storage, mapping_path=get_settings().import_mapping_contracts_path, data_as_of=data_as_of,
        )
    except ImportFileInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session, employee_id=employee.id, action="import_data", object_type="import_batch",
        object_id=result.batch_id, request=request,
        details={
            "kind": "contracts", "filename": file.filename, "created": result.created, "row_count": result.row_count,
            "data_as_of": data_as_of.isoformat() if data_as_of else None,
        },
    )
    await session.commit()
    return _result_to_dict(result)


@router.post("/payments")
async def upload_payments(
    request: Request,
    file: UploadFile = File(...),
    data_as_of: date = Form(...),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    raw = await file.read()
    try:
        result = await import_payments(
            session, raw=raw, filename=file.filename or "payments.csv", uploaded_by=employee.id,
            storage=storage, mapping_path=get_settings().import_mapping_payments_path, data_as_of=data_as_of,
            contracts_mapping_path=get_settings().import_mapping_contracts_path,
        )
    except ImportFileInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session, employee_id=employee.id, action="import_data", object_type="import_batch",
        object_id=result.batch_id, request=request,
        details={
            "kind": "payments", "filename": file.filename, "created": result.created,
            "row_count": result.row_count, "data_as_of": data_as_of.isoformat(),
        },
    )
    await session.commit()
    return _result_to_dict(result)


@router.post("/payment-schedule")
async def upload_payment_schedule(
    request: Request,
    file: UploadFile = File(...),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    raw = await file.read()
    try:
        result = await import_payment_schedule(
            session, raw=raw, filename=file.filename or "payment_schedule.csv", uploaded_by=employee.id,
            storage=storage, mapping_path=get_settings().import_mapping_payment_schedule_path,
        )
    except ImportFileInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session, employee_id=employee.id, action="import_data", object_type="import_batch",
        object_id=result.batch_id, request=request,
        details={"kind": "payment_schedule", "filename": file.filename, "created": result.created, "row_count": result.row_count},
    )
    await session.commit()
    return _result_to_dict(result)


@router.post("/historical-applications")
async def upload_historical_applications(
    request: Request,
    file: UploadFile = File(...),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    raw = await file.read()
    try:
        result = await import_historical_applications(
            session, raw=raw, filename=file.filename or "historical.csv", uploaded_by=employee.id,
            storage=storage, mapping_path=get_settings().import_mapping_historical_applications_path,
        )
    except ImportFileInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session, employee_id=employee.id, action="import_data", object_type="import_batch",
        object_id=result.batch_id, request=request,
        details={"kind": "historical_applications", "filename": file.filename, "created": result.created, "row_count": result.row_count},
    )
    await session.commit()
    return _result_to_dict(result)


@router.get("/contract-candidates")
async def list_contract_candidates(
    _employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        await session.execute(
            select(ContractLinkCandidate, Contract)
            .join(Contract, Contract.id == ContractLinkCandidate.contract_id)
            .where(ContractLinkCandidate.status == "pending")
            .order_by(ContractLinkCandidate.created_at)
        )
    ).all()
    return {
        "candidates": [
            {
                "id": str(c.id),
                "contract_id": str(c.contract_id),
                "contract_external_number": contract.external_number,
                "application_id": str(c.application_id),
                "match_reason": c.match_reason,
                "source_name": c.source_name,
                "source_phone": c.source_phone,
                "created_at": c.created_at,
            }
            for c, contract in rows
        ]
    }


@router.post("/contract-candidates/{candidate_id}/confirm")
async def confirm_contract_candidate(
    candidate_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidate = await session.get(ContractLinkCandidate, candidate_id)
    if candidate is None or candidate.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="кандидат не найден или уже обработан")

    contract = await session.get(Contract, candidate.contract_id)
    contract.application_id = candidate.application_id
    candidate.status = "confirmed"
    candidate.resolved_by = employee.id
    candidate.resolved_at = datetime.now(timezone.utc)

    await record_audit(
        session, employee_id=employee.id, action="link_contract", object_type="contract",
        object_id=contract.id, request=request,
        details={"application_id": str(candidate.application_id), "candidate_id": str(candidate.id), "match_reason": candidate.match_reason},
    )
    await session.commit()
    return {"contract_id": str(contract.id), "application_id": str(contract.application_id)}


@router.post("/contract-candidates/{candidate_id}/reject")
async def reject_contract_candidate(
    candidate_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidate = await session.get(ContractLinkCandidate, candidate_id)
    if candidate is None or candidate.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="кандидат не найден или уже обработан")

    candidate.status = "rejected"
    candidate.resolved_by = employee.id
    candidate.resolved_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": str(candidate.id), "status": "rejected"}
