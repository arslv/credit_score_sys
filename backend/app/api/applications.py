"""T12: заявки — список и карточка, только просмотр (решения принимаются в
системе заказчика, ТЗ «Карточка заявки»). Персданные — только risk_manager и
compliance; admin и viewer получают обезличенный вид (ваше уточнение: admin
для своей работы — сотрудники/справочники/аудит/состояние системы — паспорта
заявителей ему не нужны)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    Application,
    BkiReport,
    Check,
    Client,
    Decision,
    Document,
    Employee,
    GoodsCategory,
    Guarantor,
    ManualOverride,
    StatusHistory,
)
from app.security.audit import record_audit
from app.security.deps import get_current_employee, has_full_pii, require_roles
from app.security.pagination import Page, PageParams
from app.storage.base import Storage

router = APIRouter(prefix="/api/applications", tags=["applications"])

_MASKED_NAME = "Клиент скрыт"
_VIEW_ROLES = ("risk_manager", "admin", "viewer", "compliance")
_PHOTO_ROLES = ("risk_manager", "compliance")
_CREDIT_REPORT_ROLES = ("risk_manager", "compliance")


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


class ApplicationListItem(BaseModel):
    id: UUID
    number: int
    external_id: str
    submitted_at: datetime
    client_name: str | None
    goods_price: Decimal
    goods_name: str
    status: str
    stage_a_outcome: str | None
    stage_b_outcome: str | None
    score: Decimal | None
    has_override: bool
    override_matches_system: bool | None


async def _latest_outcome(session: AsyncSession, application_id: UUID, stage: str) -> Decision | None:
    return (
        await session.execute(
            select(Decision)
            .where(Decision.application_id == application_id, Decision.stage == stage)
            .order_by(Decision.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


@router.get("", response_model=Page[ApplicationListItem])
async def list_applications(
    params: PageParams = Depends(),
    status_filter: str | None = Query(None, alias="status"),
    submitted_from: date | None = None,
    submitted_to: date | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    goods_category_code: str | None = None,
    has_override: bool | None = None,
    ocr_failed_only: bool = False,
    source_unavailable_only: bool = False,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[ApplicationListItem]:
    stmt = select(Application)
    if status_filter:
        stmt = stmt.where(Application.status == status_filter)
    if submitted_from:
        stmt = stmt.where(Application.submitted_at >= submitted_from)
    if submitted_to:
        stmt = stmt.where(Application.submitted_at < submitted_to)
    if amount_min is not None:
        stmt = stmt.where(Application.requested_amount >= amount_min)
    if amount_max is not None:
        stmt = stmt.where(Application.requested_amount <= amount_max)
    if goods_category_code:
        stmt = stmt.where(
            Application.goods_category_id.in_(
                select(GoodsCategory.id).where(GoodsCategory.code == goods_category_code)
            )
        )
    if ocr_failed_only:
        stmt = stmt.where(Application.status == "ocr_failed")
    if has_override is not None:
        override_exists = exists(
            select(ManualOverride.id).where(ManualOverride.application_id == Application.id)
        )
        stmt = stmt.where(override_exists if has_override else ~override_exists)
    if source_unavailable_only:
        stmt = stmt.where(
            exists(
                select(Check.id).where(
                    Check.application_id == Application.id, Check.status == "source_unavailable"
                )
            )
        )

    total = len((await session.execute(stmt)).scalars().all())
    stmt = stmt.order_by(Application.submitted_at.desc()).offset(params.offset).limit(params.page_size)
    applications = (await session.execute(stmt)).scalars().all()

    full_pii = has_full_pii(employee)
    items: list[ApplicationListItem] = []
    for application in applications:
        client_name = None
        if full_pii and application.client_id is not None:
            client = await session.get(Client, application.client_id)
            if client is not None:
                client_name = f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip()
        elif application.client_id is not None:
            client_name = _MASKED_NAME

        decision_a = await _latest_outcome(session, application.id, "A")
        decision_b = await _latest_outcome(session, application.id, "B")
        override = (
            await session.execute(
                select(ManualOverride)
                .where(ManualOverride.application_id == application.id)
                .order_by(ManualOverride.created_at.desc())
                .limit(1)
            )
        ).scalars().first()

        latest_decision = decision_b or decision_a
        override_matches = None
        if override is not None and latest_decision is not None:
            override_matches = override.to_outcome == latest_decision.outcome

        items.append(
            ApplicationListItem(
                id=application.id, number=application.number, external_id=application.external_id,
                submitted_at=application.submitted_at, client_name=client_name,
                goods_price=application.goods_price, goods_name=application.goods_name,
                status=application.status,
                stage_a_outcome=decision_a.outcome if decision_a else None,
                stage_b_outcome=decision_b.outcome if decision_b else None,
                score=(decision_b.score if decision_b else None),
                has_override=override is not None,
                override_matches_system=override_matches,
            )
        )

    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


class DecisionOut(BaseModel):
    id: UUID
    stage: str
    outcome: str
    reason_code: str | None
    freeze: bool
    triggered_rules: list[dict[str, Any]]
    score: Decimal | None
    score_source: str | None
    approved_amount: Decimal | None
    approved_term: int | None
    offered_tariff: str | None
    required_down_pay: Decimal | None
    ruleset_version_id: UUID
    created_at: datetime


class CheckOut(BaseModel):
    id: UUID
    subject_type: str
    subject_id: UUID | None
    source: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_text: str | None


class StatusHistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    actor_type: str
    employee_id: UUID | None
    created_at: datetime


class DocumentOut(BaseModel):
    id: UUID
    kind: str
    mime: str
    size_bytes: int
    uploaded_at: datetime


class ManualOverrideOut(BaseModel):
    id: UUID
    from_outcome: str
    to_outcome: str
    reason_code: str
    comment: str | None
    created_at: datetime


class ApplicationDetail(BaseModel):
    id: UUID
    number: int
    external_id: str
    status: str
    client_name: str | None
    goods_name: str
    goods_price: Decimal
    down_payment: Decimal
    term_months: int
    tariff: str
    requested_amount: Decimal
    submitted_at: datetime
    decisions: list[DecisionOut]
    checks: list[CheckOut]
    status_history: list[StatusHistoryOut]
    documents: list[DocumentOut]
    manual_overrides: list[ManualOverrideOut]
    has_guarantor: bool


@router.get("/{application_id}", response_model=ApplicationDetail)
async def get_application(
    application_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ApplicationDetail:
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")

    full_pii = has_full_pii(employee)
    client_name = None
    if application.client_id is not None:
        if full_pii:
            client = await session.get(Client, application.client_id)
            if client is not None:
                client_name = f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip()
        else:
            client_name = _MASKED_NAME

    decisions = (
        await session.execute(
            select(Decision).where(Decision.application_id == application_id).order_by(Decision.created_at)
        )
    ).scalars().all()
    checks = (
        await session.execute(
            select(Check).where(Check.application_id == application_id).order_by(Check.started_at)
        )
    ).scalars().all()
    history = (
        await session.execute(
            select(StatusHistory)
            .where(StatusHistory.application_id == application_id)
            .order_by(StatusHistory.created_at)
        )
    ).scalars().all()
    documents = (
        await session.execute(
            select(Document).where(Document.application_id == application_id).order_by(Document.uploaded_at)
        )
    ).scalars().all()
    overrides = (
        await session.execute(
            select(ManualOverride)
            .where(ManualOverride.application_id == application_id)
            .order_by(ManualOverride.created_at)
        )
    ).scalars().all()
    guarantor_exists = (
        await session.execute(select(Guarantor.id).where(Guarantor.application_id == application_id).limit(1))
    ).scalar_one_or_none() is not None

    if full_pii:
        await record_audit(
            session, employee_id=employee.id, action="view_application",
            object_type="application", object_id=application_id, request=request,
        )
        await session.commit()

    return ApplicationDetail(
        id=application.id, number=application.number, external_id=application.external_id,
        status=application.status, client_name=client_name, goods_name=application.goods_name,
        goods_price=application.goods_price, down_payment=application.down_payment,
        term_months=application.term_months, tariff=application.tariff,
        requested_amount=application.requested_amount, submitted_at=application.submitted_at,
        decisions=[
            DecisionOut(
                id=d.id, stage=d.stage, outcome=d.outcome, reason_code=d.reason_code, freeze=d.freeze,
                triggered_rules=d.triggered_rules, score=d.score, score_source=d.score_source,
                approved_amount=d.approved_amount, approved_term=d.approved_term,
                offered_tariff=d.offered_tariff, required_down_pay=d.required_down_pay,
                ruleset_version_id=d.ruleset_version_id, created_at=d.created_at,
            )
            for d in decisions
        ],
        checks=[
            CheckOut(
                id=c.id, subject_type=c.subject_type, subject_id=c.subject_id, source=c.source, status=c.status,
                started_at=c.started_at, finished_at=c.finished_at, duration_ms=c.duration_ms,
                error_text=c.error_text,
            )
            for c in checks
        ],
        status_history=[
            StatusHistoryOut(
                from_status=h.from_status, to_status=h.to_status, actor_type=h.actor_type,
                employee_id=h.employee_id, created_at=h.created_at,
            )
            for h in history
        ],
        documents=[
            DocumentOut(id=d.id, kind=d.kind, mime=d.mime, size_bytes=d.size_bytes, uploaded_at=d.uploaded_at)
            for d in documents
        ],
        manual_overrides=[
            ManualOverrideOut(
                id=o.id, from_outcome=o.from_outcome, to_outcome=o.to_outcome, reason_code=o.reason_code,
                comment=o.comment, created_at=o.created_at,
            )
            for o in overrides
        ],
        has_guarantor=guarantor_exists,
    )


class CreditReportOut(BaseModel):
    status: str
    report_raw: dict[str, Any] | None
    features: dict[str, Any] | None
    score: Decimal | None
    requested_at: datetime


@router.get("/{application_id}/credit-report", response_model=CreditReportOut)
async def get_credit_report(
    application_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_CREDIT_REPORT_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> CreditReportOut:
    application = await session.get(Application, application_id)
    if application is None or application.client_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="кредитный отчёт недоступен")

    report = (
        await session.execute(
            select(BkiReport)
            .where(BkiReport.client_id == application.client_id, BkiReport.status == "ok")
            .order_by(BkiReport.requested_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="кредитный отчёт недоступен")

    await record_audit(
        session, employee_id=employee.id, action="view_credit_report",
        object_type="application", object_id=application_id, request=request,
    )
    await session.commit()

    return CreditReportOut(
        status=report.status, report_raw=report.report_raw, features=report.features,
        score=report.score, requested_at=report.requested_at,
    )


@router.get("/{application_id}/documents/{document_id}")
async def view_document(
    application_id: UUID,
    document_id: UUID,
    request: Request,
    response: Response,
    employee: Employee = Depends(require_roles(*_PHOTO_ROLES)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    """Фото — через сессию, не подписанной ссылкой (ваше уточнение): ссылка с
    ключом в адресе остаётся в истории браузера и в логах. Каждый просмотр —
    в audit_log; Cache-Control: no-store — ни браузер, ни промежуточный прокси
    не должны сохранять снимок паспорта на диске."""
    document = await session.get(Document, document_id)
    if document is None or document.application_id != application_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="документ не найден")

    data = await storage.read(document.storage_key)

    await record_audit(
        session, employee_id=employee.id, action="view_document",
        object_type="document", object_id=document_id, request=request,
    )
    await session.commit()

    return Response(content=data, media_type=document.mime, headers={"Cache-Control": "no-store"})
