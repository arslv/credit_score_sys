"""HTTP-транспорт Спектрум Даты (асинхронный B2B-API, Swagger
b2b-api.spectrumdata.ru): токен AR-REST, заказ → ожидание → отчёт, защита
от двойного списания, ошибки API — через провайдер в source_unavailable.
Сеть — только respx (CLAUDE.md, правило 10)."""

import base64
import hashlib

import httpx
import pytest
import respx

from app.config import Settings
from app.sources.base import CheckStatus
from app.sources.bki import NotConfiguredBkiProvider
from app.sources.factory import build_bki_provider
from app.sources.spectrum import SpectrumDataBkiProvider
from app.sources.spectrum_http import SpectrumApiError, SpectrumHttpTransport, auth_header
from tests.test_sources_spectrum import REPORT, _subject

BASE = "https://b2b-api.test"
TYPE_UID = "credit_rating@our_domain"
REPORT_UID = "report_1_123@our_domain"


class _Pending:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)


def _transport(pending=None, **kwargs) -> SpectrumHttpTransport:
    return SpectrumHttpTransport(
        base_url=BASE, user="api@our_domain", password="secret", pending=pending or _Pending(),
        poll_interval_seconds=0, **kwargs,
    )


def _ready(progress_wait=0) -> dict:
    return {"state": "ok", "size": 1, "data": [{**REPORT["data"][0], "uid": REPORT_UID, "progress_wait": progress_wait}]}


def test_auth_header_follows_dev_token_scheme():
    header = auth_header("api@our_domain", "secret", stamp=1700000000, age=300)
    assert header.startswith("AR-REST ")
    user, stamp, age, salted = base64.b64decode(header.removeprefix("AR-REST ")).decode().split(":", 3)
    assert (user, stamp, age) == ("api@our_domain", "1700000000", "300")
    b64md5 = lambda text: base64.b64encode(hashlib.md5(text.encode()).digest()).decode()  # noqa: E731
    assert salted == b64md5(f"1700000000:300:{b64md5('secret')}")
    assert "secret" not in base64.b64decode(header.removeprefix("AR-REST ")).decode()


@respx.mock
async def test_make_then_poll_until_ready():
    make = respx.post(f"{BASE}/b2b/api/v1/user/reports/{TYPE_UID}/_make").mock(
        return_value=httpx.Response(200, json={"state": "ok", "data": [{"uid": REPORT_UID, "isnew": True}]}),
    )
    get = respx.get(f"{BASE}/b2b/api/v1/user/reports/{REPORT_UID}").mock(
        side_effect=[httpx.Response(200, json=_ready(progress_wait=1)), httpx.Response(200, json=_ready())],
    )
    pending = _Pending()

    body = await _transport(pending).fetch_report(TYPE_UID, {"last_name": "Иванов"})

    assert body["data"][0]["content"] == REPORT["data"][0]["content"]
    assert make.call_count == 1 and get.call_count == 2
    sent = make.calls[0].request
    assert sent.headers["Authorization"].startswith("AR-REST ")
    assert b'"queryType": "MULTIPART"' in sent.content or b'"queryType":"MULTIPART"' in sent.content
    assert get.calls[0].request.url.params["_content"] == "true"
    assert pending.data == {}  # отчёт забран — в следующий раз закажем новый


@respx.mock
async def test_not_ready_in_time_keeps_uid_and_retry_does_not_order_again():
    make = respx.post(f"{BASE}/b2b/api/v1/user/reports/{TYPE_UID}/_make").mock(
        return_value=httpx.Response(200, json={"state": "ok", "data": [{"uid": REPORT_UID}]}),
    )
    respx.get(f"{BASE}/b2b/api/v1/user/reports/{REPORT_UID}").mock(
        side_effect=[httpx.Response(200, json=_ready(progress_wait=3)), httpx.Response(200, json=_ready())],
    )
    pending = _Pending()

    with pytest.raises(SpectrumApiError):
        await _transport(pending, poll_timeout_seconds=0).fetch_report(TYPE_UID, {"last_name": "Иванов"})
    assert list(pending.data.values()) == [REPORT_UID]

    body = await _transport(pending).fetch_report(TYPE_UID, {"last_name": "Иванов"})
    assert body["data"][0]["uid"] == REPORT_UID
    assert make.call_count == 1  # второй раз не заказывали — не списали дважды


@respx.mock
async def test_api_error_becomes_source_unavailable_without_raising():
    respx.post(f"{BASE}/b2b/api/v1/user/reports/{TYPE_UID}/_make").mock(
        return_value=httpx.Response(403, json={"code": "S110", "name": "Метка времени просрочена"}),
    )
    result = await SpectrumDataBkiProvider(_transport()).request_report(_subject(), report_code=TYPE_UID)

    assert result.status is CheckStatus.SOURCE_UNAVAILABLE
    assert "S110" in result.error_text


@respx.mock
async def test_report_types_lists_packages():
    respx.get(f"{BASE}/b2b/api/v1/user/report_types").mock(
        return_value=httpx.Response(200, json={"state": "ok", "data": [{"uid": TYPE_UID, "name": "Кредитный рейтинг"}]}),
    )
    assert await _transport().report_types() == [{"uid": TYPE_UID, "name": "Кредитный рейтинг"}]


def test_factory_builds_spectrum_only_with_credentials():
    with_creds = Settings(bki_provider_mode="spectrum", spectrum_user="api@our_domain", spectrum_password="secret")
    assert isinstance(build_bki_provider(with_creds), SpectrumDataBkiProvider)
    assert isinstance(build_bki_provider(Settings(bki_provider_mode="spectrum")), NotConfiguredBkiProvider)
