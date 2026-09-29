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

from app.config import get_settings
from app.db import get_session
from app.engine.pipeline import _subject_from_ocr
from app.integrations.status_mapping import _render_rule_text, get_all_reason_notes
from app.models import (
    Application,
    BkiReport,
    Check,
    Client,
    Decision,
    Document,
    Employee,
    ExternalSnapshot,
    GoodsCategory,
    Guarantor,
    ManualOverride,
    OcrResult,
    StatusHistory,
)
from app.security.audit import record_audit
from app.security.deps import get_current_employee, has_full_pii, require_roles
from app.security.pagination import Page, PageParams
from app.sources.base import Subject
from app.sources.factory import build_fedresurs_source, build_fns_source
from app.sources.runner import run_and_record_check
from app.storage.base import Storage

router = APIRouter(prefix="/api/applications", tags=["applications"])

_MASKED_NAME = "Клиент скрыт"
_VIEW_ROLES = ("risk_manager", "admin", "viewer", "compliance")
_PHOTO_ROLES = ("risk_manager", "compliance")
_CREDIT_REPORT_ROLES = ("risk_manager", "compliance")
_RETRY_ROLES = ("risk_manager",)
# Только эти два источника имеют смысл повторять руками (ваше уточнение):
# ФССП уже опрашивается автоматически до дедлайна (app/tasks/fssp_poll.py) —
# ручной повтор дал бы новый параллельный запрос, а не переиспользовал бы
# уже поданный; МВД/Росфинмониторинг/чёрный список — локальные (БД), их
# "недоступность" — не то, что чинится нажатием кнопки; БКИ — платный запрос,
# повтор только через «Рассмотреть» оператором (ваше явное указание).
_RETRYABLE_SOURCES = ("fns", "fedresurs")


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
    # «Расхождения с оператором» — главный инструмент риск-менеджера (ТЗ,
    # «Список заявок»): не просто «есть override» (has_override), а override,
    # который НЕ СОВПАЛ с решением системы. Сравнение по override_matches_system
    # уже не выражается плоским условием в SQL (нужен самый свежий override И
    # самое свежее решение по заявке) — материализуем кандидатов и сравниваем
    # в Python (см. цикл ниже), как уже делает total= для остальных фильтров.
    discrepancies_only: bool = False,
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
    if discrepancies_only:
        has_override = True
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
    stmt = stmt.order_by(Application.submitted_at.desc())

    full_pii = has_full_pii(employee)

    async def _to_item(application: Application) -> tuple[ApplicationListItem, bool | None]:
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

        item = ApplicationListItem(
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
        return item, override_matches

    if discrepancies_only:
        candidates = (await session.execute(stmt)).scalars().all()
        matched: list[ApplicationListItem] = []
        for application in candidates:
            item, override_matches = await _to_item(application)
            if override_matches is False:
                matched.append(item)
        total = len(matched)
        items = matched[params.offset : params.offset + params.page_size]
    else:
        total = len((await session.execute(stmt)).scalars().all())
        page_stmt = stmt.offset(params.offset).limit(params.page_size)
        applications = (await session.execute(page_stmt)).scalars().all()
        items = [(await _to_item(application))[0] for application in applications]

    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


class DecisionOut(BaseModel):
    id: UUID
    stage: str
    outcome: str
    reason_code: str | None
    freeze: bool
    triggered_rules: list[dict[str, Any]]
    # Человеческим языком, с фактическими значениями и порогом — ТЗ, «Карточка
    # заявки»: «ПДН 58% при пороге 55%», не служебный код. Один на правило —
    # переиспользует те же тексты reason_notes, что и комментарий оператору
    # (T11, app.integrations.status_mapping).
    triggered_rules_text: list[str]
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
    # Сырой ответ и разбор — только risk_manager/compliance (как фото и
    # кредитный отчёт): могут содержать ПД (паспортные поля МВД, ФИО из
    # перечня и т.п.), admin/viewer получают null (см. has_full_pii ниже).
    parsed: dict[str, Any] | None
    response_raw: dict[str, Any] | None
    can_retry: bool


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


class OcrResultOut(BaseModel):
    provider: str
    fields: dict[str, Any]
    min_confidence: Decimal | None
    created_at: datetime


class ExternalSnapshotOut(BaseModel):
    id: UUID
    stage: str
    payload_raw: dict[str, Any]
    received_at: datetime


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
    # Анкета/распознавание/снимки — ПД, только risk_manager/compliance (как выше).
    # profile — неизменяемый снимок на момент подачи (см. Application.profile).
    # external_snapshots — ВСЕ снимки анкеты по стадиям (T04/T10:
    # app/integrations/adapter.py пишет новую строку в external_snapshots при
    # КАЖДОМ изменении содержимого — идемпотентно по content_hash), в порядке
    # получения; подсветку полей, изменённых оператором между запусками стадии
    # Б, строит фронт, сравнивая соседние снимки с stage="B".
    profile: dict[str, Any] | None
    ocr_result: OcrResultOut | None
    external_snapshots: list[ExternalSnapshotOut]


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

    notes = get_all_reason_notes()
    ocr_result = None
    profile = None
    external_snapshots: list[ExternalSnapshotOut] = []
    if full_pii:
        profile = application.profile
        ocr_row = (
            await session.execute(
                select(OcrResult)
                .where(OcrResult.application_id == application_id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if ocr_row is not None:
            ocr_result = OcrResultOut(
                provider=ocr_row.provider, fields=ocr_row.fields,
                min_confidence=ocr_row.min_confidence, created_at=ocr_row.created_at,
            )
        snapshot_rows = (
            await session.execute(
                select(ExternalSnapshot)
                .where(ExternalSnapshot.application_id == application_id)
                .order_by(ExternalSnapshot.received_at)
            )
        ).scalars().all()
        external_snapshots = [
            ExternalSnapshotOut(id=s.id, stage=s.stage, payload_raw=s.payload_raw, received_at=s.received_at)
            for s in snapshot_rows
        ]

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
                triggered_rules=d.triggered_rules,
                triggered_rules_text=[_render_rule_text(r, notes) for r in d.triggered_rules],
                score=d.score, score_source=d.score_source,
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
                parsed=c.parsed if full_pii else None,
                response_raw=c.response_raw if full_pii else None,
                can_retry=(c.source in _RETRYABLE_SOURCES and c.status == "source_unavailable"),
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
        profile=profile,
        ocr_result=ocr_result,
        external_snapshots=external_snapshots,
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


class RetryCheckResult(BaseModel):
    check: CheckOut


@router.post("/{application_id}/checks/{check_id}/retry", response_model=RetryCheckResult)
async def retry_check(
    application_id: UUID,
    check_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_RETRY_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> RetryCheckResult:
    """Повтор проверки для недоступного источника (ТЗ, «Состояния карточки»:
    «Источник недоступен — плашка с кнопкой повтора»). Только risk_manager;
    только ФНС/Федресурс (_RETRYABLE_SOURCES) и только пока последний результат
    по этому источнику — source_unavailable (иначе незачем повторно дёргать
    рабочий источник или платный БКИ)."""
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")

    check = await session.get(Check, check_id)
    if check is None or check.application_id != application_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="проверка не найдена")
    if check.source not in _RETRYABLE_SOURCES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="повтор недоступен для этого источника")
    if check.status != "source_unavailable":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="повтор возможен только для недоступного источника"
        )
    # Check — append-only (правило 1 CLAUDE.md): у ИСХОДНОЙ строки status
    # никогда не поменяется, даже после успешного повтора (он пишет НОВУЮ
    # строку). Поэтому "уже повторяли" проверяем не по check.status, а по
    # тому, остаётся ли эта строка САМОЙ СВЕЖЕЙ для источника — иначе один и
    # тот же устаревший check_id можно было бы "повторять" бесконечно.
    latest_id_for_source = (
        await session.execute(
            select(Check.id)
            .where(
                Check.application_id == application_id, Check.subject_type == check.subject_type,
                Check.subject_id == check.subject_id, Check.source == check.source,
            )
            .order_by(Check.started_at.desc())
            .limit(1)
        )
    ).scalar_one()
    if latest_id_for_source != check_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="по этому источнику уже есть более новый результат — повторите по нему",
        )

    if check.subject_type == "applicant":
        ocr_row = (
            await session.execute(
                select(OcrResult)
                .where(OcrResult.application_id == application_id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if ocr_row is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="нет данных распознавания для повтора")
        subject = _subject_from_ocr(ocr_row.fields, application)
    else:
        guarantor = await session.get(Guarantor, check.subject_id) if check.subject_id else None
        if guarantor is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="поручитель не найден")
        payload = guarantor.profile
        subject = Subject(
            last_name=payload.get("last_name"), first_name=payload.get("first_name"),
            middle_name=payload.get("middle_name"),
            birth_date=date.fromisoformat(payload["birth_date"]) if payload.get("birth_date") else None,
            passport_series=payload.get("passport_series"), passport_number=payload.get("passport_number"),
            phone=payload.get("phone"),
        )

    settings = get_settings()
    source = build_fns_source(settings) if check.source == "fns" else build_fedresurs_source(settings)
    new_check = await run_and_record_check(
        session, source, application_id=application_id, subject_type=check.subject_type,
        subject_id=check.subject_id, subject=subject,
    )

    await record_audit(
        session, employee_id=employee.id, action="retry_check",
        object_type="check", object_id=check_id, request=request,
        details={"source": check.source, "new_check_id": str(new_check.id)},
    )
    await session.commit()

    return RetryCheckResult(
        check=CheckOut(
            id=new_check.id, subject_type=new_check.subject_type, subject_id=new_check.subject_id,
            source=new_check.source, status=new_check.status, started_at=new_check.started_at,
            finished_at=new_check.finished_at, duration_ms=new_check.duration_ms,
            error_text=new_check.error_text, parsed=new_check.parsed, response_raw=new_check.response_raw,
            can_retry=(new_check.source in _RETRYABLE_SOURCES and new_check.status == "source_unavailable"),
        )
    )
