import base64
import random
from contextlib import asynccontextmanager
from datetime import date

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


app = FastAPI(title="fake_partner — заглушка CRM по её API (api.yml)", lifespan=lifespan)

# Пути их API (api.yml, «Входящие запросы» с их стороны) и нашего входа для них.
PASSPORT_RESULT_PATH = "/v1/integration/scoring/passport-result"
RESULT_PATH = "/v1/integration/scoring/result"
OUR_NEW_ORDER_PATH = "/api/v1/crm/new-order"
OUR_SCORING_PATH = "/api/v1/crm/scoring"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- то, что принимает НАСТОЯЩАЯ CRM (их вход для наших POST) -------------------


def _verify_inbound(request: Request) -> None:
    settings = get_settings()
    expected = f"Bearer {settings.expected_inbound_token}"
    if request.headers.get("authorization") != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing token")


async def _receive(request: Request, kind: str):
    """store.mode — для тестов повторов/конфликта: "unavailable" -> 503 без
    сохранения; "conflict" (только для решений) -> 409 {current_status,
    reason}. В API CRM 409 нет — режим проверяет нашу обработку на случай,
    если CRM начнёт так отвечать."""
    _verify_inbound(request)
    body = await request.json()

    if store.mode == "unavailable":
        return JSONResponse(status_code=503, content={"detail": "simulated unavailable"})
    if store.mode == "conflict" and kind == "decision":
        return JSONResponse(
            status_code=409,
            content={"current_status": store.conflict_current_status, "reason": store.conflict_reason},
        )
    if "orderId" not in body:
        return JSONResponse(status_code=422, content={"detail": "нет orderId"})

    store.receive(kind, body)
    return {"ok": True}


@app.post(PASSPORT_RESULT_PATH)
async def receive_passport_result(request: Request):
    return await _receive(request, "ocr_result")


@app.post(RESULT_PATH)
async def receive_result(request: Request):
    return await _receive(request, "decision")


# --- служебные эндпоинты для тестов --------------------------------------------


@app.post("/_test/reset", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_service_account)])
def test_reset():
    store.reset()


@app.get("/_test/received/{external_id}", dependencies=[Depends(require_service_account)])
def test_received(external_id: str) -> dict:
    """Всё, что мы (dialog-scoring) прислали по этой заявке: тело как есть
    плюс type (ocr_result | decision — по адресу) и external_id (= orderId)."""
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
    """Имитирует оператора, вручную сменившего статус в CRM в обход нас.
    409 на наш следующий результат — только если включён mode="conflict"."""
    store.set_manual_status(external_id, payload.status)


@app.get("/_test/photos/{token}")
def test_get_photo(token: str):
    """URL-вариант передачи фото — без авторизации, как временная ссылка."""
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
        "passport_series": f"{random.randrange(10000):04d}",
        "passport_number": f"{random.randrange(1000000):06d}",
        "issue_date": date(2015, 3, 10).isoformat(),
        "issued_by": "УМВД России по тестовому региону",
        "department_code": "770-001",
        "registration_address": "г. Тестоград, ул. Примерная, д. 1, кв. 1",
        **(payload.passport or {}),
    }


def _photo_ref(data: bytes, mode: str) -> str:
    if mode == "url":
        token = store.store_photo(data)
        return f"http://fake_partner:8000/_test/photos/{token}"
    return base64.b64encode(data).decode("ascii")


def _new_order_id() -> int:
    return random.randrange(10**8, 10**9)


def _build_new_order_body(payload: TriggerNewApplicationRequest) -> dict:
    """Тело POST /new-order (NewOrderNotification) — как его собрала бы CRM."""
    fields = _default_passport_fields(payload)
    return {
        "orderId": payload.order_id if payload.order_id is not None else _new_order_id(),
        "lastName": payload.last_name,
        "firstName": payload.first_name,
        "fatherName": payload.middle_name,
        "phone": payload.phone,
        "passportPhotos": [
            _photo_ref(render_passport_main(fields), payload.photo_mode),
            _photo_ref(render_passport_registration(fields), payload.photo_mode),
        ],
    }


def _build_scoring_body(external_id: str, payload: TriggerReviewRequest) -> dict:
    """Тело POST /scoring (ScoringRequest) — «Рассмотреть». Поля
    employment/contacts/goodsName/tariff/monthlyPayment — наши
    необязательные расширения (в api.yml CRM их нет); send_extensions=False
    — строго их формат."""
    body: dict = {
        "orderId": int(external_id) if external_id.isdigit() else external_id,
        "productCategory": payload.goods_category_code,
        "price": payload.goods_price,
        "firstPayment": payload.down_payment,
        "period": payload.term_months,
        "clientIncome": payload.declared_monthly_income,
        "otherCreditPayments": payload.declared_monthly_obligations,
        "bkiConsent": payload.bki_consent,
        "guarantors": [payload.guarantor] if payload.guarantor else [],
    }
    if not payload.send_extensions:
        return body
    body["goodsName"] = payload.goods_name
    body["tariff"] = payload.tariff
    if payload.send_monthly_payment and payload.term_months > 0:
        body["monthlyPayment"] = (
            payload.monthly_payment if payload.monthly_payment is not None
            else round((payload.goods_price - payload.down_payment) / payload.term_months, 2)
        )
    body["employment"] = {
        "type": payload.employment_type,
        **({"employer": payload.employer} if payload.employer else {}),
        **({"position": payload.position} if payload.position else {}),
    }
    body["contacts"] = payload.contacts or []
    if payload.applicant:
        body["applicant"] = payload.applicant
    return body


@app.post("/_test/compose-new-application", dependencies=[Depends(require_service_account)])
def compose_new_application(payload: TriggerNewApplicationRequest) -> dict:
    """То же тело, что шлёт trigger-new-application, но БЕЗ отправки — его
    доставляет сам вызывающий (демо-сид под «виртуальными часами»)."""
    body = _build_new_order_body(payload)
    return {"body": body, "external_id": str(body["orderId"])}


@app.post("/_test/compose-review/{external_id}", dependencies=[Depends(require_service_account)])
def compose_review(external_id: str, payload: TriggerReviewRequest) -> dict:
    return {"body": _build_scoring_body(external_id, payload)}


async def _send(path: str, body: dict, what: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.dialog_api_base_url}{path}",
            json=body, headers={"Authorization": f"Bearer {settings.dialog_api_token}"},
        )
    # Правило 14 CLAUDE.md — ответ проверяется явно, даже в тестовом хелпере.
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"dialog-scoring отклонил {what}: {response.status_code} {response.text}")


@app.post("/_test/trigger-new-application", dependencies=[Depends(require_service_account)])
async def trigger_new_application(payload: TriggerNewApplicationRequest) -> dict:
    """Имитирует CRM: собирает тело POST /new-order и сама его отправляет."""
    body = _build_new_order_body(payload)
    await _send(OUR_NEW_ORDER_PATH, body, "заявку")
    return {"external_id": str(body["orderId"])}


@app.post("/_test/trigger-review/{external_id}", dependencies=[Depends(require_service_account)])
async def trigger_review(external_id: str, payload: TriggerReviewRequest) -> dict:
    """Имитирует оператора CRM, нажавшего «Рассмотреть» — POST /scoring."""
    body = _build_scoring_body(external_id, payload)
    await _send(OUR_SCORING_PATH, body, "анкету")
    return {"external_id": external_id}
