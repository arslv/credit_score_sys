"""Вход от CRM — по их API (api.yml от CRM, «Исходящие запросы» с их стороны).
Полный контракт — docs/integration_contract.md.

Два входа (базовый адрес, который мы даём CRM: https://<наш хост>/api/v1/crm):
- POST /new-order — новая заявка с сайта: номер заявки (orderId), ФИО,
  телефон, фото паспорта (passportPhotos: первое — разворот, второе —
  прописка; base64 или ссылка). Сразу 200, дальше асинхронно: OCR → стадия А.
- POST /scoring — «Рассмотреть»: категория, цена, первый взнос, срок, доход,
  платежи по кредитам, согласие на БКИ, поручители. 200 → стадия Б.

Их тела переводим во внутренний формат анкеты (тот же снимок external_
snapshots stage="B", что читает стадия Б) — конвейер от формата CRM не
зависит. Поля, которых в их API нет, но которые мы примем, если CRM начнёт их
присылать (employment, contacts, applicant, goodsName, monthlyPayment,
tariff), — необязательные расширения, см. контракт.

Идемпотентность (правило 11 CLAUDE.md): event_id в их запросах нет — свой
ключ: /new-order — по orderId; /scoring — по orderId + содержимому тела
(тот же запрос повторно — ничего не делаем; анкету поправили — новая стадия Б)."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.integrations.adapter import content_hash as compute_content_hash
from app.models import Application, Document, ExternalSnapshot, PartnerInboundEvent, StatusHistory
from app.ocr.service import crm_form_ocr_result, fields_from_crm
from app.storage.base import Storage
from app.system_settings import is_ocr_enabled

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])


async def verify_partner_webhook(request: Request) -> None:
    """Статичный токен в заголовке (разный для test/prod — значение из env,
    не код) + опциональный allowlist IP (ТЗ T18). Пустой allowlist — проверка
    выключена: в dev/test заголовок приходит из docker-сети/локалхоста,
    allowlist там бессмысленен, заполнять его обязаны в проде (docs/deployment.md)."""
    settings = get_settings()
    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.lower().startswith("bearer ") else ""
    # T21: сравнение за постоянное время — по времени ответа нельзя подобрать
    # токен посимвольно.
    if not token or not hmac.compare_digest(token.encode(), settings.partner_inbound_token.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing token")

    allowlist = [ip.strip() for ip in settings.partner_ip_allowlist.split(",") if ip.strip()]
    if allowlist:
        client_ip = request.client.host if request.client else None
        if client_ip not in allowlist:
            logger.warning("partner_webhook_ip_rejected", client_ip=client_ip)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ip not allowed")


class NewOrderIn(BaseModel):
    """NewOrderNotification из их api.yml."""

    model_config = ConfigDict(extra="ignore")

    orderId: int
    lastName: str
    firstName: str
    fatherName: str | None = None
    phone: str
    # Первое — разворот, второе — прописка; base64 (можно с префиксом
    # data:image/...;base64,) или https-ссылка. Остальные (если пришлют) не
    # используем — селфи не принимаем (ваше явное указание).
    passportPhotos: list[str] = Field(default_factory=list)


class ScoringGuarantorIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lastName: str | None = None
    firstName: str | None = None
    fatherName: str | None = None
    phone: str | None = None
    income: Decimal | None = None
    otherCreditPayments: Decimal | None = None
    # Расширение: без даты рождения и паспорта поручителя не проверить
    # (перечни, ФССП, БКИ) — примем, если CRM начнёт присылать.
    birthDate: str | None = None
    passportSeries: str | None = None
    passportNumber: str | None = None
    issuedDate: str | None = None
    registrationAddress: str | None = None


class ApplicantPassportIn(BaseModel):
    """Расширение: паспортные данные заявителя — нужны только когда
    распознавание фото выключено в админке (заявка ждёт в awaiting_data)."""

    model_config = ConfigDict(extra="ignore")

    lastName: str | None = None
    firstName: str | None = None
    fatherName: str | None = None
    birthDate: str | None = None
    birthPlace: str | None = None
    gender: str | None = None
    passportSeries: str | None = None
    passportNumber: str | None = None
    issuedDate: str | None = None
    issuedBy: str | None = None
    divisionCode: str | None = None
    registrationAddress: str | None = None


class ContactIn(BaseModel):
    """Расширение: контактное лицо для рейтинга клиента. relation — parent |
    sibling | relative | spouse | friend | colleague | other."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    phone: str | None = None
    relation: str = "other"


