"""T18: интеграция по новой схеме — заказчик сам шлёт нам заявки, мы отвечаем
на их эндпоинт (ваше явное указание: «формат определяем мы»). Полный контракт
и примеры — docs/integration_contract.md.

Два входа:
- POST .../applications — новая заявка с сайта (ФИО, телефон, 2 фото паспорта —
  разворот и прописка; СЕЛФИ НЕ ПРИНИМАЕМ, ваше явное указание). 202 сразу,
  дальше асинхронно: Celery-задача делает OCR → стадию А.
- POST .../review — «Принять на рассмотрение»: категория товара, цена, первый
  взнос, срок, занятость (добавлено нами — без неё ПДН стадии Б систематически
  недовычисляем, см. config/rulesets/dev.yaml stage_b_completeness), доход,
  платежи по другим кредитам, согласие на БКИ, поручитель. 202 → стадия Б.

Оба — идемпотентны по event_id (partner_inbound_events, уникальный индекс):
повторная доставка того же event_id — 202 без повторной обработки, не 409 и
не ошибка (их ретраи на таймауте — это нормально, не их вина)."""

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.integrations.adapter import content_hash as compute_content_hash
from app.models import Application, Document, ExternalSnapshot, PartnerInboundEvent, StatusHistory
from app.storage.base import Storage

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/partner", tags=["partner-webhooks"])


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


class PhotoRefIn(BaseModel):
    url: str | None = None
    base64: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "PhotoRefIn":
        if not self.url and not self.base64:
            raise ValueError("нужен url или base64")
        return self


class NewApplicationPhotosIn(BaseModel):
    # Только разворот и прописка — селфи не принимаем (ваше явное указание).
    passport_main: PhotoRefIn
    passport_reg: PhotoRefIn


class NewApplicationIn(BaseModel):
    external_id: str
    event_id: str
    last_name: str
    first_name: str
    middle_name: str | None = None
    phone: str
    photos: NewApplicationPhotosIn


class BkiConsentIn(BaseModel):
    given: bool
    at: datetime | None = None


class GuarantorIn(BaseModel):
    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    birth_date: str | None = None
    passport_series: str | None = None
    passport_number: str | None = None
    issue_date: str | None = None
    registration_address: str | None = None
    phone: str | None = None


class ContactIn(BaseModel):
    """T23: контактное лицо из анкеты (ТЗ, «Анкета» → «Контакты»). relation —
    кем приходится: parent | sibling | relative | spouse | friend | colleague |
    other (подписи — client_rating.contact_relations в правилах); нужен для
    рейтинга клиента («дал номера родителей», «только друзья»)."""

    name: str | None = None
    phone: str | None = None
    relation: str = "other"


class ReviewDealIn(BaseModel):
    goods_category_code: str | None = None
    goods_name: str | None = None
    goods_price: Decimal | None = None
    down_payment: Decimal | None = None
    term_months: int | None = None
    tariff: str | None = None
    # T21: ежемесячный платёж по нашей рассрочке — их система считает график
    # с наценкой. Нужен для ПДН; без него — запасной расчёт по тарифу из
    # правил (tariffs.<тариф>.markup_pct), см. compute_installment_payment.
    monthly_payment: Decimal | None = Field(default=None, gt=0)


class ReviewIn(BaseModel):
    external_id: str
    event_id: str
    # Их текущий статус в момент отправки — prev_status в наших ответах ниже
    # и ожидаемый статус при записи решения (замена захардкоженного "on_review").
    status: str
    deal: ReviewDealIn = Field(default_factory=ReviewDealIn)
    employment: dict[str, Any] = Field(default_factory=dict)
    income: dict[str, Any] = Field(default_factory=dict)
    obligations: dict[str, Any] = Field(default_factory=dict)
    bki_consent: BkiConsentIn
    guarantor: GuarantorIn | None = None
    # T23: контакты для рейтинга клиента; необязательны (старый формат тела
    # без них по-прежнему принимается).
    contacts: list[ContactIn] = Field(default_factory=list)


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


async def _fetch_photo(ref: PhotoRefIn) -> bytes:
    settings = get_settings()
    max_bytes = settings.partner_photo_max_bytes
    if ref.base64 is not None:
        # Оценка до декодирования: 4 символа base64 = 3 байта.
        if len(ref.base64) * 3 // 4 > max_bytes + 3:
            raise _photo_too_large(max_bytes)
        try:
            data = base64.b64decode(ref.base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"не удалось разобрать base64 фото: {exc}") from exc
        if len(data) > max_bytes:
            raise _photo_too_large(max_bytes)
        return _check_image_bytes(data)

    assert ref.url is not None  # гарантирован валидатором PhotoRefIn
    _check_photo_url(ref.url)
    chunks: list[bytes] = []
    received = 0
    try:
        # Редиректы не следуем (по умолчанию httpx) — иначе разрешённый хост
        # мог бы перенаправить на неразрешённый.
        async with httpx.AsyncClient(timeout=settings.partner_photo_timeout_seconds) as client:
            async with client.stream("GET", ref.url) as response:
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


