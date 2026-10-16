"""T16: ФССП — капча решает только человек (ваше жёсткое правило), никакого
автоматического распознавания и обходов защит от ботов. Два шага:

1. POST .../fssp/search — ищем, получаем картинку капчи + code_id, заводим
   fssp_captcha_sessions(status=pending_captcha). Регион — только по явному
   выбору оператора (авто-подсказка через GET /api/fssp/regions по адресу
   регистрации, 05 никогда не подставляется молча, ваше явное указание).
2. POST .../fssp/{session_id}/submit-code — код неверный -> сессия сгорает,
   сразу открываем НОВУЮ (autoретрай капчи, ваше решение по UX); сайт
   недоступен -> source_unavailable, без автоповтора; код принят -> пишем
   checks(source=fssp), классифицируем (app.engine.fssp.summarize_productions)
   и, если по заявке уже есть решение, а новый результат сработал бы на
   hard_stop fssp_credit_debt, — fssp_post_decision_alerts (T16, п.3 ваших
   правок; статус заявки не меняем, правило 12 CLAUDE.md).

Роли (T23): operator и admin — admin получил обязанности risk_manager и
полный доступ к персданным. Исторически (T16): только operator и risk_manager (п.1 — admin по
нашей модели ролей персданные заявителей не видит)."""

import base64
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.engine.fssp import summarize_productions
from app.engine.fssp_regions import load_fssp_regions, match_region_code
from app.engine.pipeline import _subject_from_ocr, unwrap_ocr_fields
from app.engine.rules import evaluate_hard_stops
from app.engine.versions import get_active_ruleset_version, load_config
from app.models import (
    Application,
    Check,
    Decision,
    Employee,
    FsspCaptchaSession,
    FsspPostDecisionAlert,
    Guarantor,
    OcrResult,
)
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.sources.base import Subject
from app.sources.factory import build_fssp_captcha_source
from app.sources.fssp import FsspSearchError, FsspValidationError

router = APIRouter(prefix="/api", tags=["fssp"])

_FSSP_ROLES = ("operator", "admin")
_SUBJECT_TYPES = ("applicant", "guarantor")