class EmploymentIn(BaseModel):
    """Расширение: занятость (обязательна для стадии Б по правилам —
    stage_b_completeness)."""

    model_config = ConfigDict(extra="ignore")

    type: str | None = None
    employer: str | None = None
    position: str | None = None


class ScoringIn(BaseModel):
    """ScoringRequest из их api.yml + необязательные расширения."""

    model_config = ConfigDict(extra="ignore")

    orderId: int
    productCategory: str | None = None
    price: Decimal | None = None
    firstPayment: Decimal | None = None
    period: int | None = None
    clientIncome: Decimal | None = None
    otherCreditPayments: Decimal | None = None
    bkiConsent: bool = False
    guarantors: list[ScoringGuarantorIn] = Field(default_factory=list)

    goodsName: str | None = None
    tariff: str | None = None
    # T21: ежемесячный платёж по рассрочке из их графика; без него —
    # запасной расчёт по тарифу из правил.
    monthlyPayment: Decimal | None = Field(default=None, gt=0)
    employment: EmploymentIn | None = None
    contacts: list[ContactIn] = Field(default_factory=list)
    applicant: ApplicantPassportIn | None = None


# T21: фото — только JPEG или PNG. Проверяем и заголовок ответа (для url), и
# сами байты (сигнатуру файла) — заголовку отправителя на слово не верим.
_ALLOWED_PHOTO_TYPES = ("image/jpeg", "image/png")
_PHOTO_SIGNATURES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


def _photo_too_large(max_bytes: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"фото больше допустимого размера {max_bytes // (1024 * 1024)} МБ",
    )


def _check_image_bytes(data: bytes) -> bytes:
    if not any(data.startswith(signature) for signature in _PHOTO_SIGNATURES):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="фото должно быть в формате JPEG или PNG",
        )
    return data


def _check_photo_url(url: str) -> None:
    """Только https и только хосты из PARTNER_PHOTO_HOSTS (точное имя, без
    поддоменов) — иначе ссылка в заявке становилась бы способом заставить наш
    сервер сходить по любому адресу (в том числе во внутреннюю сеть)."""
    settings = get_settings()
    parts = urlsplit(url)
    allowed_schemes = ("https", "http") if settings.partner_photo_allow_http else ("https",)
    if parts.scheme not in allowed_schemes:
        raise HTTPException(status_code=422, detail="ссылка на фото должна быть https")
    allowed_hosts = {h.strip().lower() for h in settings.partner_photo_hosts.split(",") if h.strip()}
    host = (parts.hostname or "").lower()
    if not allowed_hosts or host not in allowed_hosts:
        raise HTTPException(
            status_code=422,
            detail="хост ссылки на фото не разрешён — фото принимаем только с согласованных хостов "
            "(см. docs/integration_contract.md), либо передайте base64",
        )


def _decode_base64_photo(value: str, max_bytes: int) -> bytes:
    if value.startswith("data:"):
        value = value.split(",", 1)[1] if "," in value else ""
    # Оценка до декодирования: 4 символа base64 = 3 байта.
    if len(value) * 3 // 4 > max_bytes + 3:
        raise _photo_too_large(max_bytes)
    try:
        data = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"не удалось разобрать base64 фото: {exc}") from exc
    if len(data) > max_bytes:
        raise _photo_too_large(max_bytes)
    return _check_image_bytes(data)


async def _fetch_photo(ref: str) -> bytes:
    """Строка из passportPhotos: ссылка (http/https) или base64."""
    settings = get_settings()
    max_bytes = settings.partner_photo_max_bytes
    if not ref.lower().startswith(("http://", "https://")):
        return _decode_base64_photo(ref.strip(), max_bytes)

    _check_photo_url(ref)
    chunks: list[bytes] = []
    received = 0
    try:
        # Редиректы не следуем (по умолчанию httpx) — иначе разрешённый хост
        # мог бы перенаправить на неразрешённый.
        async with httpx.AsyncClient(timeout=settings.partner_photo_timeout_seconds) as client:
            async with client.stream("GET", ref) as response:
                # Правило 14 CLAUDE.md: ответ любого HTTP-вызова проверяется явно.
                if response.status_code != 200:
                    raise HTTPException(status_code=422, detail=f"фото по url вернуло HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type not in _ALLOWED_PHOTO_TYPES:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail="фото по ссылке должно отдаваться как image/jpeg или image/png",
                    )
                declared_length = response.headers.get("content-length")
                if declared_length is not None and declared_length.isdigit() and int(declared_length) > max_bytes:
                    raise _photo_too_large(max_bytes)
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:  # Content-Length мог соврать или не прийти
                        raise _photo_too_large(max_bytes)
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=422, detail=f"фото по url недоступно: {exc.__class__.__name__}") from exc
    return _check_image_bytes(b"".join(chunks))


