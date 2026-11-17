"""Заявка, заведённая вручную в админке (решение владельца 09.11.2026): для
случаев, когда клиента нет в CRM. Сотрудник (admin или operator) вводит
анкету и паспорт, прикладывает фото — дальше тот же путь, что у заявки из
CRM с паспортными данными в анкете: стадия А по введённым данным, затем
сразу стадия Б и ожидание запроса в БКИ (app/tasks/stage_a.py::
process_crm_data_task).

Отличие одно: заявки нет в CRM (source = 'manual') — решения туда не
отправляются (app/integrations/adapter.py), видны только в админке."""

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.partner_webhooks import _PHOTO_SIGNATURES, _check_image_bytes, _photo_too_large
from app.config import get_settings
from app.db import get_session
from app.engine.pipeline import unwrap_ocr_fields
from app.engine.state_machine import REOPEN_FOR_EDIT_FROM, reopen_for_edit
from app.integrations.adapter import content_hash as compute_content_hash
from app.models import Application, Document, Employee, ExternalSnapshot, GoodsCategory, OcrResult, StatusHistory
from app.ocr.service import crm_form_ocr_result, fields_from_crm
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.storage.base import Storage

router = APIRouter(prefix="/api/manual-applications", tags=["manual-applications"])

_ROLES = ("admin", "operator")


class ManualPassportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    birth_date: str
    passport_series: str
    passport_number: str
    issue_date: str | None = None
    issued_by: str | None = None
    department_code: str | None = None
    registration_address: str | None = None
    birth_place: str | None = None
    gender: str | None = None


class ManualEmploymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    employer: str | None = None
    position: str | None = None


class ManualGuarantorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_name: str = Field(min_length=1)
    first_name: str = Field(min_length=1)
    middle_name: str | None = None
    phone: str | None = None
    birth_date: str | None = None
    passport_series: str | None = None
    passport_number: str | None = None
    issue_date: str | None = None
    registration_address: str | None = None
    income: Decimal | None = Field(default=None, ge=0)
    other_credit_payments: Decimal | None = Field(default=None, ge=0)


class ManualApplicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_name: str = Field(min_length=1)
    first_name: str = Field(min_length=1)
    middle_name: str | None = None
    phone: str = Field(min_length=5)
    passport: ManualPassportIn

    goods_name: str = Field(min_length=1)
    goods_category_code: str | None = None
    goods_price: Decimal = Field(gt=0)
    down_payment: Decimal = Field(default=Decimal("0"), ge=0)
    term_months: int = Field(gt=0)
    monthly_payment: Decimal | None = Field(default=None, gt=0)

    income: Decimal = Field(ge=0)
    other_credit_payments: Decimal = Field(default=Decimal("0"), ge=0)
    # Согласие на запрос КИ есть у всех клиентов (решение владельца
    # 16.11.2026) — в форме его нет, по умолчанию да.
    bki_consent: bool = True
    employment: ManualEmploymentIn | None = None
    guarantor: ManualGuarantorIn | None = None


class ManualApplicationOut(BaseModel):
    id: uuid.UUID
    number: int


class GoodsCategoryOption(BaseModel):
    code: str
    name: str


class ManualOptionsOut(BaseModel):
    goods_categories: list[GoodsCategoryOption]