async def _resolve_subject(
    session: AsyncSession, application: Application, subject_type: str, subject_id: UUID | None,
) -> Subject:
    if subject_type not in _SUBJECT_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="неизвестный subject_type")

    if subject_type == "applicant":
        ocr_row = (
            await session.execute(
                select(OcrResult)
                .where(OcrResult.application_id == application.id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if ocr_row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="нет данных распознавания для проверки ФССП"
            )
        return _subject_from_ocr(unwrap_ocr_fields(ocr_row.fields), application)

    guarantor = await session.get(Guarantor, subject_id) if subject_id else None
    if guarantor is None or guarantor.application_id != application.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="поручитель не найден")
    payload = guarantor.profile
    return Subject(
        last_name=payload.get("last_name"), first_name=payload.get("first_name"),
        middle_name=payload.get("middle_name"),
        birth_date=date.fromisoformat(payload["birth_date"]) if payload.get("birth_date") else None,
        passport_series=payload.get("passport_series"), passport_number=payload.get("passport_number"),
        passport_issue_date=date.fromisoformat(payload["issue_date"]) if payload.get("issue_date") else None,
        registration_address=payload.get("registration_address"),
        phone=payload.get("phone"),
    )


class FsspRegionOut(BaseModel):
    code: str
    label: str


class FsspRegionsResponse(BaseModel):
    regions: list[FsspRegionOut]
    suggested_region_code: str | None = None


@router.get("/fssp/regions", response_model=FsspRegionsResponse)
async def list_fssp_regions(
    application_id: UUID | None = None,
    subject_type: str = "applicant",
    subject_id: UUID | None = None,
    employee: Employee = Depends(require_roles(*_FSSP_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> FsspRegionsResponse:
    settings = get_settings()
    regions = load_fssp_regions(settings.fssp_regions_path)

    suggested_region_code = None
    if application_id is not None:
        application = await session.get(Application, application_id)
        if application is not None:
            subject = await _resolve_subject(session, application, subject_type, subject_id)
            suggested_region_code = match_region_code(subject.registration_address, regions)

    return FsspRegionsResponse(
        regions=[FsspRegionOut(code=code, label=label) for code, label in regions.items()],
        suggested_region_code=suggested_region_code,
    )


async def _record_fssp_search_failure(
    session: AsyncSession, *, employee_id, application_id: UUID, subject_type: str, subject_id: UUID | None,
    request: Request, exc: Exception,
) -> None:
    """T16, живой прогон (п.4 ваших правок): неудачная попытка оставляет
    след — audit_log всегда (кто, когда, тип ошибки; картинка и код сюда не
    попадают никогда, ваше явное указание), checks(source=fssp,
    status=source_unavailable) — только когда сайт реально ответил (есть
    сырой ответ, правило 3 CLAUDE.md); на отказ валидации (субъект неполный,
    FsspValidationError) внешний сайт не запрашивался вовсе — писать checks
    нечего, это не "источник недоступен"."""
    if isinstance(exc, FsspSearchError):
        now = _now()
        session.add(
            Check(
                application_id=application_id, subject_type=subject_type, subject_id=subject_id,
                source="fssp", status="source_unavailable", response_raw=exc.raw_response,
                error_text=str(exc), started_at=now, finished_at=now, duration_ms=0,
            )
        )
        await session.flush()

    await record_audit(
        session, employee_id=employee_id, action="fssp_search_started",
        object_type="application", object_id=application_id, request=request,
        details={
            "subject_type": subject_type, "subject_id": str(subject_id) if subject_id else None,
            "result": "validation_error" if isinstance(exc, FsspValidationError) else "source_unavailable",
            "error": str(exc),
        },
    )


class FsspSearchRequest(BaseModel):
    subject_type: str
    subject_id: UUID | None = None
    region_code: str
    reason: str | None = None


class FsspSearchResponse(BaseModel):
    session_id: UUID
    captcha_png_base64: str


@router.post("/applications/{application_id}/fssp/search", response_model=FsspSearchResponse)
async def fssp_search(
    application_id: UUID,
    body: FsspSearchRequest,
    request: Request,
    response: Response,
    employee: Employee = Depends(require_roles(*_FSSP_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> FsspSearchResponse:
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")

    settings = get_settings()
    regions = load_fssp_regions(settings.fssp_regions_path)
    if body.region_code not in regions:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="неизвестный код региона")

    subject = await _resolve_subject(session, application, body.subject_type, body.subject_id)

    # Одна проверка на заявку (и субъекта) с обязательной причиной при
    # повторе — ваше явное указание в постановке задачи. Проверяем по
    # наличию УЖЕ ЗАВЕРШЁННОЙ строки checks(source=fssp), не по сессиям —
    # сессии технические (см. докстринг модели), решённый факт только в checks.
    existing_resolved = (
        await session.execute(
            select(Check.id)
            .where(
                Check.application_id == application_id, Check.subject_type == body.subject_type,
                Check.subject_id == body.subject_id, Check.source == "fssp",
            )
            .limit(1)
        )
    ).first()
    if existing_resolved is not None and not (body.reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="повторная проверка ФССП по этой заявке требует причины"
        )

    source = build_fssp_captcha_source(settings)
    try:
        challenge = await source.search(subject, region_code=body.region_code)
    except FsspValidationError as exc:
        await _record_fssp_search_failure(
            session, employee_id=employee.id, application_id=application_id,
            subject_type=body.subject_type, subject_id=body.subject_id, request=request, exc=exc,
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except FsspSearchError as exc:
        await _record_fssp_search_failure(
            session, employee_id=employee.id, application_id=application_id,
            subject_type=body.subject_type, subject_id=body.subject_id, request=request, exc=exc,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"сайт ФССП недоступен: {exc}"
        ) from exc

    # Поиск удался -> ТЕПЕРЬ предыдущая активная сессия по этому субъекту
    # гасится (T16, п.5 ваших правок); старый code_id submit-code больше не
    # примет. Делаем это после успеха, не до — неудачная попытка (валидация/
    # сайт недоступен) не должна сжигать ещё рабочую старую сессию оператора.
    await session.execute(
        update(FsspCaptchaSession)
        .where(
            FsspCaptchaSession.application_id == application_id,
            FsspCaptchaSession.subject_type == body.subject_type,
            FsspCaptchaSession.subject_id == body.subject_id,
            FsspCaptchaSession.status == "pending_captcha",
        )
        .values(status="expired")
    )

    captcha_session = FsspCaptchaSession(
        application_id=application_id, subject_type=body.subject_type, subject_id=body.subject_id,
        employee_id=employee.id, region_code=body.region_code, code_id=challenge.code_id,
    )
    session.add(captcha_session)
    await session.flush()

    # Картинку и код никогда не пишем ни в логи, ни в аудит (ваше явное
    # указание) — в audit_log только факт попытки: кто, когда, субъект, регион.
    await record_audit(
        session, employee_id=employee.id, action="fssp_search_started",
        object_type="application", object_id=application_id, request=request,
        details={
            "subject_type": body.subject_type,
            "subject_id": str(body.subject_id) if body.subject_id else None,
            "region_code": body.region_code,
            "reason": body.reason,
        },
    )
    await session.commit()

    response.headers["Cache-Control"] = "no-store"
    return FsspSearchResponse(
        session_id=captcha_session.id,
        captcha_png_base64=base64.b64encode(challenge.captcha_png).decode("ascii"),
    )


class FsspSubmitCodeRequest(BaseModel):
    code: str


class FsspSubmitCodeResponse(BaseModel):
    outcome: str  # ok | wrong_code | source_unavailable
    check: dict | None = None
    new_session_id: UUID | None = None
    new_captcha_png_base64: str | None = None
    message: str | None = None


async def _post_decision_alert_if_needed(
    session: AsyncSession, *, application_id: UUID, subject_type: str, subject_id: UUID | None, check: Check,
) -> None:
    """T16, п.3 ваших правок: решение уже принято, новый результат ФССП
    сработал бы на hard_stop fssp_credit_debt — статус не трогаем (правило 12
    CLAUDE.md), только фиксируем факт для карточки/счётчика состояния системы."""
    decision = (
        await session.execute(
            select(Decision)
            .where(Decision.application_id == application_id)
            .order_by(Decision.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if decision is None:
        return

    ruleset_row = await get_active_ruleset_version(session)
    if ruleset_row is None:
        return
    config = load_config(ruleset_row)

    features = summarize_productions((check.parsed or {}).get("productions", []), config.sources.fssp.classification)
    outcome = evaluate_hard_stops(config, {"features": features})
    if not any(r.id == "fssp_credit_debt" for r in outcome.triggered_rules):
        return

    session.add(
        FsspPostDecisionAlert(
            application_id=application_id, subject_type=subject_type, subject_id=subject_id,
            check_id=check.id, decision_id=decision.id,
        )
    )


@router.post(
    "/applications/{application_id}/fssp/{session_id}/submit-code", response_model=FsspSubmitCodeResponse,
)
async def fssp_submit_code(
    application_id: UUID,
    session_id: UUID,
    body: FsspSubmitCodeRequest,
    request: Request,
    response: Response,
    employee: Employee = Depends(require_roles(*_FSSP_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> FsspSubmitCodeResponse:
    response.headers["Cache-Control"] = "no-store"

    captcha_session = await session.get(FsspCaptchaSession, session_id)
    if captcha_session is None or captcha_session.application_id != application_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="сессия ФССП не найдена")
    if captcha_session.status != "pending_captcha":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="эта сессия уже закрыта — запросите новую капчу"
        )

    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")
    subject = await _resolve_subject(session, application, captcha_session.subject_type, captcha_session.subject_id)

    captcha_session.attempt_count += 1
    settings = get_settings()
    source = build_fssp_captcha_source(settings)
    code = body.code.strip()
    submit_outcome = await source.submit_code(
        code_id=captcha_session.code_id, code=code, subject=subject, region_code=captcha_session.region_code,
    )

    if submit_outcome.kind == "wrong_code":
        captcha_session.status = "wrong_code"
        await record_audit(
            session, employee_id=employee.id, action="fssp_code_submitted",
            object_type="application", object_id=application_id, request=request,
            details={
                "subject_type": captcha_session.subject_type,
                "subject_id": str(captcha_session.subject_id) if captcha_session.subject_id else None,
                "result": "wrong_code",
            },
        )

        # Автоматически открываем новую капчу (ваше решение по UX) — тот же
        # subject/region, новый code_id, старый уже не принимается.
        try:
            challenge = await source.search(subject, region_code=captcha_session.region_code)
        except FsspValidationError as exc:
            await _record_fssp_search_failure(
                session, employee_id=employee.id, application_id=application_id,
                subject_type=captcha_session.subject_type, subject_id=captcha_session.subject_id,
                request=request, exc=exc,
            )
            await session.commit()
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except FsspSearchError as exc:
            await _record_fssp_search_failure(
                session, employee_id=employee.id, application_id=application_id,
                subject_type=captcha_session.subject_type, subject_id=captcha_session.subject_id,
                request=request, exc=exc,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"сайт ФССП недоступен: {exc}"
            ) from exc

        new_session = FsspCaptchaSession(
            application_id=application_id, subject_type=captcha_session.subject_type,
            subject_id=captcha_session.subject_id, employee_id=employee.id,
            region_code=captcha_session.region_code, code_id=challenge.code_id,
        )
        session.add(new_session)
        await session.flush()
        await session.commit()

        return FsspSubmitCodeResponse(
            outcome="wrong_code", new_session_id=new_session.id,
            new_captcha_png_base64=base64.b64encode(challenge.captcha_png).decode("ascii"),
            message="Код неверный, введите новый",
        )

    if submit_outcome.kind == "source_unavailable":
        captcha_session.status = "expired"
        result = submit_outcome.check_result
        check = Check(
            application_id=application_id, subject_type=captcha_session.subject_type,
            subject_id=captcha_session.subject_id, source="fssp", status=result.status.value,
            request=result.request, response_raw=result.response_raw, parsed=result.parsed,
            started_at=_now(), finished_at=_now(), duration_ms=0,
            error_text=result.error_text,
        )
        session.add(check)
        await session.flush()
        await record_audit(
            session, employee_id=employee.id, action="fssp_code_submitted",
            object_type="application", object_id=application_id, request=request,
            details={
                "subject_type": captcha_session.subject_type,
                "subject_id": str(captcha_session.subject_id) if captcha_session.subject_id else None,
                "result": "source_unavailable",
            },
        )
        await session.commit()
        return FsspSubmitCodeResponse(outcome="source_unavailable", message="сайт ФССП недоступен, попробуйте позже")

    # kind == "ok"
    captcha_session.status = "resolved"
    result = submit_outcome.check_result

    ruleset_row = await get_active_ruleset_version(session)
    config = load_config(ruleset_row) if ruleset_row is not None else None
    productions = (result.parsed or {}).get("productions", [])
    classification = (
        summarize_productions(productions, config.sources.fssp.classification) if config is not None else {}
    )
    parsed = {**(result.parsed or {}), **classification}

    now = _now()
    check = Check(
        application_id=application_id, subject_type=captcha_session.subject_type,
        subject_id=captcha_session.subject_id, source="fssp", status=result.status.value,
        request=result.request, response_raw=result.response_raw, parsed=parsed,
        started_at=now, finished_at=now, duration_ms=0, error_text=result.error_text,
    )
    session.add(check)
    await session.flush()

    await _post_decision_alert_if_needed(
        session, application_id=application_id, subject_type=captcha_session.subject_type,
        subject_id=captcha_session.subject_id, check=check,
    )

    await record_audit(
        session, employee_id=employee.id, action="fssp_code_submitted",
        object_type="application", object_id=application_id, request=request,
        details={
            "subject_type": captcha_session.subject_type,
            "subject_id": str(captcha_session.subject_id) if captcha_session.subject_id else None,
            "result": result.status.value,
        },
    )
    await session.commit()

    return FsspSubmitCodeResponse(
        outcome="ok",
        check={
            "id": str(check.id), "status": check.status, "parsed": check.parsed,
        },
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