async def _already_received(session: AsyncSession, event_id: str) -> bool:
    found = (
        await session.execute(select(PartnerInboundEvent.id).where(PartnerInboundEvent.event_id == event_id))
    ).scalar_one_or_none()
    return found is not None


@router.post("/new-order", dependencies=[Depends(verify_partner_webhook)])
async def receive_new_order(
    payload: NewOrderIn, request: Request, session: AsyncSession = Depends(get_session),
) -> dict:
    external_id = str(payload.orderId)
    event_id = f"new-order:{external_id}"
    if await _already_received(session, event_id):
        return {"status": "accepted"}

    existing_application_id = (
        await session.execute(select(Application.id).where(Application.external_id == external_id))
    ).scalar_one_or_none()

    photos: dict[str, bytes] = {}
    if existing_application_id is None:
        if len(payload.passportPhotos) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="нужны два фото паспорта: passportPhotos[0] — разворот с фото, [1] — прописка",
            )
        # Фото — ДО записи в базу: сеть может подвести, не хотим частично
        # созданную заявку без документов на руках.
        photos["passport_main"] = await _fetch_photo(payload.passportPhotos[0])
        photos["passport_reg"] = await _fetch_photo(payload.passportPhotos[1])

    now = datetime.now(timezone.utc)
    # Распознавание выключено в админке -> заявка ждёт паспортные данные от
    # CRM в /scoring, стадия А сейчас не запускается.
    initial_status = "received" if await is_ocr_enabled(session) else "awaiting_data"
    form = {
        "last_name": payload.lastName, "first_name": payload.firstName,
        "middle_name": payload.fatherName, "phone": payload.phone,
    }
    if existing_application_id is None:
        application = Application(
            external_id=external_id,
            status=initial_status,
            # Параметры сделки на этом шаге неизвестны — придут в /scoring.
            goods_name="—",
            goods_price=Decimal("0"),
            down_payment=Decimal("0"),
            term_months=0,
            tariff="unknown",
            requested_amount=Decimal("0"),
            profile=form,
            consent_pd_at=now,
            submitted_at=now,
            partner_last_known_status="new",
        )
        session.add(application)
        await session.flush()
        application_id = application.id
        session.add(
            StatusHistory(
                application_id=application_id, from_status=None, to_status=initial_status, actor_type="system",
            )
        )

        storage: Storage = request.app.state.storage
        for kind, data in photos.items():
            sha = hashlib.sha256(data).hexdigest()
            storage_key = await storage.save(f"{application_id}/{kind}/{sha}.bin", data)
            session.add(
                Document(
                    application_id=application_id, kind=kind, storage_key=storage_key,
                    sha256=sha, mime="image/png", size_bytes=len(data),
                )
            )

        # Правило 3 CLAUDE.md: сырой снимок анкеты сохраняется до разбора
        # (без фото — они уже лежат документами).
        form_payload = {"form": form}
        session.add(
            ExternalSnapshot(
                application_id=application_id, stage="A",
                payload_raw=form_payload, content_hash=compute_content_hash(form_payload),
            )
        )
        created = True
    else:
        application_id = existing_application_id
        created = False

    session.add(
        PartnerInboundEvent(
            event_id=event_id, endpoint="new-order", external_id=external_id, application_id=application_id,
        )
    )
    await session.commit()

    if created and initial_status == "received":
        from app.tasks.stage_a import process_single_application_task

        process_single_application_task.delay(str(application_id))

    return {"status": "accepted"}


def _applicant_values(applicant: ApplicantPassportIn | None, profile: dict[str, Any]) -> dict[str, Any]:
    """Расширение applicant (их стиль имён) → наш формат паспортных полей."""
    a = applicant or ApplicantPassportIn()
    return {
        "last_name": a.lastName or profile.get("last_name"),
        "first_name": a.firstName or profile.get("first_name"),
        "middle_name": a.fatherName or profile.get("middle_name"),
        "birth_date": a.birthDate,
        "birth_place": a.birthPlace,
        "gender": a.gender,
        "passport_series": a.passportSeries,
        "passport_number": a.passportNumber,
        "issue_date": a.issuedDate,
        "issued_by": a.issuedBy,
        "department_code": a.divisionCode,
        "registration_address": a.registrationAddress,
    }


