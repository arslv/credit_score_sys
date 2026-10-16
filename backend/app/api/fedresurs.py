"""T23: банкротство (Федресурс) — ручная проверка рядом с ФССП.

fedresurs.ru и bankrot.fedresurs.ru закрыты защитой от ботов (Qrator: на
запрос не из браузера — 401/451 и JS-проверка). Обходить защиту не будем;
автоматическая проверка возможна только через официальный (платный) API
Федресурса по договору заказчика. Поэтому сотрудник ищет человека на сайте
сам и отмечает результат здесь.

Результат пишется как обычная проверка источника — append-only строка
checks(source='fedresurs', status=ok|hit), ответ сотрудника сохраняется в
response_raw до разбора (правило 3 CLAUDE.md), факт — в audit_log. Последняя
строка по субъекту — действующий результат; повторная проверка добавляет
новую, старые не трогаем. На решение по заявке не влияет — по ТЗ Федресурс
даёт «флаг оператору» (правил на него в конфиге нет)."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Application, Check, Employee, Guarantor
from app.security.audit import record_audit
from app.security.deps import require_roles

router = APIRouter(prefix="/api", tags=["fedresurs"])

_FEDRESURS_ROLES = ("operator", "admin")  # те же, что у ФССП


class FedresursManualCheckRequest(BaseModel):
    subject_type: Literal["applicant", "guarantor"] = "applicant"
    subject_id: UUID | None = None
    result: Literal["not_found", "found"]
    # Что именно найдено (номер дела, стадия) — обязательно при found.
    comment: str | None = Field(default=None, max_length=2000)


class FedresursManualCheckResponse(BaseModel):
    check_id: UUID
    status: str


@router.post("/applications/{application_id}/fedresurs/manual", response_model=FedresursManualCheckResponse)
async def record_fedresurs_manual_check(
    application_id: UUID,
    payload: FedresursManualCheckRequest,
    request: Request,
    employee: Employee = Depends(require_roles(*_FEDRESURS_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> FedresursManualCheckResponse:
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")

    subject_id: UUID | None = None
    if payload.subject_type == "guarantor":
        guarantor = await session.get(Guarantor, payload.subject_id) if payload.subject_id else None
        if guarantor is None or guarantor.application_id != application.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="поручитель не найден")
        subject_id = guarantor.id

    comment = (payload.comment or "").strip() or None
    if payload.result == "found" and comment is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="опишите, что найдено: номер дела или стадию банкротства",
        )

    now = datetime.now(timezone.utc)
    answer = {"manual": True, "result": payload.result, "comment": comment, "employee_id": str(employee.id)}
    check = Check(
        application_id=application.id, subject_type=payload.subject_type, subject_id=subject_id,
        source="fedresurs", status="hit" if payload.result == "found" else "ok",
        request={"manual": True, "site": "bankrot.fedresurs.ru"},
        response_raw=answer, parsed=answer,
        started_at=now, finished_at=now, duration_ms=0,
    )
    session.add(check)
    await session.flush()
    await record_audit(
        session, employee_id=employee.id, action="fedresurs_manual_check",
        object_type="application", object_id=application.id, request=request,
        details={"subject_type": payload.subject_type, "result": payload.result, "check_id": str(check.id)},
    )
    await session.commit()
    return FedresursManualCheckResponse(check_id=check.id, status=check.status)
