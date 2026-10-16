"""T22: тесты заглушки заказчика под схему T18 (push): она шлёт нам вебхуки
(/_test/trigger-*) и принимает наши ответы (/partner/webhook). Старые тесты
API опроса (GET /applications, фото по ID, смена статуса, «Рассмотреть»)
удалены вместе с этим API в T18. Вызовы нашего API мокаются через respx
(правило 10 CLAUDE.md), ответ каждого вызова проверяется (правило 14)."""

import base64
import json

import pytest
import respx
from httpx import Response

from app.config import get_settings

_PNG = b"\x89PNG\r\n\x1a\n"


def _decision(external_id: str, status: str = "approved", event_id: str = "ev-1") -> dict:
    return {"type": "decision", "external_id": external_id, "event_id": event_id, "status": status,
            "prev_status": "on_review", "comment": "Одобрено"}


def test_health_requires_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/_test/reset"),
        ("get", "/_test/received/X"),
        ("post", "/_test/configure"),
        ("post", "/_test/manual-status/X"),
        ("post", "/_test/trigger-new-application"),
        ("post", "/_test/trigger-review/X"),
        ("post", "/_test/compose-new-application"),
        ("post", "/_test/compose-review/X"),
    ],
)
def test_service_endpoints_require_service_token(client, method, path):
    headers = {"Authorization": "Bearer wrong"}
    response = client.get(path, headers=headers) if method == "get" else client.post(path, json={}, headers=headers)
    assert response.status_code == 401


# --- их вход для наших уведомлений ----------------------------------------------------