def _guarantor_payload(g: ScoringGuarantorIn) -> dict[str, Any]:
    payload = {
        "last_name": g.lastName,
        "first_name": g.firstName,
        "middle_name": g.fatherName,
        "phone": g.phone,
        "birth_date": g.birthDate,
        "passport_series": g.passportSeries,
        "passport_number": g.passportNumber,
        "issue_date": g.issuedDate,
        "registration_address": g.registrationAddress,
        "income": str(g.income) if g.income is not None else None,
        "other_credit_payments": str(g.otherCreditPayments) if g.otherCreditPayments is not None else None,
    }
    return {k: v for k, v in payload.items() if v is not None}


@router.post("/scoring", dependencies=[Depends(verify_partner_webhook)])
async def receive_scoring(payload: ScoringIn, request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    external_id = str(payload.orderId)
    raw_body = await request.json()
    body_hash = hashlib.sha256(
        json.dumps(raw_body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    event_id = f"scoring:{external_id}:{body_hash}"
    if await _already_received(session, event_id):
        return {"status": "accepted"}

    application = (
        await session.execute(select(Application).where(Application.external_id == external_id))
    ).scalar_one_or_none()
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="orderId не известен — заявка не приходила через /new-order",
        )

    # Заявка ждёт паспортные данные от CRM (распознавание было выключено):
    # без них стадию А провести не из чего — 422 до любой записи.
    crm_passport = None
    if application.status == "awaiting_data":
        values = _applicant_values(payload.applicant, application.profile or {})
        fields, missing = fields_from_crm(values)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="распознавание паспорта выключено — пришлите паспортные данные заявителя в applicant; "
                f"нет или неверный формат: {', '.join(missing)}",
            )
        crm_passport = (values, fields)

    application.goods_name = payload.goodsName or payload.productCategory or application.goods_name
    if payload.price is not None:
        application.goods_price = payload.price
    if payload.firstPayment is not None:
        application.down_payment = payload.firstPayment
    if payload.period is not None:
        application.term_months = payload.period
    if payload.tariff is not None:
        application.tariff = payload.tariff
    application.requested_amount = application.goods_price - application.down_payment
    if payload.bkiConsent:
        application.consent_bki_at = datetime.now(timezone.utc)

    # Внутренний формат анкеты стадии Б (тот же, что читает конвейер).
    # Поручитель у нас один — первый из guarantors; все — в сыром теле.
    employment = payload.employment.model_dump(exclude_none=True) if payload.employment else {}
    stage_b_payload = {
        "deal": {
            "goods_category_code": payload.productCategory,
            "goods_price": str(payload.price) if payload.price is not None else None,
            "down_payment": str(payload.firstPayment) if payload.firstPayment is not None else None,
            "term_months": payload.period,
            "tariff": payload.tariff,
            "monthly_payment": str(payload.monthlyPayment) if payload.monthlyPayment is not None else None,
        },
        "employment": employment,
        "income": {"declared_monthly": str(payload.clientIncome)} if payload.clientIncome is not None else {},
        "obligations": (
            {"monthly_payment_total": str(payload.otherCreditPayments)}
            if payload.otherCreditPayments is not None else {}
        ),
        "guarantor": _guarantor_payload(payload.guarantors[0]) if payload.guarantors else None,
        "contacts": [c.model_dump(exclude_none=True) for c in payload.contacts],
    }
    # Правило 3 CLAUDE.md: сырое тело — рядом с разобранным.
    payload_raw = {"stage_b": stage_b_payload, "crm_raw": raw_body}
    session.add(
        ExternalSnapshot(
            application_id=application.id, stage="B", payload_raw=payload_raw,
            content_hash=compute_content_hash(payload_raw),
        )
    )
    if crm_passport is not None:
        values, fields = crm_passport
        session.add(crm_form_ocr_result(application.id, raw={"applicant": values}, fields=fields))
    session.add(
        PartnerInboundEvent(event_id=event_id, endpoint="scoring", external_id=external_id, application_id=application.id)
    )
    await session.commit()
    application_id = application.id

    if crm_passport is not None:
        # Стадия А по данным CRM, затем сразу стадия Б (app/tasks/stage_a.py).
        from app.tasks.stage_a import process_crm_data_task

        process_crm_data_task.delay(str(application_id))
    else:
        from app.tasks.stage_b import process_single_application_task

        process_single_application_task.delay(str(application_id))

    return {"status": "accepted"}
