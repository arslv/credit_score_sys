import base64
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.auth import require_service_account
from app.config import get_settings
from app.images import render_passport_main, render_passport_registration
from app.schemas import ConfigureRequest, ManualStatusRequest, TriggerNewApplicationRequest, TriggerReviewRequest
from app.store import store


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="fake_partner — заглушка системы заказчика (T18: push-схема)", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- то, что принимает НАСТОЯЩИЙ эндпоинт заказчика (их вход для наших POST) ---


def _verify_inbound(request: Request) -> None:
    settings = get_settings()
    expected = f"Bearer {settings.expected_inbound_token}"
    if request.headers.get("authorization") != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing token")


@app.post("/partner/webhook")
async def receive_webhook(request: Request):
    """Единственный эндпоинт на их стороне (docs/integration_contract.md) —
    тело различается полем "type" (ocr_result | decision | comment).
    store.mode — для тестов повторов/конфликта: "unavailable" -> 503 без
    сохранения, "conflict" (только для type="decision") -> 409 с телом
    {current_status, reason} — ровно так, как задаёт наш же контракт."""
    _verify_inbound(request)
    body = await request.json()

    if store.mode == "unavailable":
        return JSONResponse(status_code=503, content={"detail": "simulated unavailable"})
    if store.mode == "conflict" and body.get("type") == "decision":
        return JSONResponse(
            status_code=409,
            content={"current_status": store.conflict_current_status, "reason": store.conflict_reason},
        )

    store.receive(body)
    return {"ok": True}


# --- служебные эндпоинты для тестов --------------------------------------------


@app.post("/_test/reset", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_service_account)])
def test_reset():
    store.reset()


@app.get("/_test/received/{external_id}", dependencies=[Depends(require_service_account)])
def test_received(external_id: str) -> dict:
    """Всё, что мы (dialog-scoring) прислали на /partner/webhook по этой
    заявке — замена опросу их API из polling-эры (тесты смотрят сюда вместо
    get_application(...).status/comments)."""
    return {"items": store.received_items(external_id), "current_status": store.current_status(external_id)}


@app.post("/_test/configure", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_service_account)])
def test_configure(payload: ConfigureRequest):
    store.mode = payload.mode
    store.conflict_current_status = payload.conflict_current_status
    store.conflict_reason = payload.conflict_reason


@app.post(
    "/_test/manual-status/{external_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_service_account)],
)
def test_manual_status(external_id: str, payload: ManualStatusRequest):
    """Имитирует оператора, вручную сменившего статус в их системе в обход
    нас (правило 12 CLAUDE.md) — следующий наш POST .../webhook с устаревшим
    prev_status получит 409 ТОЛЬКО если дополнительно включён store.mode=
    "conflict" (сама по себе смена статуса здесь ничего не проверяет — это
    задел на будущее: настоящая система заказчика сверяла бы prev_status
    сама; наша заглушка делает это явно через /_test/configure)."""
    store.set_manual_status(external_id, payload.status)


@app.get("/_test/photos/{token}")
def test_get_photo(token: str):
    """URL-вариант передачи фото (ТЗ T18: «временная ссылка ИЛИ base64») —
    без авторизации, как и настоящая временная ссылка."""
    data = store.get_photo(token)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    return Response(content=data, media_type="image/png")


def _default_passport_fields(payload: TriggerNewApplicationRequest) -> dict:
    birth = payload.birth_date or date(1990, 1, 1)
    return {
        "last_name": payload.last_name, "first_name": payload.first_name,
        "middle_name": payload.middle_name, "birth_date": birth.isoformat(),
        "gender": "female" if payload.first_name.endswith(("а", "я")) else "male",
        "passport_series": f"{uuid.uuid4().int % 10000:04d}",
        "passport_number": f"{uuid.uuid4().int % 1000000:06d}",
        "issue_date": date(2015, 3, 10).isoformat(),
        "issued_by": "УМВД России по тестовому региону",
        "department_code": "770-001",
        "registration_address": "г. Тестоград, ул. Примерная, д. 1, кв. 1",
    }


def _photo_ref(data: bytes, mode: str) -> dict:
    if mode == "url":
        token = store.store_photo(data)
        return {"url": f"http://fake_partner:8000/_test/photos/{token}"}
    return {"base64": base64.b64encode(data).decode("ascii")}


@app.post("/_test/trigger-new-application", dependencies=[Depends(require_service_account)])
async def trigger_new_application(payload: TriggerNewApplicationRequest) -> dict:
    """Имитирует заказчика: собирает тело POST .../api/v1/partner/applications
    и сама его отправляет (ТЗ T18: «fake_partner шлёт нам»)."""
    external_id = payload.external_id or f"WEB-{uuid.uuid4().hex[:10]}"
    event_id = str(uuid.uuid4())
    fields = _default_passport_fields(payload)

    body = {
        "external_id": external_id,
        "event_id": event_id,
        "last_name": payload.last_name,
        "first_name": payload.first_name,
        "middle_name": payload.middle_name,
        "phone": payload.phone,
        "photos": {
            "passport_main": _photo_ref(render_passport_main(fields), payload.photo_mode),
            "passport_reg": _photo_ref(render_passport_registration(fields), payload.photo_mode),
        },
    }

    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.dialog_api_base_url}/api/v1/partner/applications",
            json=body, headers={"Authorization": f"Bearer {settings.dialog_api_token}"},
        )
    # Правило 14 CLAUDE.md — ответ проверяется явно, даже в тестовом хелпере.
    if response.status_code != 202:
        raise HTTPException(
            status_code=502, detail=f"dialog-scoring отклонил заявку: {response.status_code} {response.text}"
        )
    return {"external_id": external_id, "event_id": event_id}


@app.post("/_test/trigger-review/{external_id}", dependencies=[Depends(require_service_account)])
async def trigger_review(external_id: str, payload: TriggerReviewRequest) -> dict:
    """Имитирует оператора, нажавшего «Принять на рассмотрение» — собирает
    тело POST .../api/v1/partner/review и сама его отправляет."""
    event_id = str(uuid.uuid4())
    body = {
        "external_id": external_id,
        "event_id": event_id,
        "status": payload.status,
        "deal": {
            "goods_category_code": payload.goods_category_code,
            "goods_name": payload.goods_name,
            "goods_price": payload.goods_price,
            "down_payment": payload.down_payment,
            "term_months": payload.term_months,
            "tariff": payload.tariff,
            **(
                {"monthly_payment": payload.monthly_payment if payload.monthly_payment is not None
                 else round((payload.goods_price - payload.down_payment) / payload.term_months, 2)}
                if payload.send_monthly_payment and payload.term_months > 0
                else {}
            ),
        },
        "employment": {"type": payload.employment_type},
        "income": {"declared_monthly": payload.declared_monthly_income},
        "obligations": {"monthly_payment_total": payload.declared_monthly_obligations},
        "bki_consent": {
            "given": payload.bki_consent,
            "at": datetime.now(timezone.utc).isoformat() if payload.bki_consent else None,
        },
        "guarantor": payload.guarantor,
    }

    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.dialog_api_base_url}/api/v1/partner/review",
            json=body, headers={"Authorization": f"Bearer {settings.dialog_api_token}"},
        )
    if response.status_code != 202:
        raise HTTPException(
            status_code=502, detail=f"dialog-scoring отклонил анкету: {response.status_code} {response.text}"
        )
    return {"external_id": external_id, "event_id": event_id}
