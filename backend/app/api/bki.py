"""Запрос в БКИ по кнопке (решение владельца): стадия Б после дешёвых
проверок ждёт в awaiting_bki, сотрудник выбирает вид отчёта — «обычный» или
«подробный» (дороже), bki.report_types в правилах. Заказ пишется
(bki_request_orders, audit_log), дальше стадию Б довершает фоновая задача
(app/tasks/stage_b.py::request_bki_report_task) — платный вызов бюро идёт
там, вне запроса сотрудника."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine.versions import get_active_ruleset_version, load_config
from app.models import Application, BkiRequestOrder, Employee, StatusHistory
from app.security.audit import record_audit
from app.security.deps import require_roles

router = APIRouter(prefix="/api", tags=["bki"])

_BKI_REQUEST_ROLES = ("operator", "admin")


class BkiRequestIn(BaseModel):
    report_type: str


class BkiRequestOut(BaseModel):
    status: str
    report_type: str


@router.post(
    "/applications/{application_id}/bki-request",
    response_model=BkiRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_bki_report(
    application_id: UUID,
    payload: BkiRequestIn,
    request: Request,
    employee: Employee = Depends(require_roles(*_BKI_REQUEST_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> BkiRequestOut:
    # FOR UPDATE — две кнопки, нажатые почти одновременно, не дают двух
    # заказов в одном ожидании: второй увидит, что заказ уже есть.
    application = (
        await session.execute(select(Application).where(Application.id == application_id).with_for_update())
    ).scalar_one_or_none()
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")
    if application.status != "awaiting_bki":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="заявка не ждёт запроса в БКИ — отчёт уже заказан или стадия Б ещё не дошла до БКИ",
        )

    ruleset_row = await get_active_ruleset_version(session)
    config = load_config(ruleset_row) if ruleset_row is not None else None
    if config is None or payload.report_type not in config.bki.report_types:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="неизвестный вид отчёта БКИ")

    waiting_order = (
        await session.execute(
            select(BkiRequestOrder.id)
            .where(BkiRequestOrder.application_id == application_id)
            .where(BkiRequestOrder.created_at >= _waiting_since_subquery(application_id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if waiting_order is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="отчёт уже заказан — ждём ответа бюро")

    session.add(BkiRequestOrder(application_id=application_id, report_type=payload.report_type, employee_id=employee.id))
    await record_audit(
        session, employee_id=employee.id, action="request_bki_report",
        object_type="application", object_id=application_id, request=request,
        details={"report_type": payload.report_type},
    )
    await session.commit()

    from app.tasks.stage_b import request_bki_report_task

    request_bki_report_task.delay(str(application_id))
    return BkiRequestOut(status="queued", report_type=payload.report_type)


def _waiting_since_subquery(application_id: UUID):
    return (
        select(StatusHistory.created_at)
        .where(StatusHistory.application_id == application_id, StatusHistory.to_status == "awaiting_bki")
        .order_by(StatusHistory.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
