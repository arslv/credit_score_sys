"""Тесты заглушки CRM по её API (api.yml): она шлёт нам /new-order и /scoring
(/_test/trigger-*) и принимает наши passport-result и result. Вызовы нашего
API мокаются через respx (правило 10 CLAUDE.md), ответ каждого вызова
проверяется (правило 14)."""

import base64
import json

import pytest
import respx
from httpx import Response

from app.config import get_settings
from app.main import OUR_NEW_ORDER_PATH, OUR_SCORING_PATH, PASSPORT_RESULT_PATH, RESULT_PATH

_PNG = b"\x89PNG\r\n\x1a\n"


def _result(order_id: int, status: str = "approved") -> dict:
    return {"orderId": order_id, "status": status, "score": 720, "reason": "Одобрено"}


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


@pytest.mark.parametrize("path", [RESULT_PATH, PASSPORT_RESULT_PATH])
def test_inbound_requires_our_token(client, path):
    response = client.post(path, json=_result(1), headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_result_is_recorded_and_changes_their_status(client, our_headers, auth_headers):
    response = client.post(RESULT_PATH, json=_result(101), headers=our_headers)
    assert response.status_code == 200

    received = client.get("/_test/received/101", headers=auth_headers)
    assert received.status_code == 200
    body = received.json()
    assert [item["type"] for item in body["items"]] == ["decision"]
    assert body["items"][0]["orderId"] == 101
    assert body["current_status"] == "approved"


def test_passport_result_does_not_change_their_status(client, our_headers, auth_headers):
    response = client.post(
        PASSPORT_RESULT_PATH, headers=our_headers, json={"orderId": 102, "lastName": "Тестов", "passportSeries": "0000"},
    )
    assert response.status_code == 200
    body = client.get("/_test/received/102", headers=auth_headers).json()
    assert body["current_status"] == "new"
    assert body["items"][0]["type"] == "ocr_result"
    assert body["items"][0]["lastName"] == "Тестов"


def test_body_without_order_id_is_rejected(client, our_headers):
    response = client.post(RESULT_PATH, json={"status": "approved"}, headers=our_headers)
    assert response.status_code == 422


def test_conflict_mode_answers_409_for_results_only(client, our_headers, auth_headers):
    configured = client.post(
        "/_test/configure", headers=auth_headers,
        json={"mode": "conflict", "conflict_current_status": "rejected", "conflict_reason": "оператор отказал"},
    )
    assert configured.status_code == 204

    response = client.post(RESULT_PATH, json=_result(103), headers=our_headers)
    assert response.status_code == 409
    assert response.json() == {"current_status": "rejected", "reason": "оператор отказал"}

    ocr = client.post(PASSPORT_RESULT_PATH, headers=our_headers, json={"orderId": 103})
    assert ocr.status_code == 200
    items = client.get("/_test/received/103", headers=auth_headers).json()["items"]
    assert [item["type"] for item in items] == ["ocr_result"]  # отклонённое решение не записано


def test_unavailable_mode_answers_503_and_records_nothing(client, our_headers, auth_headers):
    assert client.post("/_test/configure", json={"mode": "unavailable"}, headers=auth_headers).status_code == 204
    response = client.post(RESULT_PATH, json=_result(104), headers=our_headers)
    assert response.status_code == 503
    assert client.get("/_test/received/104", headers=auth_headers).json()["items"] == []


def test_reset_clears_received_and_mode(client, our_headers, auth_headers):
    assert client.post("/_test/configure", json={"mode": "unavailable"}, headers=auth_headers).status_code == 204
    assert client.post("/_test/reset", headers=auth_headers).status_code == 204
    response = client.post(RESULT_PATH, json=_result(105), headers=our_headers)
    assert response.status_code == 200  # режим снова ok


def test_manual_status_changes_their_status(client, auth_headers):
    response = client.post("/_test/manual-status/106", json={"status": "approved"}, headers=auth_headers)
    assert response.status_code == 204
    assert client.get("/_test/received/106", headers=auth_headers).json()["current_status"] == "approved"


# --- они шлют нам ------------------------------------------------------------------


def _our_api(path: str) -> str:
    return f"{get_settings().dialog_api_base_url}{path}"


def test_trigger_new_application_sends_new_order_with_two_png_photos(client, auth_headers):
    with respx.mock:
        route = respx.post(_our_api(OUR_NEW_ORDER_PATH)).mock(return_value=Response(200, json={}))
        response = client.post("/_test/trigger-new-application", json={"last_name": "Тестов"}, headers=auth_headers)
    assert response.status_code == 200
    external_id = response.json()["external_id"]

    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {get_settings().dialog_api_token}"
    body = json.loads(sent.content)
    assert str(body["orderId"]) == external_id
    assert isinstance(body["orderId"], int)
    assert body["lastName"] == "Тестов"
    assert body["fatherName"] == "Тестович"
    assert len(body["passportPhotos"]) == 2  # разворот и прописка, без селфи
    for photo in body["passportPhotos"]:
        assert base64.b64decode(photo).startswith(_PNG)


def test_trigger_new_application_url_mode_serves_photo(client, auth_headers):
    with respx.mock:
        route = respx.post(_our_api(OUR_NEW_ORDER_PATH)).mock(return_value=Response(200, json={}))
        response = client.post("/_test/trigger-new-application", json={"photo_mode": "url"}, headers=auth_headers)
    assert response.status_code == 200
    url = json.loads(route.calls.last.request.content)["passportPhotos"][0]
    token = url.rsplit("/", 1)[-1]

    photo = client.get(f"/_test/photos/{token}")  # временная ссылка — без авторизации
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/png"
    assert photo.content.startswith(_PNG)
    assert client.get("/_test/photos/unknown-token").status_code == 404


def test_trigger_new_application_reports_our_rejection_as_502(client, auth_headers):
    with respx.mock:
        respx.post(_our_api(OUR_NEW_ORDER_PATH)).mock(return_value=Response(422, json={"detail": "x"}))
        response = client.post("/_test/trigger-new-application", json={}, headers=auth_headers)
    assert response.status_code == 502


def _scoring_body(client, auth_headers, payload: dict, external_id: str = "107") -> dict:
    with respx.mock:
        route = respx.post(_our_api(OUR_SCORING_PATH)).mock(return_value=Response(200, json={}))
        response = client.post(f"/_test/trigger-review/{external_id}", json=payload, headers=auth_headers)
    assert response.status_code == 200
    return json.loads(route.calls.last.request.content)


def test_trigger_review_sends_scoring_request(client, auth_headers):
    body = _scoring_body(client, auth_headers, {})
    assert body["orderId"] == 107
    assert body["productCategory"] == "household_appliances"
    assert body["price"] == 60000.0
    assert body["firstPayment"] == 6000.0
    assert body["period"] == 12
    assert body["clientIncome"] == 90000.0
    assert body["otherCreditPayments"] == 0.0
    assert body["bkiConsent"] is True
    assert body["guarantors"] == []
    # Наше расширение: платёж «по их графику» без наценки (60000 − 6000) / 12.
    assert body["monthlyPayment"] == 4500.0


def test_trigger_review_strict_format_has_no_extensions(client, auth_headers):
    body = _scoring_body(client, auth_headers, {"send_extensions": False})
    assert set(body) == {
        "orderId", "productCategory", "price", "firstPayment", "period", "clientIncome",
        "otherCreditPayments", "bkiConsent", "guarantors",
    }


def test_trigger_review_monthly_payment_explicit_or_absent(client, auth_headers):
    assert _scoring_body(client, auth_headers, {"monthly_payment": 6500})["monthlyPayment"] == 6500
    assert "monthlyPayment" not in _scoring_body(client, auth_headers, {"send_monthly_payment": False})


def test_trigger_review_reports_our_rejection_as_502(client, auth_headers):
    with respx.mock:
        respx.post(_our_api(OUR_SCORING_PATH)).mock(return_value=Response(404, json={"detail": "x"}))
        response = client.post("/_test/trigger-review/108", json={}, headers=auth_headers)
    assert response.status_code == 502


# --- собрать тело без отправки (демо-сид доставляет его сам) ----------------------


def test_compose_new_application_returns_body_without_sending(client, auth_headers):
    passport = {"passport_series": "0512", "passport_number": "123456", "department_code": "050-001"}
    with respx.mock:  # любой исходящий вызов здесь — ошибка: respx не замокал ни одного маршрута
        response = client.post(
            "/_test/compose-new-application",
            json={"order_id": 555001, "last_name": "Демов", "passport": passport},
            headers=auth_headers,
        )
    assert response.status_code == 200
    body = response.json()["body"]
    assert body["orderId"] == 555001
    assert response.json()["external_id"] == "555001"
    assert body["lastName"] == "Демов"
    assert len(body["passportPhotos"]) == 2


def test_compose_review_returns_same_body_as_trigger_without_sending(client, auth_headers):
    payload = {"goods_price": 50000, "down_payment": 5000, "term_months": 9, "declared_monthly_income": 70000}
    with respx.mock:
        response = client.post("/_test/compose-review/555002", json=payload, headers=auth_headers)
    assert response.status_code == 200
    composed = response.json()["body"]
    sent = _scoring_body(client, auth_headers, payload, external_id="555002")
    assert composed == sent
    assert composed["monthlyPayment"] == 5000.0


def test_review_body_carries_employment_and_contacts_extensions(client, auth_headers):
    payload = {
        "employer": "Школа № 12", "position": "учитель",
        "contacts": [{"name": "Мама", "phone": "+79990001122", "relation": "parent"}],
    }
    with respx.mock:
        response = client.post("/_test/compose-review/555003", json=payload, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["body"]
    assert body["employment"] == {"type": "employed", "employer": "Школа № 12", "position": "учитель"}
    assert body["contacts"] == payload["contacts"]