def test_webhook_requires_our_token(client):
    response = client.post("/partner/webhook", json=_decision("APP-1"), headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_webhook_accepts_and_records_decision(client, our_headers, auth_headers):
    response = client.post("/partner/webhook", json=_decision("APP-1"), headers=our_headers)
    assert response.status_code == 200

    received = client.get("/_test/received/APP-1", headers=auth_headers)
    assert received.status_code == 200
    body = received.json()
    assert [item["type"] for item in body["items"]] == ["decision"]
    assert body["current_status"] == "approved"  # решение обновило «их» статус


def test_ocr_result_does_not_change_their_status(client, our_headers, auth_headers):
    response = client.post(
        "/partner/webhook", headers=our_headers,
        json={"type": "ocr_result", "external_id": "APP-2", "event_id": "ev-2", "fields": {}},
    )
    assert response.status_code == 200
    body = client.get("/_test/received/APP-2", headers=auth_headers).json()
    assert body["current_status"] == "new"
    assert body["items"][0]["type"] == "ocr_result"


def test_conflict_mode_answers_409_with_current_status_for_decisions_only(client, our_headers, auth_headers):
    configured = client.post(
        "/_test/configure", headers=auth_headers,
        json={"mode": "conflict", "conflict_current_status": "rejected", "conflict_reason": "оператор отказал"},
    )
    assert configured.status_code == 204

    response = client.post("/partner/webhook", json=_decision("APP-3"), headers=our_headers)
    assert response.status_code == 409
    assert response.json() == {"current_status": "rejected", "reason": "оператор отказал"}

    ocr = client.post(
        "/partner/webhook", headers=our_headers,
        json={"type": "ocr_result", "external_id": "APP-3", "event_id": "ev-3", "fields": {}},
    )
    assert ocr.status_code == 200
    items = client.get("/_test/received/APP-3", headers=auth_headers).json()["items"]
    assert [item["type"] for item in items] == ["ocr_result"]  # отклонённое решение не записано


def test_unavailable_mode_answers_503_and_records_nothing(client, our_headers, auth_headers):
    assert client.post("/_test/configure", json={"mode": "unavailable"}, headers=auth_headers).status_code == 204
    response = client.post("/partner/webhook", json=_decision("APP-4"), headers=our_headers)
    assert response.status_code == 503
    assert client.get("/_test/received/APP-4", headers=auth_headers).json()["items"] == []


def test_reset_clears_received_and_mode(client, our_headers, auth_headers):
    assert client.post("/_test/configure", json={"mode": "unavailable"}, headers=auth_headers).status_code == 204
    assert client.post("/_test/reset", headers=auth_headers).status_code == 204
    response = client.post("/partner/webhook", json=_decision("APP-5"), headers=our_headers)
    assert response.status_code == 200  # режим снова ok


def test_manual_status_changes_their_status(client, auth_headers):
    response = client.post("/_test/manual-status/APP-6", json={"status": "approved"}, headers=auth_headers)
    assert response.status_code == 204
    assert client.get("/_test/received/APP-6", headers=auth_headers).json()["current_status"] == "approved"


# --- они шлют нам ------------------------------------------------------------------


def _our_api(path: str) -> str:
    return f"{get_settings().dialog_api_base_url}{path}"


def test_trigger_new_application_sends_two_png_photos_in_base64(client, auth_headers):
    with respx.mock:
        route = respx.post(_our_api("/api/v1/partner/applications")).mock(return_value=Response(202, json={}))
        response = client.post(
            "/_test/trigger-new-application", json={"last_name": "Тестов"}, headers=auth_headers,
        )
    assert response.status_code == 200
    external_id = response.json()["external_id"]

    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {get_settings().dialog_api_token}"
    body = json.loads(sent.content)
    assert body["external_id"] == external_id
    assert body["last_name"] == "Тестов"
    assert set(body["photos"]) == {"passport_main", "passport_reg"}  # без селфи
    for photo in body["photos"].values():
        assert base64.b64decode(photo["base64"]).startswith(_PNG)


def test_trigger_new_application_url_mode_serves_photo(client, auth_headers):
    with respx.mock:
        route = respx.post(_our_api("/api/v1/partner/applications")).mock(return_value=Response(202, json={}))
        response = client.post(
            "/_test/trigger-new-application", json={"photo_mode": "url"}, headers=auth_headers,
        )
    assert response.status_code == 200
    body = json.loads(route.calls.last.request.content)
    url = body["photos"]["passport_main"]["url"]
    token = url.rsplit("/", 1)[-1]

    photo = client.get(f"/_test/photos/{token}")  # временная ссылка — без авторизации
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/png"
    assert photo.content.startswith(_PNG)
    assert client.get("/_test/photos/unknown-token").status_code == 404


def test_trigger_new_application_reports_our_rejection_as_502(client, auth_headers):
    with respx.mock:
        respx.post(_our_api("/api/v1/partner/applications")).mock(return_value=Response(422, json={"detail": "x"}))
        response = client.post("/_test/trigger-new-application", json={}, headers=auth_headers)
    assert response.status_code == 502


def _review_body(client, auth_headers, payload: dict) -> dict:
    with respx.mock:
        route = respx.post(_our_api("/api/v1/partner/review")).mock(return_value=Response(202, json={}))
        response = client.post("/_test/trigger-review/APP-7", json=payload, headers=auth_headers)
    assert response.status_code == 200
    return json.loads(route.calls.last.request.content)


def test_trigger_review_sends_deal_income_obligations_and_monthly_payment(client, auth_headers):
    body = _review_body(client, auth_headers, {})
    assert body["external_id"] == "APP-7"
    assert body["status"] == "on_review"
    # T21: платёж по рассрочке «по их графику» — без наценки (60000 − 6000) / 12.
    assert body["deal"]["monthly_payment"] == 4500.0
    assert body["income"] == {"declared_monthly": 90000.0}
    assert body["obligations"] == {"monthly_payment_total": 0.0}
    assert body["bki_consent"]["given"] is True


def test_trigger_review_monthly_payment_explicit_or_absent(client, auth_headers):
    assert _review_body(client, auth_headers, {"monthly_payment": 6500})["deal"]["monthly_payment"] == 6500
    assert "monthly_payment" not in _review_body(client, auth_headers, {"send_monthly_payment": False})["deal"]


def test_trigger_review_reports_our_rejection_as_502(client, auth_headers):
    with respx.mock:
        respx.post(_our_api("/api/v1/partner/review")).mock(return_value=Response(404, json={"detail": "x"}))
        response = client.post("/_test/trigger-review/APP-8", json={}, headers=auth_headers)
    assert response.status_code == 502


# --- T23: собрать тело без отправки (демо-сид доставляет его сам) -----------------


def test_compose_new_application_returns_body_without_sending(client, auth_headers):
    passport = {"passport_series": "0512", "passport_number": "123456", "department_code": "050-001"}
    with respx.mock:  # любой исходящий вызов здесь — ошибка: respx не замокал ни одного маршрута
        response = client.post(
            "/_test/compose-new-application",
            json={"external_id": "DEMO-1", "last_name": "Демов", "passport": passport},
            headers=auth_headers,
        )
    assert response.status_code == 200
    body = response.json()["body"]
    assert body["external_id"] == "DEMO-1"
    assert body["last_name"] == "Демов"
    assert body["event_id"]
    assert set(body["photos"]) == {"passport_main", "passport_reg"}
    for photo in body["photos"].values():
        assert base64.b64decode(photo["base64"]).startswith(_PNG)


def test_compose_review_returns_same_body_as_trigger_without_sending(client, auth_headers):
    payload = {"goods_price": 50000, "down_payment": 5000, "term_months": 9, "declared_monthly_income": 70000}
    with respx.mock:
        response = client.post("/_test/compose-review/DEMO-2", json=payload, headers=auth_headers)
    assert response.status_code == 200
    composed = response.json()["body"]
    sent = _review_body(client, auth_headers, payload)
    for key in ("deal", "employment", "income", "obligations", "guarantor"):
        assert composed[key] == sent[key]
    assert composed["external_id"] == "DEMO-2"
    assert composed["deal"]["monthly_payment"] == 5000.0


def test_review_body_carries_employer_position_and_contacts(client, auth_headers):
    """T23: рейтинг клиента — место работы, должность и контакты в анкете."""
    payload = {
        "employer": "Школа № 12", "position": "учитель",
        "contacts": [{"name": "Мама", "phone": "+79990001122", "relation": "parent"}],
    }
    with respx.mock:
        response = client.post("/_test/compose-review/DEMO-3", json=payload, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["body"]
    assert body["employment"] == {"type": "employed", "employer": "Школа № 12", "position": "учитель"}
    assert body["contacts"] == payload["contacts"]
    assert _review_body(client, auth_headers, {})["contacts"] == []
