import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.store import store


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    # Токен служебных /_test/* — из реального конфига, чтобы тесты не зависели
    # от того, как FAKE_PARTNER_TOKEN задан в окружении (compose/docker run).
    return {"Authorization": f"Bearer {get_settings().fake_partner_token}"}


@pytest.fixture
def our_headers():
    # Токен, которым dialog-scoring представляется на /partner/webhook.
    return {"Authorization": f"Bearer {get_settings().expected_inbound_token}"}
