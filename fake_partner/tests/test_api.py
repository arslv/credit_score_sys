def test_health_requires_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_list_applications_requires_auth(client):
    response = client.get("/applications")
    assert response.status_code == 401


def test_seed_creates_ten_new_applications(client, auth_headers):
    response = client.get("/applications", params={"status_": "new"}, headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()) == 10


def test_get_application_by_id(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.get(f"/applications/{app_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == app_id
    assert set(body["photos"]) == {"selfie_passport", "passport_main", "passport_reg"}


def test_get_application_not_found(client, auth_headers):
    response = client.get("/applications/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_download_photo(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.get(f"/applications/{app_id}/photos/passport_main", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_download_photo_unknown_kind(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.get(f"/applications/{app_id}/photos/unknown", headers=auth_headers)
    assert response.status_code == 404


def test_change_status_to_unknown_value_returns_400(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/applications/{app_id}/status", json={"status": "not_a_real_status"}, headers=auth_headers
    )
    assert response.status_code == 400


def test_change_status_valid_transition(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/applications/{app_id}/status",
        json={"status": "on_call", "comment": "нужна проверка"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "on_call"


def test_change_status_disallowed_transition_returns_409(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/applications/{app_id}/status", json={"status": "approved"}, headers=auth_headers
    )
    assert response.status_code == 409


def test_add_comment(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/applications/{app_id}/comments", json={"text": "проверено вручную"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["comments"][-1]["text"] == "проверено вручную"


def test_create_application_test_endpoint(client, auth_headers):
    response = client.post(
        "/_test/applications",
        json={"last_name": "Уникальный", "first_name": "Клиент", "phone": "+79991112233"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["last_name"] == "Уникальный"
    assert response.json()["status"] == "new"


def test_press_review_button(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    client.post(f"/applications/{app_id}/status", json={"status": "on_call"}, headers=auth_headers)

    response = client.post(f"/_test/applications/{app_id}/review", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "on_review"


def test_press_review_button_without_call_first_returns_409(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(f"/_test/applications/{app_id}/review", headers=auth_headers)
    assert response.status_code == 409


def test_fill_stage_b_fields(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/_test/applications/{app_id}/stage-b-fields",
        json={"deal": {"term_months": 12}, "income": {"monthly_income": 50000}},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["stage_b"]["deal"]["term_months"] == 12


def test_manual_status_change_bypasses_transition_rules(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/_test/applications/{app_id}/manual-status",
        json={"status": "approved"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["status_history"][-1]["changed_by"] == "operator"


def test_manual_status_change_still_validates_enum(client, auth_headers):
    listed = client.get("/applications", headers=auth_headers).json()
    app_id = listed[0]["id"]

    response = client.post(
        f"/_test/applications/{app_id}/manual-status",
        json={"status": "not_a_real_status"},
        headers=auth_headers,
    )
    assert response.status_code == 400