@router.get("/options", response_model=ManualOptionsOut)
async def manual_application_options(
    _employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ManualOptionsOut:
    """Справочник категорий для формы — оператору справочники недоступны
    целиком (app/api/references.py), здесь только код и название."""
    rows = (
        await session.execute(
            select(GoodsCategory.code, GoodsCategory.name).where(GoodsCategory.active.is_(True)).order_by(GoodsCategory.name)
        )
    ).all()
    return ManualOptionsOut(goods_categories=[GoodsCategoryOption(code=code, name=name) for code, name in rows])


async def _read_photo(upload: UploadFile | None) -> tuple[bytes, str] | None:
    if upload is None:
        return None
    max_bytes = get_settings().partner_photo_max_bytes
    data = await upload.read(max_bytes + 1)
    if not data:
        return None
    if len(data) > max_bytes:
        raise _photo_too_large(max_bytes)
    _check_image_bytes(data)
    mime = next(m for sig, m in _PHOTO_SIGNATURES.items() if data.startswith(sig))
    return data, mime


def _stage_b_payload(payload: ManualApplicationIn) -> dict:
    """Тот же внутренний формат анкеты, что из /api/v1/crm/scoring
    (app/api/partner_webhooks.py) — конвейер от источника не зависит."""
    guarantor = None
    if payload.guarantor is not None:
        guarantor = {
            k: (str(v) if isinstance(v, Decimal) else v)
            for k, v in payload.guarantor.model_dump(exclude_none=True).items()
        }
    return {
        "deal": {
            "goods_category_code": payload.goods_category_code,
            "goods_price": str(payload.goods_price),
            "down_payment": str(payload.down_payment),
            "term_months": payload.term_months,
            "tariff": None,
            "monthly_payment": str(payload.monthly_payment) if payload.monthly_payment is not None else None,
        },
        "employment": payload.employment.model_dump(exclude_none=True) if payload.employment else {},
        "income": {"declared_monthly": str(payload.income)},
        "obligations": {"monthly_payment_total": str(payload.other_credit_payments)},
        "guarantor": guarantor,
        "contacts": [],
    }


def _parse_form(data: str) -> tuple["ManualApplicationIn", dict, dict]:
    """JSON анкеты → (модель, поля паспорта в формате ocr_results, значения
    паспорта как ввели). 422 — с понятной причиной."""
    try:
        payload = ManualApplicationIn.model_validate_json(data)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors(include_url=False)) from exc
    if payload.down_payment >= payload.goods_price:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="первый взнос не меньше цены товара")
    values = {
        "last_name": payload.last_name, "first_name": payload.first_name, "middle_name": payload.middle_name,
        **payload.passport.model_dump(),
    }
    fields, missing = fields_from_crm(values)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"паспорт: нет или неверный формат — {', '.join(missing)}",
        )
    return payload, fields, values


async def _read_photos(passport_main: UploadFile | None, passport_reg: UploadFile | None) -> dict:
    # Фото — до записи в базу: битый файл не оставит заявку без документов.
    return {
        kind: await _read_photo(upload)
        for kind, upload in (("passport_main", passport_main), ("passport_reg", passport_reg))
    }


def _apply_form(application: Application, payload: "ManualApplicationIn", now: datetime) -> dict:
    """Поля сделки и анкеты на самой заявке; возвращает анкету (form) для
    снимка стадии А."""
    form = {
        "last_name": payload.last_name, "first_name": payload.first_name,
        "middle_name": payload.middle_name, "phone": payload.phone,
    }
    application.goods_name = payload.goods_name
    application.goods_price = payload.goods_price
    application.down_payment = payload.down_payment
    application.term_months = payload.term_months
    application.requested_amount = payload.goods_price - payload.down_payment
    application.profile = form
    # Согласие на КИ есть у всех клиентов (решение владельца 16.11.2026) — у
    # заявки, заведённой вручную, оно ставится всегда, что бы ни прислала
    # форма: старая форма (из кэша браузера) с выключенной галочкой давала
    # серую зону «нет согласия» без кнопок запроса КИ.
    application.consent_bki_at = application.consent_bki_at or now
    return form