@router.post("/applications", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_partner_webhook)])
async def receive_new_application(
    payload: NewApplicationIn, request: Request, session: AsyncSession = Depends(get_session),
) -> dict:
    existing_event_id = (
        await session.execute(
            select(PartnerInboundEvent.id).where(PartnerInboundEvent.event_id == payload.event_id)
        )
    ).scalar_one_or_none()
    if existing_event_id is not None:
        return {"status": "accepted"}

    existing_application_id = (
        await session.execute(select(Application.id).where(Application.external_id == payload.external_id))
    ).scalar_one_or_none()

    photos: dict[str, bytes] = {}
    if existing_application_id is None:
        # Фото — ДО записи в базу: сеть может подвести, не хотим частично
        # созданную заявку без документов на руках.
        photos["passport_main"] = await _fetch_photo(payload.photos.passport_main)
        photos["passport_reg"] = await _fetch_photo(payload.photos.passport_reg)

    now = datetime.now(timezone.utc)
    if existing_application_id is None:
        application = Application(
            external_id=payload.external_id,
            status="received",
            # Параметры сделки неизвестны на этом шаге в новой схеме (ТЗ T18:
            # они появляются в /review) — те же плейсхолдеры, что раньше
            # ставил polling-адаптер при отсутствии данных у партнёра.
            goods_name="—",
            goods_price=Decimal("0"),
            down_payment=Decimal("0"),
            term_months=0,
            tariff="unknown",
            requested_amount=Decimal("0"),
            profile={
                "last_name": payload.last_name, "first_name": payload.first_name,
                "middle_name": payload.middle_name, "phone": payload.phone,
            },
            consent_pd_at=now,
            submitted_at=now,
            partner_last_known_status="new",
        )
        session.add(application)
        await session.flush()
        application_id = application.id
        session.add(
            StatusHistory(application_id=application_id, from_status=None, to_status="received", actor_type="system")
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

        # Правило 3 CLAUDE.md: сырой снимок анкеты сохраняется до разбора —
        # то, что реально пришло на вебхук, тем же способом, что раньше писал
        # адаптер (stage="A"), источник блока «Снимки анкеты» в карточке (T17).
        form_payload = {
            "form": {
                "last_name": payload.last_name, "first_name": payload.first_name,
                "middle_name": payload.middle_name, "phone": payload.phone,
            }
        }
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
            event_id=payload.event_id, endpoint="applications",
            external_id=payload.external_id, application_id=application_id,
        )
    )
    await session.commit()

    if created:
        from app.tasks.stage_a import process_single_application_task

        process_single_application_task.delay(str(application_id))

    return {"status": "accepted"}


@router.post("/review", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_partner_webhook)])
async def receive_review(payload: ReviewIn, session: AsyncSession = Depends(get_session)) -> dict:
    existing_event_id = (
        await session.execute(
            select(PartnerInboundEvent.id).where(PartnerInboundEvent.event_id == payload.event_id)
        )
    ).scalar_one_or_none()
    if existing_event_id is not None:
        return {"status": "accepted"}

    application = (
        await session.execute(select(Application).where(Application.external_id == payload.external_id))
    ).scalar_one_or_none()
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="external_id не известен — заявка не приходила через /api/v1/partner/applications",
        )

    deal = payload.deal
    if deal.goods_name is not None:
        application.goods_name = deal.goods_name
    if deal.goods_price is not None:
        application.goods_price = deal.goods_price
    if deal.down_payment is not None:
        application.down_payment = deal.down_payment
    if deal.term_months is not None:
        application.term_months = deal.term_months
    if deal.tariff is not None:
        application.tariff = deal.tariff
    application.requested_amount = application.goods_price - application.down_payment
    application.partner_last_known_status = payload.status
    if payload.bki_consent.given:
        application.consent_bki_at = payload.bki_consent.at or datetime.now(timezone.utc)

    stage_b_payload = {
        "deal": {
            "goods_category_code": deal.goods_category_code,
            "goods_price": str(deal.goods_price) if deal.goods_price is not None else None,
            "down_payment": str(deal.down_payment) if deal.down_payment is not None else None,
            "term_months": deal.term_months,
            "tariff": deal.tariff,
            "monthly_payment": str(deal.monthly_payment) if deal.monthly_payment is not None else None,
        },
        "employment": payload.employment,
        "income": payload.income,
        "obligations": payload.obligations,
        "guarantor": payload.guarantor.model_dump(exclude_none=True) if payload.guarantor else None,
        "contacts": [c.model_dump(exclude_none=True) for c in payload.contacts],
    }
    payload_raw = {"stage_b": stage_b_payload}
    session.add(
        ExternalSnapshot(
            application_id=application.id, stage="B", payload_raw=payload_raw,
            content_hash=compute_content_hash(payload_raw),
        )
    )
    session.add(
        PartnerInboundEvent(
            event_id=payload.event_id, endpoint="review",
            external_id=payload.external_id, application_id=application.id,
        )
    )
    await session.commit()
    application_id = application.id

    from app.tasks.stage_b import process_single_application_task

    process_single_application_task.delay(str(application_id))

    return {"status": "accepted"}
