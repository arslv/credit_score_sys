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
from app.integrations.adapter import content_hash as compute_content_hash
from app.models import Application, Document, Employee, ExternalSnapshot, GoodsCategory, StatusHistory
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
    bki_consent: bool
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


@router.post("", response_model=ManualApplicationOut, status_code=status.HTTP_201_CREATED)
async def create_manual_application(
    request: Request,
    data: str = Form(..., description="анкета — JSON по схеме ManualApplicationIn"),
    passport_main: UploadFile | None = File(default=None),
    passport_reg: UploadFile | None = File(default=None),
    employee: Employee = Depends(require_roles(*_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ManualApplicationOut:
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

    # Фото — до записи в базу: битый файл не оставит заявку без документов.
    photos = {kind: await _read_photo(upload) for kind, upload in (("passport_main", passport_main), ("passport_reg", passport_reg))}

    now = datetime.now(timezone.utc)
    form = {
        "last_name": payload.last_name, "first_name": payload.first_name,
        "middle_name": payload.middle_name, "phone": payload.phone,
    }
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
        profile=form,
        consent_pd_at=now,
        consent_bki_at=now if payload.bki_consent else None,
        submitted_at=now,
    )
    session.add(application)
    await session.flush()
    await session.refresh(application, ["number"])  # номер выдаёт база (identity)
    session.add(StatusHistory(
        application_id=application.id, from_status=None, to_status="awaiting_data",
        actor_type="employee", employee_id=employee.id,
    ))

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
    stage_b = {"stage_b": _stage_b_payload(payload), "manual_raw": raw}
    session.add(ExternalSnapshot(
        application_id=application.id, stage="B", payload_raw=stage_b, content_hash=compute_content_hash(stage_b),
    ))
    session.add(crm_form_ocr_result(application.id, raw={"manual": raw["passport"]}, fields=fields))
    await record_audit(
        session, employee_id=employee.id, action="create_application",
        object_type="application", object_id=application.id, request=request,
        details={"number": application.number},
    )
    await session.commit()

    from app.tasks.stage_a import process_crm_data_task

    process_crm_data_task.delay(str(application.id))
    return ManualApplicationOut(id=application.id, number=application.number)