async def _write_form(
    session: AsyncSession, request: Request, application: Application, *,
    payload: "ManualApplicationIn", fields: dict, form: dict, photos: dict, raw_key: str,
) -> None:
    """Фото, снимки анкеты (А и Б) и паспорт (ocr_results, provider crm_form) —
    всё новыми строками (правило 1 CLAUDE.md: прежние версии остаются)."""
    storage: Storage = request.app.state.storage
    for kind, photo in photos.items():
        if photo is None:
            continue
        content, mime = photo
        sha = hashlib.sha256(content).hexdigest()
        storage_key = await storage.save(f"{application.id}/{kind}/{sha}.bin", content)
        session.add(Document(
            application_id=application.id, kind=kind, storage_key=storage_key,
            sha256=sha, mime=mime, size_bytes=len(content),
        ))

    # Правило 3 CLAUDE.md: введённое сотрудником — как есть, рядом с разобранным.
    raw = payload.model_dump(mode="json")
    form_payload = {"form": form}
    session.add(ExternalSnapshot(
        application_id=application.id, stage="A", payload_raw=form_payload,
        content_hash=compute_content_hash(form_payload),
    ))
    stage_b = {"stage_b": _stage_b_payload(payload), raw_key: raw}
    session.add(ExternalSnapshot(
        application_id=application.id, stage="B", payload_raw=stage_b, content_hash=compute_content_hash(stage_b),
    ))
    session.add(crm_form_ocr_result(application.id, raw={raw_key: raw["passport"]}, fields=fields))


