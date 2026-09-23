import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.store import store


@pytest.fixture(autouse=True)
def _reset_store():
    store._applications.clear()
    store._counter = 0
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    # Токен читаем из реального конфига, чтобы тесты работали независимо от того,
    # как FAKE_PARTNER_TOKEN задан в окружении (docker run -e ... или compose).
    return {"Authorization": f"Bearer {get_settings().fake_partner_token}"}
