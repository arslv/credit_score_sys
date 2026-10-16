"""T12: комплаенс — заявки с совпадением по перечням, отметка ложного
совпадения, снятие заморозки (только эта роль, ваши уточнения)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine.freeze import find_active_sanctions_freeze
from app.models import Application, Check, Client, ComplianceReview, Decision, Employee, FreezeLift
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.security.pagination import Page, PageParams

router = APIRouter(prefix="/api/compliance", tags=["compliance"])

# T23: разбор совпадений с перечнем — admin и operator (роли compliance больше нет).
_COMPLIANCE_ONLY = ("admin", "operator")


class MatchItem(BaseModel):
    decision_id: UUID
    application_id: UUID
    external_id: str
    client_id: UUID | None
    client_name: str | None
    reason_code: str | None
    decided_at: datetime
    freeze_active: bool
    latest_review_verdict: str | None


@router.get("/matches", response_model=Page[MatchItem])
async def list_matches(
    params: PageParams = Depends(),
    employee: Employee = Depends(require_roles(*_COMPLIANCE_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> Page[MatchItem]:
    stmt = select(Decision, Application).join(Application, Application.id == Decision.application_id).where(
        Decision.freeze.is_(True)
    )
    total = len((await session.execute(stmt)).all())
    rows = (
        await session.execute(stmt.order_by(Decision.created_at.desc()).offset(params.offset).limit(params.page_size))
    ).all()

    items: list[MatchItem] = []
    for decision, application in rows:
        client_name = None
        freeze_active = False
        if application.client_id is not None:
            client = await session.get(Client, application.client_id)
            if client is not None:
                client_name = f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip()
            freeze_active = (
                await find_active_sanctions_freeze(session, client_id=application.client_id)
            ) is not None

        latest_review = (
            await session.execute(
                select(ComplianceReview.verdict)
                .where(ComplianceReview.decision_id == decision.id)
                .order_by(ComplianceReview.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        items.append(
            MatchItem(
                decision_id=decision.id, application_id=application.id, external_id=application.external_id,
                client_id=application.client_id, client_name=client_name, reason_code=decision.reason_code,
                decided_at=decision.created_at, freeze_active=freeze_active, latest_review_verdict=latest_review,
            )
        )

    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


async def _resolve_sanctions_entry_key(session: AsyncSession, application_id: UUID) -> str | None:
    check = (
        await session.execute(
            select(Check)
            .where(
                Check.application_id == application_id, Check.source == "rosfinmon",
                Check.subject_type == "applicant", Check.status == "hit",
            )
            .order_by(Check.started_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if check is None or not check.parsed:
        return None
    return check.parsed.get("sanctions_entry_key") or None


class MarkFalsePositiveRequest(BaseModel):
    verdict: str = Field(pattern="^(false_positive|confirmed)$")
    comment: str | None = None


@router.post("/decisions/{decision_id}/mark-false-positive")
async def mark_false_positive(
    decision_id: UUID, payload: MarkFalsePositiveRequest, request: Request,
    employee: Employee = Depends(require_roles(*_COMPLIANCE_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    decision = await session.get(Decision, decision_id)
    if decision is None or not decision.freeze:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="совпадение не найдено")
    application = await session.get(Application, decision.application_id)
    if application is None or application.client_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="у заявки нет привязанного клиента")

    sanctions_entry_key = await _resolve_sanctions_entry_key(session, application.id)
    if sanctions_entry_key is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="не найдена запись перечня для этого совпадения")

    review = ComplianceReview(
        client_id=application.client_id, sanctions_entry_key=sanctions_entry_key, decision_id=decision.id,
        employee_id=employee.id, verdict=payload.verdict, comment=payload.comment,
    )
    session.add(review)
    await session.flush()
    await record_audit(
        session, employee_id=employee.id, action="mark_false_positive",
        object_type="decision", object_id=decision.id, request=request,
        details={"verdict": payload.verdict},
    )
    await session.commit()
    return {"ok": True, "id": str(review.id)}


class LiftFreezeRequest(BaseModel):
    decision_id: UUID
    reason: str = Field(min_length=1)


@router.post("/clients/{client_id}/lift-freeze")
async def lift_freeze(
    client_id: UUID, payload: LiftFreezeRequest, request: Request,
    employee: Employee = Depends(require_roles(*_COMPLIANCE_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not payload.reason.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="причина обязательна")

    decision = await session.get(Decision, payload.decision_id)
    if decision is None or not decision.freeze:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="решение с заморозкой не найдено")

    current_freeze_reason = await find_active_sanctions_freeze(session, client_id=client_id)
    if current_freeze_reason is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="активной заморозки нет")

    lift = FreezeLift(
        client_id=client_id, decision_id=decision.id, employee_id=employee.id, reason=payload.reason,
    )
    session.add(lift)
    await session.flush()
    await record_audit(
        session, employee_id=employee.id, action="lift_freeze",
        object_type="client", object_id=client_id, request=request, details={"reason": payload.reason},
    )
    await session.commit()
    return {"ok": True, "id": str(lift.id)}