@router.post("", response_model=ManualApplicationOut, status_code=status.HTTP_201_CREATED)
async def create_manual_application(
    request: Request,
    data: str = Form(..., description="анкета — JSON по схеме ManualApplicationIn"),
    passport_main: UploadFile | None = File(default=None),
    passport_reg: UploadFile | None = File(default=None),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ManualApplicationOut:
    payload, fields, _values = _parse_form(data)
    photos = await _read_photos(passport_main, passport_reg)

    now = datetime.now(timezone.utc)
    application = Application(
        # В CRM заявки нет — свой уникальный ключ; номер для людей — number.
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        source="manual",
        status="awaiting_data",
        goods_name=payload.goods_name,
        goods_price=payload.goods_price,
        down_payment=payload.down_payment,
        term_months=payload.term_months,
        tariff="unknown",
        requested_amount=payload.goods_price - payload.down_payment,
        profile={},
        consent_pd_at=now,
        submitted_at=now,
    )
    form = _apply_form(application, payload, now)
    session.add(application)
    await session.flush()
    await session.refresh(application, ["number"])  # номер выдаёт база (identity)
    session.add(StatusHistory(
        application_id=application.id, from_status=None, to_status="awaiting_data",
        actor_type="employee", employee_id=employee.id,
    ))
    await _write_form(session, request, application, payload=payload, fields=fields, form=form, photos=photos,
                      raw_key="manual_raw")
    await record_audit(
        session, employee_id=employee.id, action="create_application",
        object_type="application", object_id=application.id, request=request,
        details={"number": application.number},
    )
    await session.commit()

    from app.tasks.stage_a import process_crm_data_task

    process_crm_data_task.delay(str(application.id))
    return ManualApplicationOut(id=application.id, number=application.number)


# --- правка анкеты (решение владельца 16.11.2026) ------------------------------
# Любая заявка (ручная и из CRM). После правки — проверка заново: статус
# возвращается в awaiting_data (app/engine/state_machine.py::reopen_for_edit),
# дальше та же задача, что после анкеты с паспортом. Правка у нас в CRM не
# уходит — если CRM пришлёт анкету снова, её данные новее.


def _value(fields: dict, name: str):
    entry = fields.get(name)
    return entry.get("value") if isinstance(entry, dict) else entry


@router.get("/{application_id}/form")
async def get_application_form(
    application_id: uuid.UUID,
    _employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Текущая анкета заявки в формате формы — для правки."""
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")
    ocr_row = (
        await session.execute(
            select(OcrResult).where(OcrResult.application_id == application_id)
            .order_by(OcrResult.created_at.desc()).limit(1)
        )
    ).scalars().first()
    fields = unwrap_ocr_fields(ocr_row.fields) if ocr_row is not None else {}
    snapshot = (
        await session.execute(
            select(ExternalSnapshot)
            .where(ExternalSnapshot.application_id == application_id, ExternalSnapshot.stage == "B")
            .order_by(ExternalSnapshot.received_at.desc()).limit(1)
        )
    ).scalars().first()
    stage_b = ((snapshot.payload_raw or {}).get("stage_b") or {}) if snapshot is not None else {}
    deal = stage_b.get("deal") or {}
    profile = application.profile or {}

    def money(value, default="0") -> Decimal:
        try:
            return Decimal(str(value)) if value not in (None, "") else Decimal(default)
        except ArithmeticError:
            return Decimal(default)

    guarantor = stage_b.get("guarantor") or None
    employment = stage_b.get("employment") or None
    # model_construct и без response_model: анкета может быть неполной (заявка
    # ещё ждёт данных, цена 0) — отдаём как есть, проверка — при сохранении.
    form = ManualApplicationIn.model_construct(
        last_name=_value(fields, "last_name") or profile.get("last_name") or "",
        first_name=_value(fields, "first_name") or profile.get("first_name") or "",
        middle_name=_value(fields, "middle_name") or profile.get("middle_name"),
        phone=profile.get("phone") or "",
        passport=ManualPassportIn.model_construct(
            birth_date=_value(fields, "birth_date") or "",
            passport_series=_value(fields, "passport_series") or "",
            passport_number=_value(fields, "passport_number") or "",
            issue_date=_value(fields, "issue_date"),
            issued_by=_value(fields, "issued_by"),
            department_code=_value(fields, "department_code"),
            registration_address=_value(fields, "registration_address"),
            birth_place=_value(fields, "birth_place"),
            gender=_value(fields, "gender"),
        ),
        goods_name=application.goods_name if application.goods_name not in (None, "—") else "",
        goods_category_code=deal.get("goods_category_code"),
        goods_price=application.goods_price,
        down_payment=application.down_payment,
        term_months=application.term_months,
        monthly_payment=money(deal.get("monthly_payment"), "0") or None,
        income=money((stage_b.get("income") or {}).get("declared_monthly")),
        other_credit_payments=money((stage_b.get("obligations") or {}).get("monthly_payment_total")),
        bki_consent=application.consent_bki_at is not None,
        employment=ManualEmploymentIn.model_construct(**employment) if employment else None,
        guarantor=ManualGuarantorIn.model_construct(**guarantor) if guarantor else None,
    )
    return form.model_dump(mode="json", warnings=False)


@router.post("/{application_id}", response_model=ManualApplicationOut)
async def edit_application_form(
    application_id: uuid.UUID,
    request: Request,
    data: str = Form(..., description="анкета — JSON по схеме ManualApplicationIn"),
    passport_main: UploadFile | None = File(default=None),
    passport_reg: UploadFile | None = File(default=None),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ManualApplicationOut:
    payload, fields, _values = _parse_form(data)
    photos = await _read_photos(passport_main, passport_reg)

    application = (
        await session.execute(select(Application).where(Application.id == application_id).with_for_update())
    ).scalar_one_or_none()
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")
    if application.status != "awaiting_data" and application.status not in REOPEN_FOR_EDIT_FROM:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="сейчас идёт проверка заявки или она закрыта — попробуйте через минуту",
        )

    now = datetime.now(timezone.utc)
    form = _apply_form(application, payload, now)
    reopen_for_edit(session, application, employee_id=employee.id)
    await _write_form(session, request, application, payload=payload, fields=fields, form=form, photos=photos,
                      raw_key="edit_raw")
    await record_audit(
        session, employee_id=employee.id, action="edit_application",
        object_type="application", object_id=application.id, request=request,
        details={"number": application.number},
    )
    await session.commit()

    from app.tasks.stage_a import process_crm_data_task

    process_crm_data_task.delay(str(application.id))
    return ManualApplicationOut(id=application.id, number=application.number)
